#!/usr/bin/env python3
"""
APN CORE Server - Alpha Protocol Network Core Services
Production-ready server with proper security, logging, and persistence.

Version: 1.0.0
"""

import asyncio
import base64
import json
import os
import platform
import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
import uvicorn
import httpx

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Cryptography for secure channel
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.exceptions import InvalidSignature

# Local modules
from core.settings import get_settings, APNSettings
from core.database import get_database, close_database, APNDatabase
from core.logging_config import setup_logging, get_logger
from core.heartbeat_service import start_heartbeat_service, stop_heartbeat_service

# System resources
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Initialize logging
logger = get_logger("server")

# APN Core Version
APN_CORE_VERSION = "1.0.0"
APN_PROTOCOL_VERSION = "alpha/1.0.0"


def get_public_bytes(key) -> bytes:
    """Get raw public key bytes (compatible with different cryptography versions)"""
    if hasattr(key, 'public_bytes_raw'):
        return key.public_bytes_raw()
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def get_private_bytes(key) -> bytes:
    """Get raw private key bytes (compatible with different cryptography versions)"""
    if hasattr(key, 'private_bytes_raw'):
        return key.private_bytes_raw()
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


# ============= Request Models with Validation =============

class PeerRegistration(BaseModel):
    """Peer registration request with validation"""
    nodeId: str = Field(..., min_length=4, max_length=128, description="Node identifier")
    publicKey: str = Field(..., min_length=32, max_length=256, description="Hex-encoded public key")
    paymentAddress: Optional[str] = Field(default="", max_length=256)
    roles: Optional[List[str]] = Field(default_factory=list, max_length=10)
    settings: Optional[Dict[str, Any]] = Field(default_factory=dict)
    signature: Optional[str] = Field(default=None, description="Ed25519 signature for verification")
    timestamp: Optional[int] = Field(default=None, description="Unix timestamp for replay protection")

    @field_validator("publicKey")
    @classmethod
    def validate_public_key(cls, v):
        """Validate hex-encoded public key"""
        try:
            key_bytes = bytes.fromhex(v)
            if len(key_bytes) != 32:
                raise ValueError("Public key must be 32 bytes")
        except ValueError as e:
            raise ValueError(f"Invalid public key format: {e}")
        return v


class HandshakeMessage(BaseModel):
    """Secure handshake request"""
    type: str = Field(..., pattern="^handshake_init$")
    node_id: str = Field(..., min_length=4, max_length=128)
    public_key: str = Field(..., min_length=32, max_length=256)
    ephemeral_key: str = Field(..., description="Base64-encoded ephemeral X25519 public key")
    timestamp: int = Field(..., ge=0)
    signature: str = Field(..., description="Base64-encoded Ed25519 signature")


class TaskCreate(BaseModel):
    """Task creation request"""
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = Field(default="", max_length=5000)
    assigned_to: Optional[str] = Field(default="", max_length=128)
    priority: Optional[str] = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    status: Optional[str] = Field(default="pending", pattern="^(pending|in_progress|completed|cancelled)$")
    due_date: Optional[str] = Field(default=None)


class TaskUpdate(BaseModel):
    """Task update request"""
    title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    assigned_to: Optional[str] = Field(default=None, max_length=128)
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high|urgent)$")
    status: Optional[str] = Field(default=None, pattern="^(pending|in_progress|completed|cancelled)$")
    due_date: Optional[str] = Field(default=None)


class SecureMessage(BaseModel):
    """Encrypted message from peer"""
    from_peer: str = Field(..., alias="from", min_length=4, max_length=128)
    payload: str = Field(..., description="Base64-encoded encrypted payload")


class ContributionSettings(BaseModel):
    """Device contribution settings"""
    enabled: bool = False
    relay: bool = False
    compute: bool = False
    storage: bool = False
    storage_gb_allocated: int = Field(default=10, ge=0, le=10000)
    compute_cores_allocated: int = Field(default=1, ge=0, le=256)
    bandwidth_limit_mbps: int = Field(default=100, ge=0, le=10000)


class MeshMessage(BaseModel):
    """Mesh network message"""
    dest_node: str = Field(..., min_length=1, max_length=128)
    payload: Dict[str, Any]
    hop_count: Optional[int] = Field(default=0, ge=0, le=10)


class WearableState(BaseModel):
    """Wearable device state"""
    ring_connected: Optional[bool] = None
    glasses_connected: Optional[bool] = None
    battery_level: Optional[int] = Field(default=None, ge=0, le=100)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============= Global State =============

@dataclass
class APNPeerNode:
    """Represents a connected APN peer node"""
    node_id: str
    public_key: str
    roles: List[str] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    connected_at: datetime = field(default_factory=datetime.now)
    websocket: Optional[WebSocket] = None


# Node identity (generated on startup)
node_private_key: Optional[ed25519.Ed25519PrivateKey] = None
node_public_key: Optional[ed25519.Ed25519PublicKey] = None
node_id: str = ""
payment_address: str = ""  # Wallet address derived from public key

# In-memory caches (backed by database)
peers: Dict[str, APNPeerNode] = {}
secure_sessions: Dict[str, Dict[str, Any]] = {}
websocket_connections: Dict[str, WebSocket] = {}
peer_connections: Dict[str, Dict[str, Any]] = {}

# Database instance
db: Optional[APNDatabase] = None


