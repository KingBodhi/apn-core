"""
APN Core Database - Async SQLite persistence layer

Stores peer data, contributions, and reward tracking locally.
Schema matches pcg-cc-mcp migration (20260206000000_peer_rewards_system.sql).
Uses text UUIDs (not BLOBs) since this is a local-only database.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import aiosqlite

from core.logging_config import get_logger

logger = get_logger("database")

# VIBE unit conversion (1 VIBE = 100,000,000 units)
VIBE_UNITS = 100_000_000


def vibe_to_display(amount: int) -> float:
    """Convert internal VIBE units to display format"""
    return amount / VIBE_UNITS


def display_to_vibe(amount: float) -> int:
    """Convert display VIBE to internal units"""
    return int(amount * VIBE_UNITS)


def _new_id() -> str:
    """Generate a new text UUID"""
    return uuid.uuid4().hex


_SCHEMA = """
-- peer_nodes: Track all nodes in the network
CREATE TABLE IF NOT EXISTS peer_nodes (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL UNIQUE,
    wallet_address TEXT NOT NULL,
    capabilities TEXT,
    cpu_cores INTEGER,
    ram_mb INTEGER,
    storage_gb INTEGER,
    gpu_available BOOLEAN DEFAULT 0,
    gpu_model TEXT,
    hostname TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_heartbeat_at TEXT,
    is_active BOOLEAN DEFAULT 1,
    is_banned BOOLEAN DEFAULT 0,
    ban_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_peer_nodes_node_id ON peer_nodes(node_id);
CREATE INDEX IF NOT EXISTS idx_peer_nodes_wallet ON peer_nodes(wallet_address);
CREATE INDEX IF NOT EXISTS idx_peer_nodes_active ON peer_nodes(is_active, is_banned);
CREATE INDEX IF NOT EXISTS idx_peer_nodes_last_heartbeat ON peer_nodes(last_heartbeat_at);

-- peer_contributions: Track resource contributions over time
CREATE TABLE IF NOT EXISTS peer_contributions (
    id TEXT PRIMARY KEY,
    peer_node_id TEXT NOT NULL REFERENCES peer_nodes(id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    uptime_seconds INTEGER NOT NULL DEFAULT 0,
    cpu_units INTEGER NOT NULL DEFAULT 0,
    gpu_units INTEGER NOT NULL DEFAULT 0,
    bandwidth_bytes INTEGER NOT NULL DEFAULT 0,
    storage_bytes INTEGER NOT NULL DEFAULT 0,
    relay_messages INTEGER NOT NULL DEFAULT 0,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_failed INTEGER NOT NULL DEFAULT 0,
    heartbeat_count INTEGER NOT NULL DEFAULT 0,
    contribution_score INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_peer_contrib_node ON peer_contributions(peer_node_id);
CREATE INDEX IF NOT EXISTS idx_peer_contrib_period ON peer_contributions(period_start, period_end);

-- peer_rewards: Individual reward transactions
CREATE TABLE IF NOT EXISTS peer_rewards (
    id TEXT PRIMARY KEY,
    peer_node_id TEXT NOT NULL REFERENCES peer_nodes(id),
    contribution_id TEXT REFERENCES peer_contributions(id),
    reward_type TEXT NOT NULL CHECK(reward_type IN (
        'heartbeat', 'task', 'resource', 'mining', 'bonus'
    )),
    base_amount INTEGER NOT NULL,
    multiplier REAL NOT NULL DEFAULT 1.0,
    final_amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'batched', 'distributed', 'confirmed', 'failed'
    )),
    batch_id TEXT REFERENCES reward_batches(id),
    aptos_tx_hash TEXT,
    block_height INTEGER,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    description TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    distributed_at TEXT,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_peer_rewards_node ON peer_rewards(peer_node_id);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_status ON peer_rewards(status);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_type ON peer_rewards(reward_type);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_batch ON peer_rewards(batch_id);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_created ON peer_rewards(created_at);

-- reward_batches: Batch multiple rewards into single blockchain transactions
CREATE TABLE IF NOT EXISTS reward_batches (
    id TEXT PRIMARY KEY,
    batch_number INTEGER NOT NULL,
    total_rewards INTEGER NOT NULL,
    total_amount INTEGER NOT NULL,
    from_wallet TEXT NOT NULL,
    aptos_tx_hash TEXT,
    block_height INTEGER,
    gas_used INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'submitted', 'confirmed', 'failed'
    )),
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at TEXT,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_reward_batches_status ON reward_batches(status);

-- peer_wallet_balances: Cache of peer wallet balances
CREATE TABLE IF NOT EXISTS peer_wallet_balances (
    peer_node_id TEXT PRIMARY KEY REFERENCES peer_nodes(id),
    pending_rewards INTEGER NOT NULL DEFAULT 0,
    distributed_rewards INTEGER NOT NULL DEFAULT 0,
    confirmed_rewards INTEGER NOT NULL DEFAULT 0,
    onchain_balance INTEGER,
    onchain_last_checked TEXT,
    total_earned_lifetime INTEGER NOT NULL DEFAULT 0,
    total_withdrawn INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- reward_distribution_log: Audit trail for all distributions
CREATE TABLE IF NOT EXISTS reward_distribution_log (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'batch_created', 'batch_submitted', 'batch_confirmed',
        'batch_failed', 'reward_calculated', 'reward_distributed',
        'balance_updated', 'error'
    )),
    peer_node_id TEXT REFERENCES peer_nodes(id),
    reward_id TEXT REFERENCES peer_rewards(id),
    batch_id TEXT REFERENCES reward_batches(id),
    amount INTEGER,
    description TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reward_log_event ON reward_distribution_log(event_type);
CREATE INDEX IF NOT EXISTS idx_reward_log_peer ON reward_distribution_log(peer_node_id);
CREATE INDEX IF NOT EXISTS idx_reward_log_created ON reward_distribution_log(created_at);
"""


class APNDatabase:
    """Async SQLite database for APN Core persistence."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """Open database and run migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info(f"Database initialized at {self.db_path}")

    async def close(self):
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database closed")

    # ------------------------------------------------------------------
    # Peer operations
    # ------------------------------------------------------------------

    async def upsert_peer(
        self,
        node_id: str,
        wallet_address: str,
        capabilities: Optional[str] = None,
        cpu_cores: Optional[int] = None,
        ram_mb: Optional[int] = None,
        storage_gb: Optional[int] = None,
        gpu_available: bool = False,
        gpu_model: Optional[str] = None,
        hostname: Optional[str] = None,
    ) -> str:
        """Insert or update a peer node. Returns the internal row id."""
        now = datetime.now(timezone.utc).isoformat()

        # Check if peer exists
        async with self._db.execute(
            "SELECT id FROM peer_nodes WHERE node_id = ?", (node_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            peer_id = row["id"]
            await self._db.execute(
                """UPDATE peer_nodes SET
                    wallet_address = ?, capabilities = ?,
                    cpu_cores = ?, ram_mb = ?, storage_gb = ?,
                    gpu_available = ?, gpu_model = ?, hostname = ?,
                    last_heartbeat_at = ?, is_active = 1, updated_at = ?
                WHERE id = ?""",
                (
                    wallet_address, capabilities,
                    cpu_cores, ram_mb, storage_gb,
                    gpu_available, gpu_model, hostname,
                    now, now, peer_id,
                ),
            )
        else:
            peer_id = _new_id()
            await self._db.execute(
                """INSERT INTO peer_nodes
                    (id, node_id, wallet_address, capabilities,
                     cpu_cores, ram_mb, storage_gb, gpu_available, gpu_model,
                     hostname, last_heartbeat_at, first_seen_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    peer_id, node_id, wallet_address, capabilities,
                    cpu_cores, ram_mb, storage_gb, gpu_available, gpu_model,
                    hostname, now, now, now, now,
                ),
            )
            # Initialize wallet balance row
            await self._db.execute(
                "INSERT OR IGNORE INTO peer_wallet_balances (peer_node_id) VALUES (?)",
                (peer_id,),
            )

        await self._db.commit()
        return peer_id

    async def get_peer_id(self, node_id: str) -> Optional[str]:
        """Get internal id for a node_id."""
        async with self._db.execute(
            "SELECT id FROM peer_nodes WHERE node_id = ?", (node_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["id"] if row else None

    async def list_active_peers(self) -> List[Dict[str, Any]]:
        """List all active, non-banned peers."""
        async with self._db.execute(
            """SELECT p.*, b.pending_rewards, b.confirmed_rewards, b.total_earned_lifetime
            FROM peer_nodes p
            LEFT JOIN peer_wallet_balances b ON b.peer_node_id = p.id
            WHERE p.is_active = 1 AND p.is_banned = 0
            ORDER BY p.last_heartbeat_at DESC"""
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def mark_stale_inactive(self, stale_minutes: int = 5):
        """Mark peers that haven't sent a heartbeat as inactive."""
        cutoff = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """UPDATE peer_nodes SET is_active = 0, updated_at = ?
            WHERE is_active = 1
            AND last_heartbeat_at IS NOT NULL
            AND julianday(?) - julianday(last_heartbeat_at) > ?""",
            (cutoff, cutoff, stale_minutes / (24 * 60)),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Reward operations
    # ------------------------------------------------------------------

    async def create_reward(
        self,
        peer_node_id: str,
        reward_type: str,
        base_amount: int,
        multiplier: float = 1.0,
        description: Optional[str] = None,
        contribution_id: Optional[str] = None,
    ) -> str:
        """Create a new pending reward record. Returns reward id."""
        reward_id = _new_id()
        final_amount = int(base_amount * multiplier)
        now = datetime.now(timezone.utc).isoformat()

        await self._db.execute(
            """INSERT INTO peer_rewards
                (id, peer_node_id, contribution_id, reward_type,
                 base_amount, multiplier, final_amount, status,
                 description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                reward_id, peer_node_id, contribution_id, reward_type,
                base_amount, multiplier, final_amount,
                description, now, now,
            ),
        )

        # Update wallet balance
        await self._db.execute(
            """UPDATE peer_wallet_balances
            SET pending_rewards = pending_rewards + ?,
                total_earned_lifetime = total_earned_lifetime + ?,
                updated_at = ?
            WHERE peer_node_id = ?""",
            (final_amount, final_amount, now, peer_node_id),
        )

        # Audit log
        await self._db.execute(
            """INSERT INTO reward_distribution_log
                (id, event_type, peer_node_id, reward_id, amount, description, created_at)
            VALUES (?, 'reward_calculated', ?, ?, ?, ?, ?)""",
            (_new_id(), peer_node_id, reward_id, final_amount, description, now),
        )

        await self._db.commit()
        return reward_id

    async def get_reward_summary(self, peer_node_id: str) -> Dict[str, Any]:
        """Get reward summary for a peer."""
        async with self._db.execute(
            "SELECT * FROM peer_wallet_balances WHERE peer_node_id = ?",
            (peer_node_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return {
                "pending_rewards": 0,
                "distributed_rewards": 0,
                "confirmed_rewards": 0,
                "total_earned_lifetime": 0,
            }

        return {
            "pending_rewards": row["pending_rewards"],
            "distributed_rewards": row["distributed_rewards"],
            "confirmed_rewards": row["confirmed_rewards"],
            "total_earned_lifetime": row["total_earned_lifetime"],
        }

    async def get_reward_history(
        self, peer_node_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get reward transaction history for a peer."""
        async with self._db.execute(
            """SELECT id, reward_type, base_amount, multiplier, final_amount,
                      status, description, created_at
            FROM peer_rewards
            WHERE peer_node_id = ?
            ORDER BY created_at DESC
            LIMIT ?""",
            (peer_node_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Contribution operations
    # ------------------------------------------------------------------

    async def create_contribution(
        self,
        peer_node_id: str,
        period_start: str,
        period_end: str,
        uptime_seconds: int = 0,
        cpu_units: int = 0,
        gpu_units: int = 0,
        bandwidth_bytes: int = 0,
        storage_bytes: int = 0,
        relay_messages: int = 0,
        tasks_completed: int = 0,
        tasks_failed: int = 0,
        heartbeat_count: int = 0,
        contribution_score: int = 0,
    ) -> str:
        """Create a contribution record. Returns contribution id."""
        contrib_id = _new_id()
        await self._db.execute(
            """INSERT INTO peer_contributions
                (id, peer_node_id, period_start, period_end,
                 uptime_seconds, cpu_units, gpu_units, bandwidth_bytes,
                 storage_bytes, relay_messages, tasks_completed, tasks_failed,
                 heartbeat_count, contribution_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                contrib_id, peer_node_id, period_start, period_end,
                uptime_seconds, cpu_units, gpu_units, bandwidth_bytes,
                storage_bytes, relay_messages, tasks_completed, tasks_failed,
                heartbeat_count, contribution_score,
            ),
        )
        await self._db.commit()
        return contrib_id

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_network_totals(self) -> Dict[str, Any]:
        """Get aggregate resource totals across active peers."""
        async with self._db.execute(
            """SELECT
                COUNT(*) as peer_count,
                COALESCE(SUM(cpu_cores), 0) as total_cpu_cores,
                COALESCE(SUM(ram_mb), 0) as total_ram_mb,
                COALESCE(SUM(storage_gb), 0) as total_storage_gb,
                SUM(CASE WHEN gpu_available THEN 1 ELSE 0 END) as gpu_node_count
            FROM peer_nodes
            WHERE is_active = 1 AND is_banned = 0"""
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row)


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------

_database: Optional[APNDatabase] = None


async def get_database() -> Optional[APNDatabase]:
    """Get the global database instance."""
    return _database


async def init_database(db_path: Optional[Path] = None) -> APNDatabase:
    """Initialize and return the global database."""
    global _database
    if _database is not None:
        return _database

    if db_path is None:
        db_path = Path.home() / ".apn" / "apn.db"

    _database = APNDatabase(db_path)
    await _database.initialize()
    return _database


async def close_database():
    """Close the global database."""
    global _database
    if _database:
        await _database.close()
        _database = None
