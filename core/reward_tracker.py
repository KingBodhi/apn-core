"""
APN Core Local Reward Tracker - Estimates VIBE rewards locally

Ported from pcg-cc-mcp economics.rs RewardRates and calculate_rewards.
Fed by peer listener dispatch (shared NATS connection).
Creates reward records in the local SQLite database.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from core.database import (
    get_database, display_to_vibe, vibe_to_display, APNDatabase,
)
from core.logging_config import get_logger

logger = get_logger("reward_tracker")


class RewardRates:
    """Reward rate constants ported exactly from economics.rs defaults."""
    CPU_RATE = display_to_vibe(0.001)       # 0.001 VIBE per 1M CPU units
    GPU_RATE = display_to_vibe(0.005)       # 0.005 VIBE per 1M GPU units
    BANDWIDTH_RATE = display_to_vibe(0.01)  # 0.01 VIBE per GB
    STORAGE_RATE = display_to_vibe(0.001)   # 0.001 VIBE per GB/hour
    RELAY_RATE = display_to_vibe(0.0001)    # 0.0001 VIBE per relay
    UPTIME_RATE = display_to_vibe(0.1)      # 0.1 VIBE per hour
    TASK_RATE = display_to_vibe(1.0)        # 1.0 VIBE per task
    HEARTBEAT_BASE = display_to_vibe(0.1)   # 0.1 VIBE per heartbeat
    GPU_MULTIPLIER = 2.0                    # 2x for GPU nodes
    HIGH_CPU_MULTIPLIER = 1.5              # 1.5x for >16 cores
    HIGH_RAM_MULTIPLIER = 1.3              # 1.3x for >32GB RAM


class LocalRewardTracker:
    """Tracks heartbeats and calculates estimated VIBE rewards locally.

    Fed by peer listener on_heartbeat dispatch. Does NOT open its own
    NATS connection.
    """

    def __init__(self, own_node_id: str, own_wallet_address: str):
        self.own_node_id = own_node_id
        self.own_wallet_address = own_wallet_address
        self._running = False
        self._process_task: Optional[asyncio.Task] = None
        # In-memory heartbeat accumulator: node_id -> count since last flush
        self._pending_heartbeats: Dict[str, int] = {}
        # node_id -> latest resources dict (for multiplier calc)
        self._peer_resources: Dict[str, Dict[str, Any]] = {}

    async def start(self):
        """Start the periodic reward processor."""
        if self._running:
            return
        self._running = True
        self._process_task = asyncio.create_task(self._process_loop())
        logger.info("Local reward tracker started")

    async def stop(self):
        """Stop the reward tracker."""
        self._running = False
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None
        logger.info("Local reward tracker stopped")

    async def on_heartbeat(self, data: Dict[str, Any]):
        """Called by peer listener for every heartbeat (including own node).

        Upserts the peer in the DB and accumulates a heartbeat count
        for the periodic reward processor.
        """
        node_id = data.get("node_id", "")
        if not node_id:
            return

        wallet = data.get("wallet_address", "")
        resources = data.get("resources") or {}
        capabilities = data.get("capabilities", [])
        hostname = data.get("hostname") or data.get("device_name", "")

        # Upsert peer in database
        db = await get_database()
        if db:
            try:
                caps_json = json.dumps(capabilities) if capabilities else None
                await db.upsert_peer(
                    node_id=node_id,
                    wallet_address=wallet,
                    capabilities=caps_json,
                    cpu_cores=resources.get("cpu_cores"),
                    ram_mb=resources.get("ram_mb"),
                    storage_gb=resources.get("storage_gb"),
                    gpu_available=resources.get("gpu_available", False),
                    gpu_model=resources.get("gpu_model"),
                    hostname=hostname,
                )
            except Exception as e:
                logger.debug(f"Failed to upsert peer {node_id}: {e}")

        # Accumulate heartbeat count
        self._pending_heartbeats[node_id] = (
            self._pending_heartbeats.get(node_id, 0) + 1
        )
        self._peer_resources[node_id] = resources

    def _calculate_multiplier(self, resources: Dict[str, Any]) -> float:
        """Calculate reward multiplier from node resources.

        Exact port of Rust formula: GPU 2x, high CPU (>16) 1.5x, high RAM (>32GB) 1.3x.
        Multipliers stack multiplicatively.
        """
        mult = 1.0
        if resources.get("gpu_available"):
            mult *= RewardRates.GPU_MULTIPLIER
        cpu_cores = resources.get("cpu_cores") or 0
        if cpu_cores > 16:
            mult *= RewardRates.HIGH_CPU_MULTIPLIER
        ram_mb = resources.get("ram_mb") or 0
        if ram_mb > 32 * 1024:
            mult *= RewardRates.HIGH_RAM_MULTIPLIER
        return mult

    async def _process_loop(self):
        """Process pending rewards every 60 seconds."""
        while self._running:
            try:
                await asyncio.sleep(60)
                await self._process_pending_rewards()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reward processing error: {e}")

    async def _process_pending_rewards(self):
        """Flush accumulated heartbeats into reward records."""
        if not self._pending_heartbeats:
            return

        db = await get_database()
        if not db:
            return

        # Swap out pending data
        heartbeats = self._pending_heartbeats
        self._pending_heartbeats = {}

        total_rewards_created = 0

        for node_id, count in heartbeats.items():
            try:
                peer_id = await db.get_peer_id(node_id)
                if not peer_id:
                    continue

                resources = self._peer_resources.get(node_id, {})
                multiplier = self._calculate_multiplier(resources)

                # Base reward: HEARTBEAT_BASE per heartbeat
                base_amount = RewardRates.HEARTBEAT_BASE * count

                await db.create_reward(
                    peer_node_id=peer_id,
                    reward_type="heartbeat",
                    base_amount=base_amount,
                    multiplier=multiplier,
                    description=f"{count} heartbeats @ {vibe_to_display(RewardRates.HEARTBEAT_BASE)} VIBE each",
                )
                total_rewards_created += 1
            except Exception as e:
                logger.debug(f"Failed to create reward for {node_id}: {e}")

        if total_rewards_created > 0:
            logger.info(
                f"Processed rewards for {total_rewards_created} peers "
                f"({sum(heartbeats.values())} heartbeats total)"
            )


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------

_reward_tracker: Optional[LocalRewardTracker] = None


async def start_reward_tracker(
    own_node_id: str, own_wallet_address: str
) -> LocalRewardTracker:
    """Start the global reward tracker."""
    global _reward_tracker
    if _reward_tracker is not None:
        return _reward_tracker

    _reward_tracker = LocalRewardTracker(own_node_id, own_wallet_address)
    await _reward_tracker.start()
    return _reward_tracker


def get_reward_tracker() -> Optional[LocalRewardTracker]:
    """Get the global reward tracker."""
    return _reward_tracker


async def stop_reward_tracker():
    """Stop the global reward tracker."""
    global _reward_tracker
    if _reward_tracker:
        await _reward_tracker.stop()
        _reward_tracker = None