# ============= App Setup =============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown"""
    global db

    settings = get_settings()
    setup_logging(settings.log_level)

    generate_node_identity()

    # Initialize database
    db = await get_database()
    logger.info(f"Database initialized: {settings.full_database_path}")

    # Load contribution settings from database
    contribution = await db.get_setting("contribution_settings")
    if contribution:
        logger.info(f"Loaded contribution settings: enabled={contribution.get('enabled', False)}")

    logger.info("")
    logger.info("  =====================================================")
    logger.info(f"            APN CORE v{APN_CORE_VERSION}")
    logger.info("   Alpha Protocol Network - Sovereign Mesh Node")
    logger.info("  =====================================================")
    logger.info("")
    logger.info(f"  Node ID: {node_id}")
    logger.info(f"  Wallet Address: {payment_address}")
    logger.info(f"  NATS Relay: {settings.nats_relay}")
    logger.info(f"  Nora Backend: {settings.nora_url}")
    logger.info(f"  API Port: {settings.port}")
    logger.info("")

    # Start mesh peer connections in background
    asyncio.create_task(connect_to_mesh_peers())

    # Start heartbeat service if contribution is enabled
    if contribution and contribution.get('enabled', False):
        try:
            capabilities = []
            if contribution.get('relay_enabled'): capabilities.append('relay')
            if contribution.get('compute_enabled'): capabilities.append('compute')
            if contribution.get('storage_enabled'): capabilities.append('storage')

            await start_heartbeat_service(
                nats_url=settings.nats_relay,
                node_id=node_id,
                wallet_address=payment_address or "0x0000000000000000000000000000000000000000",
                capabilities=capabilities or ['compute', 'relay', 'storage']
            )
            logger.info("✅ Heartbeat service started - earning VIBE rewards!")
        except Exception as e:
            logger.error(f"Failed to start heartbeat service: {e}")

    yield  # Server runs here

    # Shutdown
    logger.info("Shutting down APN CORE Server...")

    # Stop heartbeat service
    try:
        await stop_heartbeat_service()
    except Exception as e:
        logger.error(f"Error stopping heartbeat service: {e}")

    await close_database()


def create_app() -> FastAPI:
    """Create and configure FastAPI application"""
    settings = get_settings()

    # Initialize rate limiter
    limiter = Limiter(key_func=get_remote_address)

    app = FastAPI(
        title="APN CORE Server",
        description="Alpha Protocol Network - Sovereign mesh networking for device contribution",
        version=APN_CORE_VERSION,
        lifespan=lifespan,
    )

    # Add rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Configure CORS with proper security
    if settings.debug and "*" in settings.cors_origins:
        logger.warning("CORS is configured with wildcard origin - NOT RECOMMENDED FOR PRODUCTION")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    return app


app = create_app()


# ============= Dependencies =============

async def get_db() -> APNDatabase:
    """Dependency to get database instance"""
    global db
    if db is None:
        db = await get_database()
    return db


async def verify_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> bool:
    """Verify API key if configured"""
    settings = get_settings()

    # If no API key configured, allow all requests (development mode)
    if not settings.api_key:
        return True

    if not x_api_key:
        logger.warning("Request without API key rejected")
        raise HTTPException(status_code=401, detail="API key required")

    if x_api_key != settings.api_key:
        logger.warning("Invalid API key rejected")
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True


# ============= Initialization =============

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

            # Generate payment address if not saved (for old identities)
            if not payment_address:
                pub_bytes = get_public_bytes(node_public_key)
                payment_address = f"0x{hashlib.sha256(pub_bytes).hexdigest()}"
                # Update identity file with payment address
                data['payment_address'] = payment_address
                with open(identity_file, 'w') as f:
                    json.dump(data, f)
                logger.info(f"Generated payment address for existing identity: {payment_address}")

            logger.info(f"Loaded node identity: {node_id}")
            logger.info(f"Wallet address: {payment_address}")
            return
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to load identity from {identity_file}: {e}")
        except IOError as e:
            logger.error(f"Failed to read identity file: {e}")

    # Generate new identity
    node_private_key = ed25519.Ed25519PrivateKey.generate()
    node_public_key = node_private_key.public_key()

    # Generate node_id from public key
    pub_bytes = get_public_bytes(node_public_key)
    node_id = f"apn_{pub_bytes[:8].hex()}"

    # Generate unique wallet address from public key hash
    payment_address = f"0x{hashlib.sha256(pub_bytes).hexdigest()}"

    # Save identity with payment address
    seed = get_private_bytes(node_private_key)
    try:
        settings.ensure_config_dir()
        with open(identity_file, 'w') as f:
            json.dump({
                'seed': seed.hex(),
                'node_id': node_id,
                'payment_address': payment_address
            }, f)
        identity_file.chmod(0o600)  # Secure file permissions
        logger.info(f"Generated new node identity: {node_id}")
        logger.info(f"Generated wallet address: {payment_address}")
    except IOError as e:
        logger.error(f"Failed to save identity file: {e}")


# ============= Health & Info Endpoints =============

@app.get("/health")
async def health(database: APNDatabase = Depends(get_db)):
    """Health check endpoint with dependency status"""
    settings = get_settings()

    # Check database connectivity
    db_healthy = True
    try:
        await database.get_setting("health_check")
    except Exception:
        db_healthy = False

    return {
        "status": "ok" if db_healthy else "degraded",
        "node_id": node_id,
        "version": APN_CORE_VERSION,
        "protocol": APN_PROTOCOL_VERSION,
        "components": {
            "database": "healthy" if db_healthy else "unhealthy",
            "mesh_peers": len(peer_connections),
            "websocket_clients": len(websocket_connections),
        }
    }


@app.get("/api/version")
async def get_version():
    """Get APN Core version information"""
    settings = get_settings()
    return {
        "apn_core_version": APN_CORE_VERSION,
        "protocol_version": APN_PROTOCOL_VERSION,
        "node_id": node_id,
        "nats_relay": settings.nats_relay,
    }


