#!/usr/bin/env python3
"""
APN Core Minimal Server - Essential functionality only

Provides basic API endpoints for node status and contribution settings.
Version: 2.0.0 (Minimal)
"""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("Install cryptography: pip install cryptography")
    exit(1)

from core.settings import get_settings
from core.logging_config import setup_logging, get_logger
from core.heartbeat_service import start_heartbeat_service, stop_heartbeat_service

# Logging
logger = get_logger("server")

# APN Core Version
APN_CORE_VERSION = "2.0.0-minimal"

# Node identity (generated on startup)
node_private_key: Optional[ed25519.Ed25519PrivateKey] = None
node_public_key: Optional[ed25519.Ed25519PublicKey] = None
node_id: str = ""
payment_address: str = ""


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
    """Generate Ed25519 keypair for node identity and wallet address"""
    global node_private_key, node_public_key, node_id, payment_address

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

            # Generate payment address if not saved
            if not payment_address:
                pub_bytes = get_public_bytes(node_public_key)
                payment_address = f"0x{hashlib.sha256(pub_bytes).hexdigest()}"
                data['payment_address'] = payment_address
                with open(identity_file, 'w') as f:
                    json.dump(data, f)

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
    node_id = f"apn_{pub_bytes[:8].hex()}"

    # Generate unique wallet address from public key hash
    payment_address = f"0x{hashlib.sha256(pub_bytes).hexdigest()}"

    # Save identity
    seed = get_private_bytes(node_private_key)
    try:
        settings.ensure_config_dir()
        with open(identity_file, 'w') as f:
            json.dump({
                'seed': seed.hex(),
                'node_id': node_id,
                'payment_address': payment_address
            }, f)
        identity_file.chmod(0o600)
        logger.info(f"Generated new node identity: {node_id}")
        logger.info(f"Generated wallet address: {payment_address}")
    except IOError as e:
        logger.error(f"Failed to save identity file: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    settings = get_settings()
    setup_logging(settings.log_level)

    generate_node_identity()

    # Load contribution settings
    contribution = await load_contribution_settings()

    logger.info("")
    logger.info("  =====================================================")
    logger.info(f"            APN CORE v{APN_CORE_VERSION}")
    logger.info("   Alpha Protocol Network - Minimal Client")
    logger.info("  =====================================================")
    logger.info("")
    logger.info(f"  Node ID: {node_id}")
    logger.info(f"  Wallet Address: {payment_address}")
    logger.info(f"  NATS Relay: {settings.nats_relay}")
    logger.info(f"  API Port: {settings.port}")
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
            logger.info("✅ Heartbeat service started - earning VIBE rewards!")
        except Exception as e:
            logger.error(f"Failed to start heartbeat service: {e}")

    yield

    # Shutdown
    logger.info("Shutting down APN Core...")
    try:
        await stop_heartbeat_service()
    except Exception as e:
        logger.error(f"Error stopping heartbeat service: {e}")


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


# Request Models
class ContributionSettings(BaseModel):
    enabled: bool
    relay_enabled: bool = True
    compute_enabled: bool = True
    storage_enabled: bool = True


# Create FastAPI app
app = FastAPI(
    title="APN Core Minimal",
    description="Alpha Protocol Network - Minimal client node",
    version=APN_CORE_VERSION,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============= API Endpoints =============

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": APN_CORE_VERSION
    }


@app.get("/api/version")
async def get_version():
    """Get version information"""
    return {
        "version": APN_CORE_VERSION,
        "node_id": node_id,
        "wallet_address": payment_address
    }


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
        "relay_url": get_settings().nats_relay
    }


@app.post("/api/contribution/settings")
async def update_contribution_settings(settings: ContributionSettings):
    """Update contribution settings"""
    saved = await save_contribution_settings(settings.dict())

    if not saved:
        return {"status": "error", "message": "Failed to save settings"}

    # Note: Requires restart to apply changes
    return {
        "status": "success",
        "message": "Settings saved. Restart APN Core to apply changes.",
        "settings": settings.dict()
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "APN Core Minimal",
        "version": APN_CORE_VERSION,
        "node_id": node_id,
        "wallet": payment_address,
        "status": "online"
    }


# ============= Main =============

def main():
    """Run the APN Core server"""
    settings = get_settings()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
