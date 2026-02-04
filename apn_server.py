#!/usr/bin/env python3
"""
APN Core Server - Alpha Protocol Network Core Services
Main entry point for APN Dashboard providing mesh networking,
peer registration, wearable integration, and device contribution.

Version: 1.0.0
"""

import asyncio
import base64
import json
import os
import platform
import secrets
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
import httpx

# Cryptography for secure channel
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

# System resources
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# APN Core Version
APN_CORE_VERSION = "1.0.0"
APN_PROTOCOL_VERSION = "alpha/1.0.0"

def get_public_bytes(key):
    """Get raw public key bytes (compatible with different cryptography versions)"""
    if hasattr(key, 'public_bytes_raw'):
        return key.public_bytes_raw()
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

def get_private_bytes(key):
    """Get raw private key bytes (compatible with different cryptography versions)"""
    if hasattr(key, 'private_bytes_raw'):
        return key.private_bytes_raw()
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())

NORA_URL = "http://127.0.0.1:3003"

# Default NATS relay for mesh networking
DEFAULT_NATS_RELAY = "nats://nonlocal.info:4222"

# Known peers for mesh networking (Pythia master node)
KNOWN_PEERS = [
    "https://dashboard.powerclubglobal.com",
    "https://pythia.nonlocal.info",
]

# Peer connection state
peer_connections: Dict[str, Dict[str, Any]] = {}

app = FastAPI(
    title="APN Core Server",
    description="Alpha Protocol Network - Sovereign mesh networking for device contribution",
    version=APN_CORE_VERSION
)

# CORS for cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============= Data Models =============

@dataclass
class APNPeerNode:
    """Represents a connected APN peer node"""
    node_id: str
    public_key: str
    roles: List[str] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    connected_at: datetime = field(default_factory=datetime.now)
    websocket: Optional[WebSocket] = None

class PeerRegistration(BaseModel):
    nodeId: str
    publicKey: str
    paymentAddress: Optional[str] = ""
    roles: Optional[List[str]] = []
    settings: Optional[Dict[str, Any]] = {}

class HandshakeMessage(BaseModel):
    type: str
    node_id: str
    public_key: str
    ephemeral_key: str
    timestamp: int
    signature: str

# ============= Global State =============

# Node identity (generated on startup)
node_private_key: Optional[ed25519.Ed25519PrivateKey] = None
node_public_key: Optional[ed25519.Ed25519PublicKey] = None
node_id: str = ""

# Connected peers
peers: Dict[str, APNPeerNode] = {}

# Secure sessions
secure_sessions: Dict[str, Dict[str, Any]] = {}

# WebSocket connections
websocket_connections: Dict[str, WebSocket] = {}

# ============= Initialization =============

def generate_node_identity():
    """Generate Ed25519 keypair for node identity"""
    global node_private_key, node_public_key, node_id

    # Check for existing identity
    identity_file = os.path.expanduser("~/.apn_bridge_identity.json")
    if os.path.exists(identity_file):
        try:
            with open(identity_file, 'r') as f:
                data = json.load(f)
            seed = bytes.fromhex(data['seed'])
            node_private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
            node_public_key = node_private_key.public_key()
            node_id = data['node_id']
            print(f"Loaded identity: {node_id}")
            return
        except Exception as e:
            print(f"Failed to load identity: {e}")

    # Generate new identity
    node_private_key = ed25519.Ed25519PrivateKey.generate()
    node_public_key = node_private_key.public_key()

    # Generate node_id from public key
    pub_bytes = get_public_bytes(node_public_key)
    node_id = f"apn_{pub_bytes[:8].hex()}"

    # Save identity
    seed = get_private_bytes(node_private_key)
    try:
        with open(identity_file, 'w') as f:
            json.dump({'seed': seed.hex(), 'node_id': node_id}, f)
        print(f"Generated new identity: {node_id}")
    except Exception as e:
        print(f"Warning: Could not save identity: {e}")