# ============= Peer Registration =============

@app.post("/register")
async def register_peer(
    registration: PeerRegistration,
    request: Request,
    database: APNDatabase = Depends(get_db),
):
    """Register a peer node with optional signature verification"""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"

    # Verify signature if provided (recommended for production)
    if registration.signature and registration.timestamp:
        if not verify_peer_signature(registration):
            logger.warning(f"Invalid signature from peer {registration.nodeId} at {client_ip}")
            await database.log_audit_event(
                "peer_registration",
                peer_id=registration.nodeId,
                details={"error": "invalid_signature"},
                ip_address=client_ip,
                success=False,
            )
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Check timestamp for replay protection (5 minute window)
        now = int(datetime.now().timestamp())
        if abs(now - registration.timestamp) > 300:
            logger.warning(f"Stale registration from peer {registration.nodeId}")
            raise HTTPException(status_code=401, detail="Registration timestamp too old")

    # Save peer to database
    capabilities = registration.settings.get('capabilities', {}) if registration.settings else {}
    await database.save_peer(
        node_id=registration.nodeId,
        public_key=registration.publicKey,
        roles=registration.roles or [],
        capabilities=capabilities,
        payment_address=registration.paymentAddress or "",
    )

    # Update in-memory cache
    peer = APNPeerNode(
        node_id=registration.nodeId,
        public_key=registration.publicKey,
        roles=registration.roles or [],
        capabilities=capabilities,
    )
    peers[registration.nodeId] = peer

    # Log audit event
    await database.log_audit_event(
        "peer_registration",
        peer_id=registration.nodeId,
        details={"roles": peer.roles, "capabilities": peer.capabilities},
        ip_address=client_ip,
        success=True,
    )

    logger.info(f"Registered peer: {registration.nodeId} from {client_ip}")
    logger.debug(f"  Roles: {peer.roles}")
    logger.debug(f"  Capabilities: {peer.capabilities}")

    return {
        "status": "registered",
        "dashboard_node_id": node_id,
        "timestamp": datetime.now().isoformat(),
    }


def verify_peer_signature(registration: PeerRegistration) -> bool:
    """Verify peer's Ed25519 signature on registration"""
    try:
        # Reconstruct signed data
        sign_data = json.dumps({
            "nodeId": registration.nodeId,
            "publicKey": registration.publicKey,
            "timestamp": registration.timestamp,
        }, sort_keys=True).encode()

        # Load peer's public key
        pub_key_bytes = bytes.fromhex(registration.publicKey)
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_key_bytes)

        # Verify signature
        signature = base64.b64decode(registration.signature)
        public_key.verify(signature, sign_data)

        return True
    except (InvalidSignature, ValueError, TypeError) as e:
        logger.debug(f"Signature verification failed: {e}")
        return False


# ============= Secure Channel =============

