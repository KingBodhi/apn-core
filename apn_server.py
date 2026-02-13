#!/usr/bin/env python3
"""
APN Core Server - Layer 0: Alpha Protocol Network Substrate

The foundational network layer of the Sovereign Stack.
Provides identity, P2P networking, heartbeats, capability advertisement,
and peer tracking for the Alpha Protocol Network.

All higher layers (Dashboard, Pythia) consume this API.
Version: 3.0.0
"""

import asyncio
import hashlib
import json
import platform
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import httpx
import uvicorn

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("Install cryptography: pip install cryptography")
    exit(1)

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from core.settings import get_settings
from core.logging_config import setup_logging, get_logger
from core.heartbeat_service import start_heartbeat_service, stop_heartbeat_service
from core.task_runtime import start_task_runtime, stop_task_runtime, get_task_runtime
from core.file_transfer import start_file_transfer, stop_file_transfer, get_file_transfer
from core.cloud_import import start_cloud_import, get_cloud_import
from core.crypto import encrypt_task_payload, decrypt_task_payload

# Logging
logger = get_logger("server")

# APN Core Version
APN_CORE_VERSION = "3.0.0"

# Server start time for uptime tracking
_server_start_time: float = 0.0

# Node identity (generated on startup)
node_private_key: Optional[ed25519.Ed25519PrivateKey] = None
node_public_key: Optional[ed25519.Ed25519PublicKey] = None
node_id: str = ""
payment_address: str = ""
public_key_hex: str = ""

# Peer registry - tracks all known peers from heartbeats
_peer_registry: Dict[str, Dict[str, Any]] = {}
_peer_registry_lock = asyncio.Lock()

# Local capabilities - what this node can do
_local_capabilities: Dict[str, Any] = {
    "agents": [],
    "software": {},
    "contribution": ["relay", "compute", "storage"],
}


def get_public_bytes(key) -> bytes:
    """Get raw public key bytes"""
    if hasattr(key, 'public_bytes_raw'):
        return key.public_bytes_raw()
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def get_private_bytes(key) -> bytes:
    """Get raw private key bytes"""
    if hasattr(key, 'private_bytes_raw'):
        return key.private_bytes_raw()
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


