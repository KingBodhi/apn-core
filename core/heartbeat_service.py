"""
APN Core Heartbeat Service - Sends periodic heartbeats to earn VIBE rewards

Connects to NATS relay and sends heartbeats every 30 seconds to prove node is online.
Integrates with Pythia master node reward tracker.
"""

import asyncio
import json
import platform
from datetime import datetime, timezone
from typing import Optional, Dict, Any

try:
    from nats.aio.client import Client as NATS
    NATS_AVAILABLE = True
except ImportError:
    NATS_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from core.logging_config import get_logger

logger = get_logger("heartbeat")


class HeartbeatService:
    """Manages periodic heartbeats to the APN network.

    Heartbeats include capability advertisement so other nodes
    and Pythia can discover what this node can execute.
    """

    def __init__(self, nats_url: str, node_id: str, wallet_address: str, capabilities: list):
        self.nats_url = nats_url
        self.node_id = node_id
        self.wallet_address = wallet_address
        self.capabilities = capabilities
        self.nats = None
        self.running = False
        self.heartbeat_task = None
        self._agents: list = []
        self._software: dict = {}

    async def start(self):
        """Start the heartbeat service"""
        if not NATS_AVAILABLE:
            logger.error("nats-py not available - install with: pip install nats-py")
            return

        if self.running:
            logger.warning("Heartbeat service already running")
            return

        logger.info(f"Starting heartbeat service for node {self.node_id}")
        logger.info(f"Connecting to NATS: {self.nats_url}")

        try:
            # Connect to NATS
            self.nats = NATS()
            await self.nats.connect(self.nats_url)
            logger.info("✅ Connected to NATS relay")

            self.running = True

            # Send initial discovery announcement
            await self.send_discovery()

            # Start heartbeat loop
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("💓 Heartbeat service started (30s interval)")

        except Exception as e:
            logger.error(f"Failed to start heartbeat service: {e}")
            raise

    async def stop(self):
        """Stop the heartbeat service"""
        logger.info("Stopping heartbeat service...")
        self.running = False

        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass

        if self.nats:
            await self.nats.close()
            self.nats = None

        logger.info("Heartbeat service stopped")

    def update_capabilities(self, agents: list = None, software: dict = None):
        """Update capability advertisement for heartbeats"""
        if agents is not None:
            self._agents = agents
        if software is not None:
            self._software = software

    async def send_discovery(self):
        """Send initial discovery announcement with full capability info"""
        if not self.nats:
            return

        resources = self._collect_resources()
        self._load_capabilities()

        announcement = {
            "node_id": self.node_id,
            "wallet_address": self.wallet_address,
            "hostname": self._get_hostname(),
            "capabilities": self.capabilities,
            "agents": self._agents,
            "software": self._software,
            "resources": resources,
            "version": "3.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = json.dumps(announcement).encode()
        await self.nats.publish("apn.discovery", payload)
        logger.info("Sent discovery announcement to apn.discovery")

    async def send_heartbeat(self):
        """Send a single heartbeat with capabilities"""
        if not self.nats:
            return

        resources = self._collect_resources()

        heartbeat = {
            "node_id": self.node_id,
            "wallet_address": self.wallet_address,
            "hostname": self._get_hostname(),
            "device_name": self._get_hostname(),
            "capabilities": self.capabilities,
            "agents": self._agents,
            "software": self._software,
            "resources": resources,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = json.dumps(heartbeat).encode()
        await self.nats.publish("apn.heartbeat", payload)
        logger.debug("Sent heartbeat to apn.heartbeat")

        # Record heartbeat in resource accounting
        try:
            from core.resource_accounting import get_resource_accounting
            accountant = get_resource_accounting()
            if accountant:
                accountant.record_heartbeat()
        except Exception:
            pass

    async def _heartbeat_loop(self):
        """Main heartbeat loop - sends heartbeat every 30 seconds"""
        while self.running:
            try:
                await self.send_heartbeat()
                await asyncio.sleep(30)  # 30 second interval
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                await asyncio.sleep(30)  # Continue even on error

    def _get_hostname(self) -> str:
        """Get friendly hostname from config, falling back to system hostname"""
        try:
            from pathlib import Path
            config_file = Path.home() / ".apn" / "apn_config.json"
            if config_file.exists():
                import json as _json
                with open(config_file, 'r') as f:
                    config = _json.load(f)
                name = config.get("device_name", "").strip()
                if name:
                    return name
        except Exception:
            pass
        return platform.node()

    def _load_capabilities(self):
        """Load capabilities from ~/.apn/capabilities.json if not already set"""
        if self._agents:
            return
        try:
            from pathlib import Path
            caps_file = Path.home() / ".apn" / "capabilities.json"
            if caps_file.exists():
                with open(caps_file, 'r') as f:
                    caps = json.load(f)
                self._agents = caps.get("agents", [])
                self._software = caps.get("software", {})
        except Exception:
            pass

    def _collect_resources(self) -> Optional[Dict[str, Any]]:
        """Collect system resources for heartbeat"""
        if not PSUTIL_AVAILABLE:
            return None

        try:
            # CPU info
            cpu_count = psutil.cpu_count(logical=True)

            # Memory info
            memory = psutil.virtual_memory()
            ram_mb = int(memory.total / (1024 * 1024))

            # Disk info
            disk = psutil.disk_usage('/')
            storage_gb = int(disk.total / (1024 * 1024 * 1024))

            # GPU detection (basic)
            gpu_available = False
            gpu_model = None

            # Try to detect NVIDIA GPU
            try:
                import subprocess
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0 and result.stdout.strip():
                    gpu_available = True
                    gpu_model = result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            # Bandwidth detection via network interface stats
            bandwidth_mbps = None
            try:
                net_stats = psutil.net_if_stats()
                for name, stats in net_stats.items():
                    if stats.isup and stats.speed > 0 and name != "lo":
                        bandwidth_mbps = stats.speed
                        break
            except Exception:
                pass

            return {
                "cpu_cores": cpu_count,
                "ram_mb": ram_mb,
                "storage_gb": storage_gb,
                "gpu_available": gpu_available,
                "gpu_model": gpu_model,
                "hashrate": None,
                "bandwidth_mbps": bandwidth_mbps,
            }

        except Exception as e:
            logger.warning(f"Failed to collect resources: {e}")
            return None


# Global heartbeat service instance
_heartbeat_service: Optional[HeartbeatService] = None


async def start_heartbeat_service(nats_url: str, node_id: str, wallet_address: str, capabilities: list):
    """Start the global heartbeat service"""
    global _heartbeat_service

    if _heartbeat_service and _heartbeat_service.running:
        logger.warning("Heartbeat service already running")
        return _heartbeat_service

    _heartbeat_service = HeartbeatService(nats_url, node_id, wallet_address, capabilities)
    await _heartbeat_service.start()
    return _heartbeat_service


async def stop_heartbeat_service():
    """Stop the global heartbeat service"""
    global _heartbeat_service

    if _heartbeat_service:
        await _heartbeat_service.stop()
        _heartbeat_service = None


def get_heartbeat_service() -> Optional[HeartbeatService]:
    """Get the global heartbeat service instance"""
    return _heartbeat_service