@app.post("/api/secure/handshake")
async def secure_handshake(
    message: HandshakeMessage,
    request: Request,
    database: APNDatabase = Depends(get_db),
):
    """Handle secure channel handshake with signature verification"""
    client_ip = request.client.host if request.client else "unknown"

    try:
        # Verify handshake signature
        sign_data = json.dumps({
            'ephemeral_key': message.ephemeral_key,
            'node_id': message.node_id,
            'public_key': message.public_key,
            'timestamp': message.timestamp,
            'type': message.type,
        }, sort_keys=True).encode()

        try:
            pub_key_bytes = base64.b64decode(message.public_key)
            peer_public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_key_bytes)
            signature = base64.b64decode(message.signature)
            peer_public_key.verify(signature, sign_data)
        except InvalidSignature:
            logger.warning(f"Invalid handshake signature from {message.node_id}")
            await database.log_audit_event(
                "handshake_failed",
                peer_id=message.node_id,
                details={"error": "invalid_signature"},
                ip_address=client_ip,
                success=False,
            )
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Check timestamp (5 minute window for replay protection)
        now = int(datetime.now().timestamp())
        if abs(now - message.timestamp) > 300:
            logger.warning(f"Stale handshake from {message.node_id}")
            raise HTTPException(status_code=401, detail="Handshake timestamp too old")

        # Generate ephemeral X25519 key for this session
        ephemeral_private = x25519.X25519PrivateKey.generate()
        ephemeral_public = ephemeral_private.public_key()

        # Decode peer's ephemeral key
        peer_ephemeral_bytes = base64.b64decode(message.ephemeral_key)
        peer_ephemeral = x25519.X25519PublicKey.from_public_bytes(peer_ephemeral_bytes)

        # Perform X25519 key exchange
        shared_secret = ephemeral_private.exchange(peer_ephemeral)

        # Derive session keys using HKDF
        sorted_ids = sorted([node_id, message.node_id])
        info = f"{sorted_ids[0]}:{sorted_ids[1]}".encode()

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=b"APN_SecureChannel_v1",
            info=info,
        )
        key_material = hkdf.derive(shared_secret)

        # Split into send/recv keys (swap for responder)
        recv_key = key_material[:32]
        send_key = key_material[32:64]

        # Store session in database
        await database.save_session(message.node_id, send_key, recv_key)

        # Update in-memory cache
        secure_sessions[message.node_id] = {
            'send_key': send_key,
            'recv_key': recv_key,
            'send_nonce': 0,
            'recv_nonce': 0,
            'created_at': datetime.now(),
        }

        # Create response
        timestamp = int(datetime.now().timestamp())

        # Sign response
        response_data = {
            'ephemeral_key': base64.b64encode(get_public_bytes(ephemeral_public)).decode(),
            'node_id': node_id,
            'public_key': base64.b64encode(get_public_bytes(node_public_key)).decode(),
            'timestamp': timestamp,
            'type': 'handshake_response',
        }
        sign_data = json.dumps(response_data, sort_keys=True).encode()
        signature = node_private_key.sign(sign_data)

        # Log audit event
        await database.log_audit_event(
            "handshake_success",
            peer_id=message.node_id,
            ip_address=client_ip,
            success=True,
        )

        logger.info(f"Secure session established with {message.node_id}")

        return {
            **response_data,
            'signature': base64.b64encode(signature).decode(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Handshake error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=400, detail="Handshake failed")


@app.post("/api/secure/message")
async def secure_message(
    message: SecureMessage,
    database: APNDatabase = Depends(get_db),
):
    """Handle encrypted message from peer"""
    peer_id = message.from_peer

    # Get session from cache or database
    session = secure_sessions.get(peer_id)
    if not session:
        db_session = await database.get_session(peer_id)
        if not db_session:
            logger.warning(f"No session found for peer {peer_id}")
            raise HTTPException(status_code=400, detail="No session with peer")
        session = {
            'send_key': db_session['send_key'],
            'recv_key': db_session['recv_key'],
            'send_nonce': db_session['send_nonce'],
            'recv_nonce': db_session['recv_nonce'],
            'created_at': db_session['created_at'],
        }
        secure_sessions[peer_id] = session

    try:
        # Decrypt
        encrypted = base64.b64decode(message.payload)
        if len(encrypted) < 28:  # 12 bytes nonce + 16 bytes tag minimum
            raise ValueError("Payload too short")

        nonce = encrypted[:12]
        ciphertext = encrypted[12:]

        cipher = ChaCha20Poly1305(session['recv_key'])
        plaintext = cipher.decrypt(nonce, ciphertext, None)

        # Validate and increment nonce
        received_nonce = int.from_bytes(nonce, 'big')
        if received_nonce < session['recv_nonce']:
            logger.warning(f"Replay attack detected from {peer_id}: nonce {received_nonce} < {session['recv_nonce']}")
            raise HTTPException(status_code=400, detail="Invalid nonce (replay detected)")

        session['recv_nonce'] = received_nonce + 1
        await database.update_session_nonce(peer_id, recv_nonce=session['recv_nonce'])

        data = json.loads(plaintext.decode())
        logger.debug(f"Decrypted message from {peer_id}: {data.get('type', 'unknown')}")

        # Handle message based on type
        response_data = await handle_peer_message(peer_id, data)

        # Encrypt response
        if response_data:
            response_json = json.dumps(response_data).encode()

            nonce_int = session['send_nonce']
            session['send_nonce'] += 1
            await database.update_session_nonce(peer_id, send_nonce=session['send_nonce'])

            nonce_bytes = nonce_int.to_bytes(12, 'big')
            cipher = ChaCha20Poly1305(session['send_key'])
            ct = cipher.encrypt(nonce_bytes, response_json, None)

            return {
                'type': 'secure_response',
                'payload': base64.b64encode(nonce_bytes + ct).decode(),
            }

        return {'status': 'ok'}

    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in decrypted message from {peer_id}: {e}")
        raise HTTPException(status_code=400, detail="Invalid message format")
    except Exception as e:
        logger.error(f"Secure message error from {peer_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=400, detail="Message processing failed")


async def handle_peer_message(peer_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle decrypted peer message"""
    msg_type = data.get('type')

    if msg_type == 'wearable_state':
        logger.info(f"Wearable state from {peer_id}: ring={data.get('ring_connected')}, glasses={data.get('glasses_connected')}")
        return {'type': 'ack', 'status': 'received'}

    elif msg_type == 'button_event':
        event_type = data.get('event_type')
        logger.info(f"Button event from {peer_id}: {event_type}")
        await forward_to_nora('button_event', data)
        return {'type': 'ack', 'status': 'processed'}

    elif msg_type == 'voice_command':
        logger.info(f"Voice command from {peer_id}: {data.get('text')}")
        response = await forward_to_nora('voice_command', data)
        return response

    return None


async def forward_to_nora(event_type: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Forward event to Nora backend"""
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if event_type == 'voice_command':
                response = await client.post(
                    f"{settings.nora_url}/api/chat",
                    json={
                        'message': data.get('text', ''),
                        'context': {'source': 'wearable', 'peer_id': data.get('peer_id')},
                    },
                )
                if response.status_code == 200:
                    return response.json()

            elif event_type == 'button_event':
                logger.debug(f"Button event forwarded: {data}")

        return None
    except httpx.TimeoutException:
        logger.warning(f"Timeout forwarding {event_type} to Nora")
        return None
    except httpx.RequestError as e:
        logger.warning(f"Error forwarding to Nora: {type(e).__name__}: {e}")
        return None


# ============= WebSocket =============

@app.websocket("/api/events/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time events"""
    await websocket.accept()

    peer_id = None
    try:
        while True:
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received on WebSocket")
                await websocket.send_text(json.dumps({'type': 'error', 'message': 'Invalid JSON'}))
                continue

            if message.get('type') == 'identify':
                peer_id = message.get('node_id')
                if peer_id:
                    websocket_connections[peer_id] = websocket
                    logger.info(f"WebSocket identified: {peer_id}")
                    await websocket.send_text(json.dumps({
                        'type': 'welcome',
                        'node_id': node_id,
                    }))

            elif message.get('type') == 'ping':
                await websocket.send_text(json.dumps({'type': 'pong'}))

    except WebSocketDisconnect:
        if peer_id and peer_id in websocket_connections:
            del websocket_connections[peer_id]
        logger.info(f"WebSocket disconnected: {peer_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {type(e).__name__}: {e}")
        if peer_id and peer_id in websocket_connections:
            del websocket_connections[peer_id]


# ============= Wearables =============

@app.post("/api/wearables/state")
async def wearable_state(state: WearableState):
    """Receive wearable state update"""
    logger.info(f"Wearable state update: ring={state.ring_connected}, glasses={state.glasses_connected}")
    return {"status": "received"}


@app.post("/api/wearables/button")
async def wearable_button(event: Dict[str, Any]):
    """Receive button event from wearable"""
    event_type = event.get('event_type')
    logger.info(f"Button event: {event_type}")
    await forward_to_nora('button_event', event)
    return {"status": "processed"}


# ============= Tasks =============

@app.get("/api/tasks")
async def get_tasks(
    assigned_to: Optional[str] = None,
    status: Optional[str] = None,
    database: APNDatabase = Depends(get_db),
):
    """Get tasks with optional filters"""
    settings = get_settings()

    # Try to fetch tasks from Nora first
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{settings.nora_url}/api/tasks",
                params={'assigned_to': assigned_to} if assigned_to else {},
            )
            if response.status_code == 200:
                nora_tasks = response.json()
                # Get local tasks from database
                local_tasks = await database.get_tasks(assigned_to=assigned_to, status=status)
                all_tasks = nora_tasks.get('tasks', []) + local_tasks
                return {"tasks": all_tasks, "source": "merged"}
    except httpx.RequestError as e:
        logger.debug(f"Nora unavailable for tasks: {e}")

    # Return local tasks from database
    tasks = await database.get_tasks(assigned_to=assigned_to, status=status)
    return {"tasks": tasks, "source": "local"}


@app.post("/api/tasks")
async def create_task(
    task: TaskCreate,
    database: APNDatabase = Depends(get_db),
):
    """Create a new task"""
    new_task = {
        "id": str(uuid.uuid4())[:8],
        "title": task.title,
        "description": task.description,
        "assigned_to": task.assigned_to,
        "priority": task.priority,
        "status": task.status,
        "due_date": task.due_date,
        "created_by": node_id,
    }

    created_task = await database.create_task(new_task)
    logger.info(f"Task created: {created_task['title']} (assigned to: {task.assigned_to})")

    # Sync to mesh peers
    for peer_url, peer_info in peer_connections.items():
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{peer_url}/api/tasks/sync",
                    json=created_task,
                )
        except httpx.RequestError as e:
            logger.debug(f"Failed to sync task to {peer_url}: {e}")

    return {"status": "created", "task": created_task}


@app.post("/api/tasks/sync")
async def sync_task(
    task: Dict[str, Any],
    request: Request,
    database: APNDatabase = Depends(get_db),
):
    """Receive synced task from mesh peer"""
    client_ip = request.client.host if request.client else "unknown"

    # Validate required fields
    if not task.get('id') or not task.get('title'):
        raise HTTPException(status_code=400, detail="Missing required task fields")

    synced = await database.sync_task(task, synced_from=client_ip)

    if synced:
        logger.info(f"Task synced from mesh: {task.get('title')}")

    return {"status": "synced" if synced else "duplicate"}


@app.patch("/api/tasks/{task_id}")
async def update_task(
    task_id: str,
    updates: TaskUpdate,
    database: APNDatabase = Depends(get_db),
):
    """Update a task"""
    update_dict = updates.model_dump(exclude_unset=True)
    updated_task = await database.update_task(task_id, update_dict)

    if not updated_task:
        raise HTTPException(status_code=404, detail="Task not found")

    logger.info(f"Task updated: {task_id}")
    return {"status": "updated", "task": updated_task}


# ============= System Resources & Contribution =============

def get_system_resources() -> Dict[str, Any]:
    """Get system resource information for device contribution"""
    resources = {
        "cpu": {
            "cores": os.cpu_count() or 1,
            "usage_percent": 0.0,
            "model": platform.processor() or "Unknown",
        },
        "memory": {
            "total_gb": 0,
            "available_gb": 0,
            "used_percent": 0.0,
        },
        "storage": {
            "total_gb": 0,
            "available_gb": 0,
            "used_percent": 0.0,
        },
        "gpu": {
            "available": False,
            "name": None,
            "memory_gb": 0,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }
    }

    if PSUTIL_AVAILABLE:
        resources["cpu"]["usage_percent"] = psutil.cpu_percent(interval=0.1)

        mem = psutil.virtual_memory()
        resources["memory"]["total_gb"] = round(mem.total / (1024**3), 2)
        resources["memory"]["available_gb"] = round(mem.available / (1024**3), 2)
        resources["memory"]["used_percent"] = mem.percent

        try:
            disk = psutil.disk_usage('/')
            resources["storage"]["total_gb"] = round(disk.total / (1024**3), 2)
            resources["storage"]["available_gb"] = round(disk.free / (1024**3), 2)
            resources["storage"]["used_percent"] = disk.percent
        except OSError as e:
            logger.debug(f"Could not get disk usage: {e}")

    # Try to detect GPU
    try:
        import subprocess
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(',')
            if len(parts) >= 2:
                resources["gpu"]["available"] = True
                resources["gpu"]["name"] = parts[0].strip()
                resources["gpu"]["memory_gb"] = round(int(parts[1].strip()) / 1024, 2)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        logger.debug(f"GPU detection failed: {e}")

    return resources


@app.get("/api/resources")
async def get_resources():
    """Get current system resources"""
    return {
        "node_id": node_id,
        "resources": get_system_resources(),
        "apn_core_version": APN_CORE_VERSION,
        "protocol_version": APN_PROTOCOL_VERSION,
    }


@app.get("/api/contribution/status")
async def get_contribution_status(database: APNDatabase = Depends(get_db)):
    """Get current device contribution status"""
    settings = get_settings()
    contribution = await database.get_setting("contribution_settings") or settings.get_contribution_settings()
    resources = get_system_resources()

    return {
        "node_id": node_id,
        "settings": contribution,
        "resources": resources,
        "mesh_peers": len(peer_connections),
        "relay_url": settings.nats_relay,
        "status": "contributing" if contribution.get("enabled") else "idle",
    }


@app.post("/api/contribution/settings")
async def update_contribution_settings(
    settings_update: ContributionSettings,
    database: APNDatabase = Depends(get_db),
):
    """Update device contribution settings"""
    contribution = settings_update.model_dump()

    # Save to database
    await database.save_setting("contribution_settings", contribution)

    logger.info(f"Contribution settings updated: enabled={settings_update.enabled}")
    logger.debug(f"  Relay: {settings_update.relay}, Compute: {settings_update.compute}, Storage: {settings_update.storage}")

    return {"status": "updated", "settings": contribution}


# ============= Mesh Peering =============

async def connect_to_mesh_peers():
    """Connect to known APN peers for mesh networking"""
    settings = get_settings()
    await asyncio.sleep(2)  # Wait for server to fully start

    for peer_url in settings.known_peers:
        asyncio.create_task(connect_to_peer(peer_url))


async def connect_to_peer(peer_url: str):
    """Establish connection with a mesh peer"""
    settings = get_settings()
    database = await get_database()

    try:
        logger.info(f"Attempting mesh connection to: {peer_url}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if peer is online
            try:
                response = await client.get(f"{peer_url}/health")
                if response.status_code != 200:
                    logger.warning(f"Peer {peer_url} not healthy: {response.status_code}")
                    await schedule_peer_retry(peer_url)
                    return
            except httpx.RequestError as e:
                logger.warning(f"Peer {peer_url} unreachable: {e}")
                await schedule_peer_retry(peer_url)
                return

            # Get peer info
            peer_node_id = None
            try:
                health_data = response.json()
                peer_node_id = health_data.get('node_id', 'unknown')
            except json.JSONDecodeError:
                pass

            # Register ourselves with the peer
            pub_bytes = get_public_bytes(node_public_key)
            registration = {
                'nodeId': node_id,
                'publicKey': pub_bytes.hex(),
                'paymentAddress': '',
                'roles': ['sovereign_node', 'wearable_hub'],
                'settings': {
                    'capabilities': {
                        'mesh_relay': True,
                        'wearables': True,
                    },
                    'device_name': 'Sovereign Stack Node',
                }
            }

            try:
                reg_response = await client.post(
                    f"{peer_url}/register",
                    json=registration,
                )

                if reg_response.status_code == 200:
                    reg_data = reg_response.json()
                    peer_node_id = reg_data.get('dashboard_node_id', peer_node_id)

                    peer_connections[peer_url] = {
                        'node_id': peer_node_id,
                        'status': 'connected',
                        'connected_at': datetime.now().isoformat(),
                        'url': peer_url,
                    }

                    # Save to database
                    await database.save_peer_connection(peer_url, peer_node_id, "connected")

                    logger.info(f"Mesh connected to peer: {peer_node_id} at {peer_url}")

                    # Start keep-alive
                    asyncio.create_task(peer_keepalive(peer_url))
                else:
                    logger.warning(f"Peer registration failed: {reg_response.status_code}")
                    await schedule_peer_retry(peer_url)

            except httpx.RequestError as e:
                logger.warning(f"Peer registration error: {e}")
                await schedule_peer_retry(peer_url)

    except Exception as e:
        logger.error(f"Mesh connection error for {peer_url}: {type(e).__name__}: {e}")
        await schedule_peer_retry(peer_url)


async def peer_keepalive(peer_url: str):
    """Send periodic keepalives to mesh peer"""
    database = await get_database()

    while peer_url in peer_connections:
        await asyncio.sleep(30)

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{peer_url}/health")
                if response.status_code != 200:
                    logger.warning(f"Peer {peer_url} health check failed")
                    del peer_connections[peer_url]
                    await database.update_peer_connection_status(peer_url, "disconnected")
                    await schedule_peer_retry(peer_url)
                    return
                else:
                    await database.update_peer_connection_status(peer_url, "connected")

        except httpx.RequestError as e:
            logger.warning(f"Peer keepalive failed for {peer_url}: {e}")
            if peer_url in peer_connections:
                del peer_connections[peer_url]
            await database.update_peer_connection_status(peer_url, "disconnected", increment_retry=True)
            await schedule_peer_retry(peer_url)
            return


async def schedule_peer_retry(peer_url: str):
    """Schedule retry connection to peer"""
    async def retry():
        await asyncio.sleep(30)
        await connect_to_peer(peer_url)

    asyncio.create_task(retry())


@app.get("/api/mesh/peers")
async def get_mesh_peers(database: APNDatabase = Depends(get_db)):
    """Get list of connected mesh peers"""
    settings = get_settings()

    return {
        'node_id': node_id,
        'peers': list(peer_connections.values()),
        'known_peers': settings.known_peers,
    }


@app.post("/api/mesh/message")
async def mesh_message(message: MeshMessage):
    """Forward a message to the mesh network"""
    dest_node = message.dest_node
    payload = message.payload

    # Find peer that can reach destination
    for peer_url, peer_info in peer_connections.items():
        if peer_info.get('node_id') == dest_node or dest_node == 'broadcast':
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        f"{peer_url}/api/mesh/relay",
                        json={
                            'source_node': node_id,
                            'dest_node': dest_node,
                            'payload': payload,
                            'hop_count': message.hop_count + 1,
                        },
                    )
                    if response.status_code == 200:
                        return {'status': 'relayed', 'via': peer_info.get('node_id')}
            except httpx.RequestError as e:
                logger.warning(f"Mesh relay failed: {e}")

    return {'status': 'no_route', 'dest_node': dest_node}


@app.post("/api/mesh/relay")
async def mesh_relay(message: Dict[str, Any]):
    """Handle relayed mesh message"""
    source_node = message.get('source_node')
    dest_node = message.get('dest_node')
    payload = message.get('payload')
    hop_count = message.get('hop_count', 0)

    if not source_node or not payload:
        raise HTTPException(status_code=400, detail="Missing required fields")

    logger.info(f"Mesh relay from {source_node}: {payload.get('type', 'unknown')} (hops: {hop_count})")

    # If broadcast or destined for us, process locally
    if dest_node == 'broadcast' or dest_node == node_id:
        await handle_mesh_payload(source_node, payload)

    # Forward to local wearables if applicable
    for ws in websocket_connections.values():
        try:
            await ws.send_text(json.dumps({
                'type': 'mesh_message',
                'source': source_node,
                'data': payload,
            }))
        except Exception as e:
            logger.debug(f"Failed to forward to WebSocket: {e}")

    return {'status': 'received'}


# ============= PCG Dashboard Bridge =============

class PCGTaskDistribution(BaseModel):
    """Task distribution request from PCG Dashboard"""
    task_id: str = Field(..., description="UUID of the task")
    task_attempt_id: str = Field(..., description="UUID of the task attempt")
    executor_profile: str = Field(..., description="Executor profile name")
    prompt: str = Field(..., description="Task prompt")
    project_id: str = Field(..., description="Project UUID")
    project_path: Optional[str] = None
    resource_requirements: Optional[Dict[str, Any]] = Field(default_factory=dict)
    reward_vibe: float = Field(default=10.0, ge=0)


class PCGExecutionUpdate(BaseModel):
    """Execution update from remote node"""
    task_id: str
    execution_process_id: str
    stage: str = Field(..., pattern="^(setup|coding|testing|review|cleanup|completed|failed)$")
    progress_percent: int = Field(ge=0, le=100)
    current_action: str = ""
    files_modified: int = 0
    error: Optional[str] = None


@app.post("/api/pcg/distribute")
async def pcg_distribute_task(
    distribution: PCGTaskDistribution,
    request: Request,
    database: APNDatabase = Depends(get_db),
):
    """
    Receive task distribution request from PCG Dashboard.
    Finds capable peers and assigns the task.
    """
    client_ip = request.client.host if request.client else "unknown"
    logger.info(f"PCG task distribution from {client_ip}: {distribution.task_id}")

    # Find capable peers based on requirements
    capable_peers = []
    for peer_url, peer_info in peer_connections.items():
        if peer_info.get('status') == 'connected':
            capable_peers.append({
                'node_id': peer_info.get('node_id'),
                'url': peer_url,
            })

    # If no remote peers, assign to local
    if not capable_peers:
        assigned_node = node_id
        logger.info(f"No remote peers available, assigning task {distribution.task_id} locally")
    else:
        # Select first available peer (in production, use reputation/latency scoring)
        assigned_peer = capable_peers[0]
        assigned_node = assigned_peer['node_id']

        # Forward task to assigned peer
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{assigned_peer['url']}/api/pcg/execute",
                    json={
                        'task_id': distribution.task_id,
                        'task_attempt_id': distribution.task_attempt_id,
                        'executor_profile': distribution.executor_profile,
                        'prompt': distribution.prompt,
                        'project_id': distribution.project_id,
                        'project_path': distribution.project_path,
                        'reward_vibe': distribution.reward_vibe,
                        'from_node': node_id,
                    }
                )
        except httpx.RequestError as e:
            logger.warning(f"Failed to forward task to {assigned_node}: {e}")
            assigned_node = node_id  # Fall back to local

    # Record audit
    await database.log_audit_event(
        "pcg_task_distributed",
        peer_id=assigned_node,
        details={
            "task_id": distribution.task_id,
            "executor_profile": distribution.executor_profile,
            "reward_vibe": distribution.reward_vibe,
        },
        ip_address=client_ip,
        success=True,
    )

    return {
        'status': 'distributed',
        'task_id': distribution.task_id,
        'assigned_node': assigned_node,
        'timestamp': datetime.now().isoformat(),
    }


@app.post("/api/pcg/execute")
async def pcg_execute_task(
    task: Dict[str, Any],
    request: Request,
):
    """
    Receive task execution request from another APN node.
    This node will execute the task and stream results back.
    """
    task_id = task.get('task_id')
    from_node = task.get('from_node')

    logger.info(f"Received task execution request {task_id} from {from_node}")

    # Broadcast to WebSocket clients that a task is available
    for ws in websocket_connections.values():
        try:
            await ws.send_text(json.dumps({
                'type': 'task_received',
                'task_id': task_id,
                'from_node': from_node,
                'executor_profile': task.get('executor_profile'),
                'prompt': task.get('prompt', '')[:200],  # Preview
                'reward_vibe': task.get('reward_vibe', 0),
            }))
        except Exception as e:
            logger.debug(f"Failed to notify WebSocket: {e}")

    return {
        'status': 'accepted',
        'task_id': task_id,
        'executor_node': node_id,
    }


@app.post("/api/pcg/execution/update")
async def pcg_execution_update(
    update: PCGExecutionUpdate,
    request: Request,
):
    """
    Receive execution progress update.
    Forward to WebSocket clients and other interested parties.
    """
    logger.info(f"Execution update for {update.task_id}: {update.stage} ({update.progress_percent}%)")

    # Broadcast to WebSocket clients
    for ws in websocket_connections.values():
        try:
            await ws.send_text(json.dumps({
                'type': 'execution_progress',
                'task_id': update.task_id,
                'execution_process_id': update.execution_process_id,
                'stage': update.stage,
                'progress_percent': update.progress_percent,
                'current_action': update.current_action,
                'files_modified': update.files_modified,
            }))
        except Exception as e:
            logger.debug(f"Failed to broadcast execution update: {e}")

    return {'status': 'received'}


@app.post("/api/pcg/execution/log")
async def pcg_execution_log(
    log: Dict[str, Any],
    request: Request,
):
    """
    Receive execution log chunk.
    Forward to WebSocket clients for real-time display.
    """
    task_id = log.get('task_id')
    execution_id = log.get('execution_process_id')
    log_type = log.get('log_type', 'system')
    content = log.get('content', '')

    # Broadcast to WebSocket clients
    for ws in websocket_connections.values():
        try:
            await ws.send_text(json.dumps({
                'type': 'execution_log',
                'task_id': task_id,
                'execution_process_id': execution_id,
                'log_type': log_type,
                'content': content,
                'timestamp': datetime.now().isoformat(),
            }))
        except Exception as e:
            logger.debug(f"Failed to broadcast log: {e}")

    return {'status': 'received'}


@app.get("/api/pcg/status")
async def pcg_bridge_status():
    """Get PCG bridge connection status"""
    return {
        'node_id': node_id,
        'connected_peers': len(peer_connections),
        'active_websockets': len(websocket_connections),
        'nats_relay': get_settings().nats_relay,
        'bridge_version': '1.0.0',
        'capabilities': ['task_distribution', 'execution_relay', 'log_streaming'],
    }


async def handle_mesh_payload(source_node: str, payload: Dict[str, Any]):
    """Handle mesh message payload locally"""
    msg_type = payload.get('type')

    if msg_type == 'ping':
        logger.info(f"Mesh ping from {source_node}")
    elif msg_type == 'task_assignment':
        logger.info(f"Task assignment from {source_node}: {payload.get('task')}")
        for ws in websocket_connections.values():
            try:
                await ws.send_text(json.dumps({
                    'type': 'task',
                    'data': payload.get('task'),
                }))
            except Exception as e:
                logger.debug(f"Failed to forward task to WebSocket: {e}")


# ============= Landing Page =============

@app.get("/")
async def landing_page():
    """APN CORE landing page"""
    resources = get_system_resources()
    peers_count = len(peer_connections)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>APN CORE - Alpha Protocol Network</title>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: linear-gradient(135deg, #0a0a0a, #1a1a2e);
                color: #FFD700;
                margin: 0;
                padding: 2rem;
                min-height: 100vh;
            }}
            .container {{ max-width: 900px; margin: 0 auto; }}
            .header {{ text-align: center; margin-bottom: 2rem; }}
            .logo {{ font-size: 4rem; margin-bottom: 0.5rem; }}
            .version {{ color: #888; font-size: 0.9rem; }}
            .card {{
                background: rgba(255, 215, 0, 0.08);
                border: 1px solid rgba(255, 215, 0, 0.3);
                border-radius: 12px;
                padding: 1.5rem;
                margin: 1rem 0;
            }}
            .card h2 {{ margin-top: 0; color: #FFD700; }}
            .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
            .stat {{ background: rgba(0,0,0,0.3); padding: 1rem; border-radius: 8px; }}
            .stat-value {{ font-size: 1.5rem; font-weight: bold; }}
            .stat-label {{ color: #aaa; font-size: 0.85rem; }}
            .online {{ color: #00ff88; }}
            .offline {{ color: #ff4444; }}
            a {{ color: #FFD700; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">A</div>
                <h1>APN CORE</h1>
                <p>Alpha Protocol Network - Sovereign Mesh Node</p>
                <p class="version">v{APN_CORE_VERSION} | Protocol: {APN_PROTOCOL_VERSION}</p>
            </div>

            <div class="card">
                <h2>Node Status</h2>
                <div class="stat-grid">
                    <div class="stat">
                        <div class="stat-value">{node_id[:16]}...</div>
                        <div class="stat-label">Node ID</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value online">Online</div>
                        <div class="stat-label">Status</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{peers_count}</div>
                        <div class="stat-label">Mesh Peers</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>System Resources</h2>
                <div class="stat-grid">
                    <div class="stat">
                        <div class="stat-value">{resources['cpu']['cores']} cores</div>
                        <div class="stat-label">CPU ({resources['cpu']['usage_percent']:.1f}% used)</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{resources['memory']['total_gb']} GB</div>
                        <div class="stat-label">RAM ({resources['memory']['used_percent']:.1f}% used)</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{resources['storage']['available_gb']} GB</div>
                        <div class="stat-label">Storage Available</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{'Yes' if resources['gpu']['available'] else 'No'}</div>
                        <div class="stat-label">GPU {resources['gpu']['name'] or ''}</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>API Endpoints</h2>
                <ul>
                    <li><a href="/health">/health</a> - Health check</li>
                    <li><a href="/api/resources">/api/resources</a> - System resources</li>
                    <li><a href="/api/contribution/status">/api/contribution/status</a> - Contribution status</li>
                    <li><a href="/api/mesh/peers">/api/mesh/peers</a> - Mesh peers</li>
                    <li><a href="/docs">/docs</a> - API documentation</li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# ============= Main =============

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