def generate_node_identity() -> None:
    """Generate Ed25519 keypair for node identity and wallet address.

    Identity is stored at ~/.apn/node_identity.json and is THE single source
    of truth for this device's identity on the Alpha Protocol Network.
    Both the Dashboard and Pythia consume this identity via the /api/identity endpoint.
    """
    global node_private_key, node_public_key, node_id, payment_address, public_key_hex

    settings = get_settings()
    identity_file = settings.full_identity_path

    if identity_file.exists():
        try:
            with open(identity_file, 'r') as f:
                data = json.load(f)
            seed = bytes.fromhex(data['seed'])
            node_private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
            node_public_key = node_private_key.public_key()
            node_id = data['node_id']
            payment_address = data.get('payment_address', '')

            pub_bytes = get_public_bytes(node_public_key)
            public_key_hex = pub_bytes.hex()

            # Generate payment address if not saved
            if not payment_address:
                payment_address = f"0x{hashlib.sha256(pub_bytes).hexdigest()}"
                data['payment_address'] = payment_address
                data['public_key'] = public_key_hex
                with open(identity_file, 'w') as f:
                    json.dump(data, f, indent=2)

            # Ensure public_key is in identity file
            if 'public_key' not in data:
                data['public_key'] = public_key_hex
                with open(identity_file, 'w') as f:
                    json.dump(data, f, indent=2)

            logger.info(f"Loaded node identity: {node_id}")
            logger.info(f"Wallet address: {payment_address}")
            return
        except Exception as e:
            logger.error(f"Failed to load identity: {e}")

    # Generate new identity
    node_private_key = ed25519.Ed25519PrivateKey.generate()
    node_public_key = node_private_key.public_key()

    # Generate node_id from public key
    pub_bytes = get_public_bytes(node_public_key)
    public_key_hex = pub_bytes.hex()
    node_id = f"apn_{pub_bytes[:8].hex()}"

    # Generate unique wallet address from public key hash
    payment_address = f"0x{hashlib.sha256(pub_bytes).hexdigest()}"

    # Save identity
    seed = get_private_bytes(node_private_key)
    try:
        settings.ensure_config_dir()
        identity_data = {
            'seed': seed.hex(),
            'node_id': node_id,
            'public_key': public_key_hex,
            'payment_address': payment_address,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        with open(identity_file, 'w') as f:
            json.dump(identity_data, f, indent=2)
        identity_file.chmod(0o600)
        logger.info(f"Generated new node identity: {node_id}")
        logger.info(f"Generated wallet address: {payment_address}")
    except IOError as e:
        logger.error(f"Failed to save identity file: {e}")


def load_capabilities() -> Dict[str, Any]:
    """Load capabilities from ~/.apn/capabilities.json"""
    global _local_capabilities
    settings = get_settings()
    caps_file = settings.config_dir / "capabilities.json"

    if caps_file.exists():
        try:
            with open(caps_file, 'r') as f:
                _local_capabilities = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load capabilities: {e}")

    return _local_capabilities


def save_capabilities(capabilities: Dict[str, Any]) -> bool:
    """Save capabilities to ~/.apn/capabilities.json"""
    global _local_capabilities
    settings = get_settings()
    caps_file = settings.config_dir / "capabilities.json"

    try:
        settings.ensure_config_dir()
        with open(caps_file, 'w') as f:
            json.dump(capabilities, f, indent=2)
        _local_capabilities = capabilities
        return True
    except Exception as e:
        logger.error(f"Failed to save capabilities: {e}")
        return False


def collect_system_resources() -> Dict[str, Any]:
    """Collect current system resource information"""
    if not PSUTIL_AVAILABLE:
        return {}

    try:
        cpu_count = psutil.cpu_count(logical=True)
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        resources = {
            "cpu_cores": cpu_count,
            "cpu_percent": round(cpu_percent, 1),
            "ram_mb": int(memory.total / (1024 * 1024)),
            "ram_used_mb": int(memory.used / (1024 * 1024)),
            "ram_percent": round(memory.percent, 1),
            "storage_gb": int(disk.total / (1024 * 1024 * 1024)),
            "storage_used_gb": int(disk.used / (1024 * 1024 * 1024)),
            "storage_percent": round(disk.percent, 1),
            "gpu_available": False,
            "gpu_model": None,
            "platform": platform.system(),
            "hostname": platform.node(),
        }

        # Try to detect NVIDIA GPU
        try:
            import subprocess
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0 and result.stdout.strip():
                resources["gpu_available"] = True
                resources["gpu_model"] = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return resources
    except Exception as e:
        logger.warning(f"Failed to collect resources: {e}")
        return {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    global _server_start_time
    _server_start_time = time.time()

    settings = get_settings()
    setup_logging(settings.log_level)

    generate_node_identity()
    load_capabilities()

    # Load contribution settings
    contribution = await load_contribution_settings()

    logger.info("")
    logger.info("  =====================================================")
    logger.info(f"   APN CORE v{APN_CORE_VERSION} - Layer 0: Network Substrate")
    logger.info("   Alpha Protocol Network - Sovereign Stack Foundation")
    logger.info("  =====================================================")
    logger.info("")
    logger.info(f"  Node ID:        {node_id}")
    logger.info(f"  Wallet Address: {payment_address}")
    logger.info(f"  Public Key:     {public_key_hex[:16]}...")
    logger.info(f"  NATS Relay:     {settings.nats_relay}")
    logger.info(f"  API Port:       {settings.port}")
    logger.info(f"  Capabilities:   {_local_capabilities.get('agents', [])}")
    logger.info("")
    logger.info(f"  Nora URL:       {settings.nora_url}")
    logger.info("")
    logger.info("  Endpoints:")
    logger.info(f"    GET  http://localhost:{settings.port}/api/identity")
    logger.info(f"    GET  http://localhost:{settings.port}/api/network/peers")
    logger.info(f"    GET  http://localhost:{settings.port}/api/capabilities")
    logger.info(f"    GET  http://localhost:{settings.port}/api/resources")
    logger.info(f"    GET  http://localhost:{settings.port}/api/voice/status")
    logger.info(f"    POST http://localhost:{settings.port}/api/voice/interaction")
    logger.info(f"    POST http://localhost:{settings.port}/api/voice/chat")
    logger.info("")

    # Start heartbeat service if contribution enabled
    if contribution and contribution.get('enabled', False):
        try:
            capabilities = []
            if contribution.get('relay_enabled'): capabilities.append('relay')
            if contribution.get('compute_enabled'): capabilities.append('compute')
            if contribution.get('storage_enabled'): capabilities.append('storage')

            await start_heartbeat_service(
                nats_url=settings.nats_relay,
                node_id=node_id,
                wallet_address=payment_address,
                capabilities=capabilities or ['compute', 'relay', 'storage']
            )
            logger.info("Heartbeat service started - earning VIBE rewards!")
        except Exception as e:
            logger.error(f"Failed to start heartbeat service: {e}")

    # Start peer listener (subscribes to apn.heartbeat to track peers)
    asyncio.create_task(_start_peer_listener())

    # Start task runtime (receives tasks from Pythia via NATS)
    try:
        await start_task_runtime(
            nats_url=settings.nats_relay,
            node_id=node_id,
            wallet_address=payment_address,
        )
        logger.info("Task runtime started - ready to execute agents!")
    except Exception as e:
        logger.error(f"Failed to start task runtime: {e}")

    # Start file transfer service (P2P file transfers via NATS)
    try:
        await start_file_transfer(
            nats_url=settings.nats_relay,
            node_id=node_id,
        )
        logger.info("File transfer service started - ready for P2P transfers!")
    except Exception as e:
        logger.error(f"Failed to start file transfer service: {e}")

    # Start cloud import service (Google Drive, OneDrive, Dropbox downloads)
    start_cloud_import()
    logger.info("Cloud import service started - ready for cloud downloads!")

    yield

    # Shutdown
    logger.info("Shutting down APN Core...")
    try:
        await stop_file_transfer()
    except Exception as e:
        logger.error(f"Error stopping file transfer service: {e}")
    try:
        await stop_task_runtime()
    except Exception as e:
        logger.error(f"Error stopping task runtime: {e}")
    try:
        await stop_heartbeat_service()
    except Exception as e:
        logger.error(f"Error stopping heartbeat service: {e}")


async def _start_peer_listener():
    """Subscribe to apn.heartbeat and apn.discovery to track network peers"""
    try:
        from nats.aio.client import Client as NATS
    except ImportError:
        logger.warning("nats-py not available - peer tracking disabled")
        return

    settings = get_settings()

    try:
        nc = NATS()
        await nc.connect(settings.nats_relay)
        logger.info("Peer listener connected to NATS relay")

        async def on_heartbeat(msg):
            try:
                data = json.loads(msg.data.decode())
                peer_id = data.get("node_id", "")
                if peer_id and peer_id != node_id:
                    async with _peer_registry_lock:
                        _peer_registry[peer_id] = {
                            "node_id": peer_id,
                            "wallet_address": data.get("wallet_address", ""),
                            "capabilities": data.get("capabilities", []),
                            "resources": data.get("resources"),
                            "agents": data.get("agents", []),
                            "software": data.get("software", {}),
                            "last_seen": datetime.now(timezone.utc).isoformat(),
                            "connection_type": "NATS",
                        }
            except Exception as e:
                logger.debug(f"Failed to parse heartbeat: {e}")

        async def on_discovery(msg):
            await on_heartbeat(msg)

        await nc.subscribe("apn.heartbeat", cb=on_heartbeat)
        await nc.subscribe("apn.discovery", cb=on_discovery)
        logger.info("Subscribed to apn.heartbeat and apn.discovery")

        # Keep connection alive
        while True:
            await asyncio.sleep(60)
            # Prune stale peers (not seen in 5 minutes)
            cutoff = datetime.now(timezone.utc).timestamp() - 300
            async with _peer_registry_lock:
                stale = [
                    pid for pid, pdata in _peer_registry.items()
                    if datetime.fromisoformat(pdata["last_seen"]).timestamp() < cutoff
                ]
                for pid in stale:
                    del _peer_registry[pid]
                if stale:
                    logger.debug(f"Pruned {len(stale)} stale peers")

    except Exception as e:
        logger.error(f"Peer listener failed: {e}")
        # Retry after delay
        await asyncio.sleep(10)
        asyncio.create_task(_start_peer_listener())


async def load_contribution_settings():
    """Load contribution settings from file"""
    settings = get_settings()
    contrib_file = settings.config_dir / "contribution_settings.json"

    if contrib_file.exists():
        try:
            with open(contrib_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load contribution settings: {e}")

    return None


async def save_contribution_settings(settings: dict):
    """Save contribution settings to file"""
    config = get_settings()
    contrib_file = config.config_dir / "contribution_settings.json"

    try:
        config.ensure_config_dir()
        with open(contrib_file, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save contribution settings: {e}")
        return False


# ============= Request/Response Models =============

class ContributionSettings(BaseModel):
    enabled: bool
    relay_enabled: bool = True
    compute_enabled: bool = True
    storage_enabled: bool = True


class CapabilitiesUpdate(BaseModel):
    agents: List[str] = []
    software: Dict[str, Any] = {}
    contribution: List[str] = ["relay", "compute", "storage"]


# ============= Create FastAPI App =============

app = FastAPI(
    title="APN Core",
    description="Alpha Protocol Network - Layer 0: Network Substrate. "
                "The foundational network layer of the Sovereign Stack. "
                "Dashboard and Pythia consume this API.",
    version=APN_CORE_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============= Core API Endpoints =============
# These are the primary endpoints consumed by Dashboard and Pythia


@app.get("/health")
async def health():
    """Health check - used by Dashboard/Pythia to verify APN Core is running"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APN_CORE_VERSION,
        "node_id": node_id,
        "uptime_seconds": int(time.time() - _server_start_time) if _server_start_time else 0,
    }


@app.get("/api/identity")
async def get_identity():
    """Get this node's identity - THE primary endpoint for identity unification.

    Dashboard and Pythia call this to get the single source of truth
    for this device's identity on the APN network.
    """
    return {
        "node_id": node_id,
        "wallet_address": payment_address,
        "public_key": public_key_hex,
        "identity_file": str(get_settings().full_identity_path),
        "created_at": _get_identity_created_at(),
    }


@app.get("/api/version")
async def get_version():
    """Get version and node information"""
    return {
        "version": APN_CORE_VERSION,
        "protocol": "alpha/3.0.0",
        "layer": 0,
        "layer_name": "Alpha Protocol Network Substrate",
        "node_id": node_id,
        "wallet_address": payment_address,
        "uptime_seconds": int(time.time() - _server_start_time) if _server_start_time else 0,
    }


# ============= Network Endpoints =============


@app.get("/api/network/peers")
async def get_network_peers():
    """Get all known peers on the APN network.

    Peers are discovered via NATS heartbeat/discovery subscriptions.
    Dashboard uses this to display the network map.
    Pythia uses this for task routing decisions.
    """
    async with _peer_registry_lock:
        peers = list(_peer_registry.values())

    return {
        "node_id": node_id,
        "peer_count": len(peers),
        "peers": peers,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/network/stats")
async def get_network_stats():
    """Get network statistics for this node"""
    async with _peer_registry_lock:
        peer_count = len(_peer_registry)

    resources = collect_system_resources()

    return {
        "node_id": node_id,
        "status": "online",
        "peers_connected": peer_count,
        "relay_url": get_settings().nats_relay,
        "uptime_seconds": int(time.time() - _server_start_time) if _server_start_time else 0,
        "resources": resources,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============= Capability Endpoints =============


@app.get("/api/capabilities")
async def get_capabilities():
    """Get this node's capabilities - what agents and software are available.

    Dashboard writes capabilities here when user configures software.
    Pythia reads this to determine task routing.
    Capabilities are also broadcast in heartbeat for network-wide discovery.
    """
    return {
        "node_id": node_id,
        "capabilities": _local_capabilities,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/capabilities")
async def update_capabilities(caps: CapabilitiesUpdate):
    """Register/update this node's capabilities.

    Called by Dashboard when user configures available software,
    or when agents are installed/updated.
    """
    new_caps = {
        "agents": caps.agents,
        "software": caps.software,
        "contribution": caps.contribution,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if save_capabilities(new_caps):
        return {
            "status": "success",
            "capabilities": new_caps,
        }

    raise HTTPException(status_code=500, detail="Failed to save capabilities")


# ============= Resource Endpoints =============


@app.get("/api/resources")
async def get_resources():
    """Get current system resource usage"""
    resources = collect_system_resources()
    return {
        "node_id": node_id,
        "resources": resources,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============= Contribution Endpoints =============


@app.get("/api/contribution/status")
async def get_contribution_status():
    """Get contribution status"""
    settings = await load_contribution_settings()

    if not settings:
        settings = {
            "enabled": False,
            "relay_enabled": True,
            "compute_enabled": True,
            "storage_enabled": True
        }

    return {
        "node_id": node_id,
        "wallet_address": payment_address,
        "contribution": settings,
        "relay_url": get_settings().nats_relay,
    }


@app.post("/api/contribution/settings")
async def update_contribution_settings(settings: ContributionSettings):
    """Update contribution settings and restart heartbeat"""
    saved = await save_contribution_settings(settings.dict())

    if not saved:
        raise HTTPException(status_code=500, detail="Failed to save settings")

    return {
        "status": "success",
        "message": "Settings saved. Restart APN Core to apply changes.",
        "settings": settings.dict(),
    }


# ============= Task Runtime Endpoints =============


@app.get("/api/tasks/active")
async def get_active_tasks():
    """Get currently executing tasks"""
    runtime = get_task_runtime()
    if not runtime:
        return {"tasks": [], "runtime_status": "not_running"}

    return {
        "tasks": runtime.get_active_tasks(),
        "runtime_status": "running" if runtime.running else "stopped",
    }


@app.get("/api/tasks/history")
async def get_task_history(limit: int = 20):
    """Get recent task execution history"""
    runtime = get_task_runtime()
    if not runtime:
        return {"history": [], "runtime_status": "not_running"}

    return {
        "history": runtime.get_task_history(limit),
        "runtime_status": "running" if runtime.running else "stopped",
    }


@app.get("/api/tasks/stats")
async def get_task_stats():
    """Get task execution statistics"""
    runtime = get_task_runtime()
    if not runtime:
        return {"stats": None, "runtime_status": "not_running"}

    return {
        "stats": runtime.get_stats(),
        "runtime_status": "running" if runtime.running else "stopped",
    }


# ============= File Transfer Endpoints =============


class FileSendRequest(BaseModel):
    target_node_id: str
    file_path: str


@app.post("/api/files/send")
async def send_file(req: FileSendRequest):
    """Initiate a P2P file transfer to another node"""
    ft = get_file_transfer()
    if not ft:
        raise HTTPException(503, "File transfer service not running")

    try:
        info = await ft.send_file(req.target_node_id, req.file_path)
        return {"transfer": info.to_dict()}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Transfer failed: {e}")


@app.get("/api/files/transfers")
async def get_transfers():
    """Get all active file transfers"""
    ft = get_file_transfer()
    if not ft:
        return {"active": [], "service_status": "not_running"}

    return {
        "active": ft.get_active_transfers(),
        "service_status": "running",
    }


@app.get("/api/files/transfers/{transfer_id}")
async def get_transfer(transfer_id: str):
    """Get status of a specific transfer"""
    ft = get_file_transfer()
    if not ft:
        raise HTTPException(503, "File transfer service not running")

    info = ft.get_transfer(transfer_id)
    if not info:
        raise HTTPException(404, f"Transfer {transfer_id} not found")
    return {"transfer": info}


@app.get("/api/files/history")
async def get_file_history(limit: int = 50):
    """Get file transfer history"""
    ft = get_file_transfer()
    if not ft:
        return {"history": [], "service_status": "not_running"}

    return {
        "history": ft.get_transfer_history(limit),
        "service_status": "running",
    }


@app.post("/api/files/transfers/{transfer_id}/accept")
async def accept_transfer(transfer_id: str):
    """Accept a pending incoming transfer"""
    ft = get_file_transfer()
    if not ft:
        raise HTTPException(503, "File transfer service not running")

    accepted = await ft.accept_transfer(transfer_id)
    if not accepted:
        raise HTTPException(404, "Transfer not found or not in offered state")
    return {"accepted": True, "transfer_id": transfer_id}


@app.post("/api/files/transfers/{transfer_id}/cancel")
async def cancel_transfer(transfer_id: str):
    """Cancel an active transfer"""
    ft = get_file_transfer()
    if not ft:
        raise HTTPException(503, "File transfer service not running")

    cancelled = await ft.cancel_transfer(transfer_id)
    if not cancelled:
        raise HTTPException(404, "Transfer not found")
    return {"cancelled": True, "transfer_id": transfer_id}


# ============= Cloud Import Endpoints =============


class CloudImportRequest(BaseModel):
    url: str
    file_name: Optional[str] = None


@app.post("/api/cloud/import")
async def cloud_import(req: CloudImportRequest):
    """Import a file from a cloud storage URL (Google Drive, OneDrive, Dropbox)"""
    ci = get_cloud_import()
    if not ci:
        raise HTTPException(503, "Cloud import service not running")

    try:
        job = await ci.import_url(req.url, req.file_name)
        return {"import": job.to_dict()}
    except Exception as e:
        raise HTTPException(500, f"Import failed: {e}")


@app.get("/api/cloud/imports")
async def get_imports():
    """Get all active cloud imports"""
    ci = get_cloud_import()
    if not ci:
        return {"active": [], "service_status": "not_running"}

    return {
        "active": ci.get_active_imports(),
        "service_status": "running",
    }


@app.get("/api/cloud/imports/{job_id}")
async def get_import_status(job_id: str):
    """Get status of a specific import"""
    ci = get_cloud_import()
    if not ci:
        raise HTTPException(503, "Cloud import service not running")

    job = ci.get_import(job_id)
    if not job:
        raise HTTPException(404, f"Import {job_id} not found")
    return {"import": job}


@app.get("/api/cloud/history")
async def get_import_history(limit: int = 50):
    """Get cloud import history"""
    ci = get_cloud_import()
    if not ci:
        return {"history": [], "service_status": "not_running"}

    return {
        "history": ci.get_import_history(limit),
        "service_status": "running",
    }


@app.get("/api/cloud/cache")
async def get_cache_stats():
    """Get download cache statistics"""
    ci = get_cloud_import()
    if not ci:
        return {"cache": None, "service_status": "not_running"}

    return {
        "cache": ci.get_cache_stats(),
        "service_status": "running",
    }


@app.post("/api/cloud/cache/clear")
async def clear_cache():
    """Clear the download cache index"""
    ci = get_cloud_import()
    if not ci:
        raise HTTPException(503, "Cloud import service not running")

    result = ci.clear_cache()
    return {"result": result}


@app.get("/api/cloud/resolve")
async def resolve_url(url: str):
    """Resolve a cloud URL to a direct download URL (preview, no download)"""
    ci = get_cloud_import()
    if not ci:
        raise HTTPException(503, "Cloud import service not running")

    provider = ci.detect_provider(url)
    resolved = ci.resolve_url(url, provider)

    return {
        "source_url": url,
        "provider": provider.value,
        "resolved_url": resolved,
    }


# ============= Crypto Endpoints =============


class EncryptRequest(BaseModel):
    payload: dict
    peer_public_key: str


@app.post("/api/crypto/encrypt")
async def encrypt_payload_endpoint(req: EncryptRequest):
    """Encrypt a task payload for a specific peer"""
    result = encrypt_task_payload(req.payload, req.peer_public_key)
    return {"encrypted_payload": result}


@app.post("/api/crypto/decrypt")
async def decrypt_payload_endpoint(req: EncryptRequest):
    """Decrypt a task payload from a specific peer"""
    result = decrypt_task_payload(req.payload, req.peer_public_key)
    return {"decrypted_payload": result}


# ============= Auth Proxy Endpoints =============
# Proxy auth requests to Dashboard backend so the phone can authenticate


@app.get("/api/auth/status")
async def auth_status():
    """Check if Dashboard has valid GitHub auth — phone inherits this identity"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{settings.nora_url}/api/auth/github/check")
            data = resp.json()
            is_valid = data.get("data") == "VALID"

            # Read username from Dashboard config if available
            config_path = Path.home() / ".local" / "share" / "duck-kanban" / "config.json"
            username = None
            email = None
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        cfg = json.load(f)
                    gh = cfg.get("github", {})
                    username = gh.get("username")
                    email = gh.get("primary_email")
                except Exception:
                    pass

            return {
                "authenticated": is_valid,
                "username": username,
                "email": email,
                "node_id": node_id,
                "auth_source": "dashboard_github",
            }
        except httpx.ConnectError:
            raise HTTPException(502, f"Cannot reach Dashboard at {settings.nora_url}")


@app.post("/api/auth/github/device/start")
async def auth_device_start():
    """Proxy GitHub device flow start to Dashboard"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{settings.nora_url}/api/auth/github/device/start")
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(502, f"Cannot reach Dashboard at {settings.nora_url}")


@app.post("/api/auth/github/device/poll")
async def auth_device_poll():
    """Proxy GitHub device flow poll to Dashboard"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{settings.nora_url}/api/auth/github/device/poll")
            response_data = resp.json()
            json_resp = JSONResponse(content=response_data, status_code=resp.status_code)
            for key, value in resp.headers.multi_items():
                if key.lower() == "set-cookie":
                    json_resp.headers.append(key, value)
            return json_resp
        except httpx.ConnectError:
            raise HTTPException(502, f"Cannot reach Dashboard at {settings.nora_url}")


# ============= Mobile Registration Endpoint =============


@app.post("/register")
async def register_mobile_node(body: dict):
    """Accept registration from companion app (phone as sovereign node)"""
    peer_node_id = body.get("nodeId", "unknown")
    logger.info(f"Mobile node registered: {peer_node_id}")

    async with _peer_registry_lock:
        _peer_registry[peer_node_id] = {
            "node_id": peer_node_id,
            "wallet_address": body.get("paymentAddress", ""),
            "capabilities": body.get("roles", []),
            "settings": body.get("settings", {}),
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "connection_type": body.get("settings", {}).get("transport", "usb"),
        }

    return {
        "status": "registered",
        "dashboard_node_id": node_id,
        "message": f"Welcome to the sovereign stack, {peer_node_id}",
    }


# ============= Voice Proxy Endpoints =============
# Proxy voice requests to Dashboard's Nora backend


@app.get("/api/voice/status")
async def voice_status():
    """Proxy Nora status from Dashboard backend"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{settings.nora_url}/api/nora/status")
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(502, f"Cannot reach Nora at {settings.nora_url}")
        except Exception as e:
            raise HTTPException(502, f"Voice status proxy error: {e}")


@app.post("/api/voice/interaction")
async def voice_interaction(request: Request, body: dict):
    """Voice interaction: transcribe audio with Whisper, then chat with Nora.
    Two-step approach avoids TTS failures and is more reliable."""
    settings = get_settings()
    cookies = dict(request.cookies)
    session_id = body.get("sessionId", "mobile")
    audio_input = body.get("audioInput")

    if not audio_input:
        raise HTTPException(400, "No audioInput provided")

    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            # Step 1: Transcribe audio via Nora's STT endpoint
            transcription = None
            try:
                stt_resp = await client.post(
                    f"{settings.nora_url}/api/nora/voice/transcribe",
                    json={"audioData": audio_input},
                    cookies=cookies,
                )
                if stt_resp.status_code == 200:
                    stt_data = stt_resp.json()
                    transcription = stt_data.get("text", "")
                    logger.info(f"Voice transcription: '{transcription}'")
                else:
                    logger.warning(f"STT failed ({stt_resp.status_code}), trying raw text")
            except Exception as e:
                logger.warning(f"STT error: {e}")

            if not transcription:
                return {"transcription": "", "responseText": "Could not transcribe audio", "audioResponse": None}

            # Step 2: Send transcribed text to Nora chat (text only — reliable)
            chat_resp = await client.post(
                f"{settings.nora_url}/api/nora/chat",
                json={
                    "message": transcription,
                    "sessionId": session_id,
                    "voiceEnabled": False,
                },
                cookies=cookies,
            )

            if chat_resp.status_code == 200:
                chat_data = chat_resp.json()
                response_text = chat_data.get("content", "")

                # Step 3: TTS via Chatterbox directly (bypass Nora's broken TTS)
                import base64
                audio_b64 = None
                if response_text:
                    try:
                        tts_resp = await client.post(
                            "http://127.0.0.1:8100/tts",
                            json={"text": response_text, "exaggeration": 0.5},
                            timeout=30.0,
                        )
                        if tts_resp.status_code == 200:
                            audio_b64 = base64.b64encode(tts_resp.content).decode()
                            logger.info(f"TTS generated {len(tts_resp.content)} bytes")
                        else:
                            logger.warning(f"TTS failed: {tts_resp.status_code}")
                    except Exception as e:
                        logger.warning(f"TTS error (continuing without audio): {e}")

                return {
                    "transcription": transcription,
                    "responseText": response_text,
                    "audioResponse": audio_b64,
                }
            else:
                return {
                    "transcription": transcription,
                    "responseText": f"Nora error: {chat_resp.status_code}",
                    "audioResponse": None,
                }
        except httpx.ConnectError:
            raise HTTPException(502, f"Cannot reach Nora at {settings.nora_url}")
        except Exception as e:
            raise HTTPException(502, f"Voice interaction proxy error: {e}")


@app.post("/api/voice/chat")
async def voice_chat(request: Request, body: dict):
    """Proxy text chat to Nora"""
    settings = get_settings()
    cookies = dict(request.cookies)
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                f"{settings.nora_url}/api/nora/chat",
                json=body,
                cookies=cookies,
            )
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(502, f"Cannot reach Nora at {settings.nora_url}")
        except Exception as e:
            raise HTTPException(502, f"Voice chat proxy error: {e}")


# ============= Root Endpoint =============


@app.get("/")
async def root():
    """Root endpoint - service info"""
    async with _peer_registry_lock:
        peer_count = len(_peer_registry)

    return {
        "service": "APN Core",
        "layer": "Layer 0: Alpha Protocol Network Substrate",
        "version": APN_CORE_VERSION,
        "node_id": node_id,
        "wallet_address": payment_address,
        "peers_connected": peer_count,
        "capabilities": _local_capabilities.get("agents", []),
        "status": "online",
        "uptime_seconds": int(time.time() - _server_start_time) if _server_start_time else 0,
    }


# ============= Helper Functions =============


def _get_identity_created_at() -> Optional[str]:
    """Get identity creation timestamp from identity file"""
    settings = get_settings()
    identity_file = settings.full_identity_path
    if identity_file.exists():
        try:
            with open(identity_file, 'r') as f:
                data = json.load(f)
            return data.get('created_at')
        except Exception:
            pass
    return None


# ============= Main =============


def main():
    """Run the APN Core server"""
    settings = get_settings()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
        h11_max_incomplete_event_size=10 * 1024 * 1024,  # 10MB for voice audio
    )


if __name__ == "__main__":
    main()