@app.on_event("startup")
async def startup():
    global contribution_settings

    generate_node_identity()
    print(f"")
    print(f"  ╔═══════════════════════════════════════════════════╗")
    print(f"  ║           APN Core v{APN_CORE_VERSION}                         ║")
    print(f"  ║   Alpha Protocol Network - Sovereign Mesh Node    ║")
    print(f"  ╚═══════════════════════════════════════════════════╝")
    print(f"")
    print(f"  Node ID: {node_id}")
    print(f"  NATS Relay: {DEFAULT_NATS_RELAY}")
    print(f"  Nora Backend: {NORA_URL}")
    print(f"")

    # Load contribution settings if exists
    config_path = os.path.expanduser("~/.apn/contribution_settings.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                contribution_settings = json.load(f)
            print(f"  Loaded contribution settings: enabled={contribution_settings.get('enabled', False)}")
        except Exception as e:
            print(f"  Warning: Could not load contribution settings: {e}")

    # Start mesh peer connections in background
    asyncio.create_task(connect_to_mesh_peers())

# ============= API Endpoints =============

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok", "node_id": node_id}

@app.post("/register")
async def register_peer(registration: PeerRegistration):
    """Register a peer node"""
    peer = APNPeerNode(
        node_id=registration.nodeId,
        public_key=registration.publicKey,
        roles=registration.roles or [],
        capabilities=registration.settings.get('capabilities', {}) if registration.settings else {},
    )
    peers[registration.nodeId] = peer

    print(f"Registered peer: {registration.nodeId}")
    print(f"  Roles: {peer.roles}")
    print(f"  Capabilities: {peer.capabilities}")

    return {
        "status": "registered",
        "dashboard_node_id": node_id,
        "timestamp": datetime.now().isoformat(),
    }

@app.post("/api/secure/handshake")
async def secure_handshake(message: HandshakeMessage):
    """Handle secure channel handshake"""
    try:
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

        # Store session
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
        sign_data = json.dumps({
            'ephemeral_key': base64.b64encode(get_public_bytes(ephemeral_public)).decode(),
            'node_id': node_id,
            'public_key': base64.b64encode(get_public_bytes(node_public_key)).decode(),
            'timestamp': timestamp,
            'type': 'handshake_response',
        }, sort_keys=True).encode()

        signature = node_private_key.sign(sign_data)

        print(f"Secure session established with {message.node_id}")

        return {
            'type': 'handshake_response',
            'node_id': node_id,
            'public_key': base64.b64encode(get_public_bytes(node_public_key)).decode(),
            'ephemeral_key': base64.b64encode(get_public_bytes(ephemeral_public)).decode(),
            'timestamp': timestamp,
            'signature': base64.b64encode(signature).decode(),
        }

    except Exception as e:
        print(f"Handshake error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/secure/message")
async def secure_message(message: Dict[str, Any]):
    """Handle encrypted message from peer"""
    try:
        peer_id = message.get('from')
        payload_b64 = message.get('payload')

        if not peer_id or not payload_b64:
            raise HTTPException(status_code=400, detail="Missing from or payload")

        session = secure_sessions.get(peer_id)
        if not session:
            raise HTTPException(status_code=400, detail="No session with peer")

        # Decrypt
        encrypted = base64.b64decode(payload_b64)
        nonce = encrypted[:12]
        ciphertext = encrypted[12:-16]
        tag = encrypted[-16:]

        cipher = ChaCha20Poly1305(session['recv_key'])
        plaintext = cipher.decrypt(nonce, ciphertext + tag, None)

        data = json.loads(plaintext.decode())
        print(f"Decrypted message from {peer_id}: {data.get('type', 'unknown')}")

        # Handle message based on type
        response_data = await handle_peer_message(peer_id, data)

        # Encrypt response
        if response_data:
            response_json = json.dumps(response_data).encode()

            nonce_int = session['send_nonce']
            session['send_nonce'] += 1

            nonce_bytes = nonce_int.to_bytes(12, 'big')
            cipher = ChaCha20Poly1305(session['send_key'])
            ct = cipher.encrypt(nonce_bytes, response_json, None)

            return {
                'type': 'secure_response',
                'payload': base64.b64encode(nonce_bytes + ct).decode(),
            }

        return {'status': 'ok'}

    except Exception as e:
        print(f"Secure message error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

async def handle_peer_message(peer_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle decrypted peer message"""
    msg_type = data.get('type')

    if msg_type == 'wearable_state':
        print(f"Wearable state from {peer_id}: ring={data.get('ring_connected')}, glasses={data.get('glasses_connected')}")
        return {'type': 'ack', 'status': 'received'}

    elif msg_type == 'button_event':
        event_type = data.get('event_type')
        print(f"Button event from {peer_id}: {event_type}")
        # Forward to Nora for processing
        await forward_to_nora('button_event', data)
        return {'type': 'ack', 'status': 'processed'}

    elif msg_type == 'voice_command':
        print(f"Voice command from {peer_id}: {data.get('text')}")
        # Forward to Nora
        response = await forward_to_nora('voice_command', data)
        return response

    return None

async def forward_to_nora(event_type: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Forward event to Nora backend"""
    try:
        async with httpx.AsyncClient() as client:
            # Try Nora's chat endpoint for voice commands
            if event_type == 'voice_command':
                response = await client.post(
                    f"{NORA_URL}/api/chat",
                    json={
                        'message': data.get('text', ''),
                        'context': {'source': 'wearable', 'peer_id': data.get('peer_id')},
                    },
                    timeout=30.0,
                )
                if response.status_code == 200:
                    return response.json()

            # Log button events
            elif event_type == 'button_event':
                print(f"Button event forwarded: {data}")

        return None
    except Exception as e:
        print(f"Error forwarding to Nora: {e}")
        return None

@app.websocket("/api/events/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time events"""
    await websocket.accept()

    peer_id = None
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message.get('type') == 'identify':
                peer_id = message.get('node_id')
                if peer_id:
                    websocket_connections[peer_id] = websocket
                    print(f"WebSocket identified: {peer_id}")
                    await websocket.send_text(json.dumps({
                        'type': 'welcome',
                        'node_id': node_id,
                    }))

            elif message.get('type') == 'ping':
                await websocket.send_text(json.dumps({'type': 'pong'}))

    except WebSocketDisconnect:
        if peer_id and peer_id in websocket_connections:
            del websocket_connections[peer_id]
        print(f"WebSocket disconnected: {peer_id}")
    except Exception as e:
        print(f"WebSocket error: {e}")

@app.post("/api/wearables/state")
async def wearable_state(state: Dict[str, Any]):
    """Receive wearable state update"""
    print(f"Wearable state update: {state}")
    return {"status": "received"}

# Local task storage
local_tasks: List[Dict[str, Any]] = []

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    assigned_to: Optional[str] = ""
    priority: Optional[str] = "medium"
    status: Optional[str] = "pending"
    due_date: Optional[str] = None

@app.get("/api/tasks")
async def get_tasks(assigned_to: Optional[str] = None):
    """Get tasks assigned to a node"""
    # Try to fetch tasks from Nora first
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{NORA_URL}/api/tasks",
                params={'assigned_to': assigned_to} if assigned_to else {},
                timeout=5.0,
            )
            if response.status_code == 200:
                nora_tasks = response.json()
                # Merge with local tasks
                all_tasks = nora_tasks.get('tasks', []) + local_tasks
                if assigned_to:
                    all_tasks = [t for t in all_tasks if t.get('assigned_to') == assigned_to]
                return {"tasks": all_tasks, "source": "merged"}
    except Exception as e:
        print(f"Failed to fetch tasks from Nora: {e}")

    # Return local tasks if Nora unavailable
    tasks = local_tasks
    if assigned_to:
        tasks = [t for t in tasks if t.get('assigned_to') == assigned_to]
    return {"tasks": tasks, "source": "local"}

@app.post("/api/tasks")
async def create_task(task: TaskCreate):
    """Create a new task"""
    import uuid

    new_task = {
        "id": str(uuid.uuid4())[:8],
        "title": task.title,
        "description": task.description,
        "assigned_to": task.assigned_to,
        "priority": task.priority,
        "status": task.status,
        "due_date": task.due_date,
        "created_at": datetime.now().isoformat(),
        "created_by": node_id,
    }

    local_tasks.append(new_task)
    print(f"Task created: {new_task['title']} (assigned to: {task.assigned_to})")

    # Try to sync to mesh peers
    for peer_url, peer_info in peer_connections.items():
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{peer_url}/api/tasks/sync",
                    json=new_task,
                    timeout=5.0,
                )
        except:
            pass

    return {"status": "created", "task": new_task}

@app.post("/api/tasks/sync")
async def sync_task(task: Dict[str, Any]):
    """Receive synced task from mesh peer"""
    # Check if task already exists
    if not any(t.get('id') == task.get('id') for t in local_tasks):
        local_tasks.append(task)
        print(f"Task synced from mesh: {task.get('title')}")
    return {"status": "synced"}

@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: str, updates: Dict[str, Any]):
    """Update a task"""
    for task in local_tasks:
        if task.get('id') == task_id:
            task.update(updates)
            task['updated_at'] = datetime.now().isoformat()
            print(f"Task updated: {task_id}")
            return {"status": "updated", "task": task}

    raise HTTPException(status_code=404, detail="Task not found")

@app.post("/api/wearables/button")
async def wearable_button(event: Dict[str, Any]):
    """Receive button event from wearable"""
    event_type = event.get('event_type')
    print(f"Button event: {event_type}")

    # Forward to Nora
    await forward_to_nora('button_event', event)

    return {"status": "processed"}

# ============= System Resources & Device Contribution =============

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
        # CPU usage
        resources["cpu"]["usage_percent"] = psutil.cpu_percent(interval=0.1)

        # Memory
        mem = psutil.virtual_memory()
        resources["memory"]["total_gb"] = round(mem.total / (1024**3), 2)
        resources["memory"]["available_gb"] = round(mem.available / (1024**3), 2)
        resources["memory"]["used_percent"] = mem.percent

        # Storage (root partition)
        try:
            disk = psutil.disk_usage('/')
            resources["storage"]["total_gb"] = round(disk.total / (1024**3), 2)
            resources["storage"]["available_gb"] = round(disk.free / (1024**3), 2)
            resources["storage"]["used_percent"] = disk.percent
        except:
            pass

    # Try to detect GPU (basic detection)
    try:
        import subprocess
        result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            parts = result.stdout.strip().split(',')
            if len(parts) >= 2:
                resources["gpu"]["available"] = True
                resources["gpu"]["name"] = parts[0].strip()
                resources["gpu"]["memory_gb"] = round(int(parts[1].strip()) / 1024, 2)
    except:
        pass

    return resources

# Device contribution settings (stored in memory, persisted to config)
contribution_settings: Dict[str, Any] = {
    "enabled": False,
    "relay": False,
    "compute": False,
    "storage": False,
    "storage_gb_allocated": 10,
    "compute_cores_allocated": 1,
    "bandwidth_limit_mbps": 100,
}

class ContributionSettings(BaseModel):
    enabled: bool = False
    relay: bool = False
    compute: bool = False
    storage: bool = False
    storage_gb_allocated: int = 10
    compute_cores_allocated: int = 1
    bandwidth_limit_mbps: int = 100

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
async def get_contribution_status():
    """Get current device contribution status"""
    resources = get_system_resources()
    return {
        "node_id": node_id,
        "settings": contribution_settings,
        "resources": resources,
        "mesh_peers": len(peer_connections),
        "relay_url": DEFAULT_NATS_RELAY,
        "status": "contributing" if contribution_settings["enabled"] else "idle",
    }

@app.post("/api/contribution/settings")
async def update_contribution_settings(settings: ContributionSettings):
    """Update device contribution settings"""
    global contribution_settings
    contribution_settings = settings.model_dump()

    print(f"Contribution settings updated: enabled={settings.enabled}")
    print(f"  Relay: {settings.relay}, Compute: {settings.compute}, Storage: {settings.storage}")

    # Save to config file
    config_path = os.path.expanduser("~/.apn/contribution_settings.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(contribution_settings, f, indent=2)

    return {"status": "updated", "settings": contribution_settings}

@app.get("/")
async def landing_page():
    """APN Core landing page"""
    resources = get_system_resources()
    peers_count = len(peer_connections)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>APN Core - Alpha Protocol Network</title>
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
                <div class="logo">Α</div>
                <h1>APN Core</h1>
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
                        <div class="stat-value class="online">Online</div>
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

@app.get("/api/version")
async def get_version():
    """Get APN Core version information"""
    return {
        "apn_core_version": APN_CORE_VERSION,
        "protocol_version": APN_PROTOCOL_VERSION,
        "node_id": node_id,
        "nats_relay": DEFAULT_NATS_RELAY,
    }

# ============= Mesh Peering =============

async def connect_to_mesh_peers():
    """Connect to known APN peers for mesh networking"""
    await asyncio.sleep(2)  # Wait for server to fully start

    for peer_url in KNOWN_PEERS:
        asyncio.create_task(connect_to_peer(peer_url))

async def connect_to_peer(peer_url: str):
    """Establish connection with a mesh peer"""
    try:
        print(f"Attempting mesh connection to: {peer_url}")

        # Check if peer is online
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{peer_url}/health", timeout=5.0)
                if response.status_code != 200:
                    print(f"Peer {peer_url} not healthy: {response.status_code}")
                    schedule_peer_retry(peer_url)
                    return
            except Exception as e:
                print(f"Peer {peer_url} unreachable: {e}")
                schedule_peer_retry(peer_url)
                return

            # Get peer info
            peer_node_id = None
            try:
                health_data = response.json()
                peer_node_id = health_data.get('node_id', 'unknown')
            except:
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
                    timeout=10.0,
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

                    print(f"Mesh connected to peer: {peer_node_id} at {peer_url}")

                    # Start keep-alive
                    asyncio.create_task(peer_keepalive(peer_url))
                else:
                    print(f"Peer registration failed: {reg_response.status_code}")
                    schedule_peer_retry(peer_url)

            except Exception as e:
                print(f"Peer registration error: {e}")
                schedule_peer_retry(peer_url)

    except Exception as e:
        print(f"Mesh connection error for {peer_url}: {e}")
        schedule_peer_retry(peer_url)

async def peer_keepalive(peer_url: str):
    """Send periodic keepalives to mesh peer"""
    while peer_url in peer_connections:
        await asyncio.sleep(30)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{peer_url}/health", timeout=5.0)
                if response.status_code != 200:
                    print(f"Peer {peer_url} health check failed")
                    del peer_connections[peer_url]
                    schedule_peer_retry(peer_url)
                    return
        except Exception as e:
            print(f"Peer keepalive failed for {peer_url}: {e}")
            if peer_url in peer_connections:
                del peer_connections[peer_url]
            schedule_peer_retry(peer_url)
            return

def schedule_peer_retry(peer_url: str):
    """Schedule retry connection to peer"""
    async def retry():
        await asyncio.sleep(30)  # Wait 30 seconds before retry
        await connect_to_peer(peer_url)

    asyncio.create_task(retry())

@app.get("/api/mesh/peers")
async def get_mesh_peers():
    """Get list of connected mesh peers"""
    return {
        'node_id': node_id,
        'peers': list(peer_connections.values()),
        'known_peers': KNOWN_PEERS,
    }

@app.post("/api/mesh/message")
async def mesh_message(message: Dict[str, Any]):
    """Forward a message to the mesh network"""
    dest_node = message.get('dest_node')
    payload = message.get('payload')

    if not dest_node or not payload:
        raise HTTPException(status_code=400, detail="Missing dest_node or payload")

    # Find peer that can reach destination
    for peer_url, peer_info in peer_connections.items():
        if peer_info.get('node_id') == dest_node or dest_node == 'broadcast':
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{peer_url}/api/mesh/relay",
                        json={
                            'source_node': node_id,
                            'dest_node': dest_node,
                            'payload': payload,
                            'hop_count': message.get('hop_count', 0) + 1,
                        },
                        timeout=10.0,
                    )
                    if response.status_code == 200:
                        return {'status': 'relayed', 'via': peer_info.get('node_id')}
            except Exception as e:
                print(f"Mesh relay failed: {e}")

    return {'status': 'no_route', 'dest_node': dest_node}

@app.post("/api/mesh/relay")
async def mesh_relay(message: Dict[str, Any]):
    """Handle relayed mesh message"""
    source_node = message.get('source_node')
    dest_node = message.get('dest_node')
    payload = message.get('payload')
    hop_count = message.get('hop_count', 0)

    print(f"Mesh relay from {source_node}: {payload.get('type', 'unknown')} (hops: {hop_count})")

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
        except:
            pass

    return {'status': 'received'}

async def handle_mesh_payload(source_node: str, payload: Dict[str, Any]):
    """Handle mesh message payload locally"""
    msg_type = payload.get('type')

    if msg_type == 'ping':
        print(f"Mesh ping from {source_node}")
    elif msg_type == 'task_assignment':
        print(f"Task assignment from {source_node}: {payload.get('task')}")
        # Forward to connected wearables
        for ws in websocket_connections.values():
            try:
                await ws.send_text(json.dumps({
                    'type': 'task',
                    'data': payload.get('task'),
                }))
            except:
                pass

# ============= Main =============

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
