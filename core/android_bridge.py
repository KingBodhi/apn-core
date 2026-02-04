"""
Alpha Protocol Network - Android Peer Bridge
Manages Android devices as APN peer nodes in the Sovereign Stack.
Even non-GrapheneOS devices can participate as sovereign nodes.
"""
import asyncio
import subprocess
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable
from enum import Enum
from datetime import datetime

from .logging_config import get_logger

logger = get_logger("android_bridge")


class AndroidConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    REGISTERED = "registered"  # Full APN peer
    ERROR = "error"


class WearableType(Enum):
    RING = "ring"
    GLASSES = "glasses"


@dataclass
class APNPeerNode:
    """Represents an APN peer node (Android device in Sovereign Stack)"""
    node_id: str
    public_key: str
    device_serial: Optional[str] = None
    device_model: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    last_seen: Optional[datetime] = None
    is_graphene: bool = False  # True if GrapheneOS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "public_key": self.public_key,
            "device_serial": self.device_serial,
            "device_model": self.device_model,
            "roles": self.roles,
            "capabilities": self.capabilities,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "is_graphene": self.is_graphene,
        }


@dataclass
class WearableState:
    """State of a connected wearable device"""
    device_type: WearableType
    connected: bool = False
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    battery_level: Optional[int] = None
    last_event: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ButtonEvent:
    """Ring button event from peer node"""
    event_type: str  # singleTap, doubleTap, tripleTap, longPressEnd
    timestamp: float
    device_id: Optional[str] = None
    source_node_id: Optional[str] = None  # APN node that sent this event


class AndroidBridge:
    """
    Manages Android devices as APN peer nodes in the Sovereign Stack.
    Handles peer registration, wearable events, and mesh communication.
    """

    ADB_PORT = 8000  # Port to reverse-forward for dashboard access

    def __init__(self):
        self.state = AndroidConnectionState.DISCONNECTED
        self.device_serial: Optional[str] = None
        self.device_model: Optional[str] = None

        # Registered APN peer nodes (Android devices)
        self.peer_nodes: Dict[str, APNPeerNode] = {}
        self.active_peer_id: Optional[str] = None  # Currently connected peer

        # Wearable states (per-node, keyed by node_id)
        self.ring_states: Dict[str, WearableState] = {}
        self.glasses_states: Dict[str, WearableState] = {}

        # Legacy single-device states (for backwards compat)
        self.ring_state = WearableState(device_type=WearableType.RING)
        self.glasses_state = WearableState(device_type=WearableType.GLASSES)

        # Event callbacks
        self._button_callbacks: List[Callable[[ButtonEvent], None]] = []
        self._state_callbacks: List[Callable[[AndroidConnectionState], None]] = []
        self._peer_callbacks: List[Callable[[APNPeerNode], None]] = []

    async def check_adb_available(self) -> bool:
        """Check if ADB is installed and available"""
        try:
            result = await self._run_adb(["version"])
            return result.returncode == 0
        except FileNotFoundError:
            logger.warning("ADB not found. Install with: sudo apt install adb")
            return False

    async def scan_devices(self) -> List[Dict[str, str]]:
        """Scan for connected Android devices"""
        try:
            result = await self._run_adb(["devices", "-l"])
            if result.returncode != 0:
                return []

            devices = []
            lines = result.stdout.strip().split('\n')[1:]  # Skip header

            for line in lines:
                if not line.strip() or 'offline' in line:
                    continue

                parts = line.split()
                if len(parts) >= 2:
                    serial = parts[0]
                    status = parts[1]

                    # Parse device info
                    model = "Unknown"
                    product = "Unknown"
                    for part in parts[2:]:
                        if part.startswith("model:"):
                            model = part.split(":")[1]
                        elif part.startswith("product:"):
                            product = part.split(":")[1]

                    if status == "device":
                        devices.append({
                            "serial": serial,
                            "model": model,
                            "product": product,
                            "status": status
                        })

            return devices

        except Exception as e:
            logger.error(f"Error scanning ADB devices: {e}")
            return []

    async def connect(self, serial: Optional[str] = None) -> bool:
        """
        Connect to Android device and set up port forwarding.
        If serial is None, connects to first available device.
        """
        self.state = AndroidConnectionState.CONNECTING
        self._notify_state_change()

        try:
            # Find device
            devices = await self.scan_devices()
            if not devices:
                logger.error("No Android devices found")
                self.state = AndroidConnectionState.ERROR
                self._notify_state_change()
                return False

            # Select device
            if serial:
                device = next((d for d in devices if d["serial"] == serial), None)
                if not device:
                    logger.error(f"Device {serial} not found")
                    self.state = AndroidConnectionState.ERROR
                    self._notify_state_change()
                    return False
            else:
                device = devices[0]

            self.device_serial = device["serial"]
            self.device_model = device["model"]

            # Set up reverse port forwarding
            # This makes localhost:8000 on the phone point to localhost:8000 on the computer
            result = await self._run_adb([
                "-s", self.device_serial,
                "reverse",
                f"tcp:{self.ADB_PORT}",
                f"tcp:{self.ADB_PORT}"
            ])

            if result.returncode != 0:
                logger.error(f"Failed to set up port forwarding: {result.stderr}")
                self.state = AndroidConnectionState.ERROR
                self._notify_state_change()
                return False

            logger.info(f"Connected to {self.device_model} ({self.device_serial})")
            logger.info(f"Port forwarding: phone:localhost:{self.ADB_PORT} -> computer:localhost:{self.ADB_PORT}")

            self.state = AndroidConnectionState.CONNECTED
            self._notify_state_change()
            return True

        except Exception as e:
            logger.error(f"Connection error: {e}")
            self.state = AndroidConnectionState.ERROR
            self._notify_state_change()
            return False

    async def disconnect(self):
        """Disconnect from Android device"""
        if self.device_serial:
            try:
                # Remove port forwarding
                await self._run_adb([
                    "-s", self.device_serial,
                    "reverse",
                    "--remove",
                    f"tcp:{self.ADB_PORT}"
                ])
            except Exception as e:
                logger.warning(f"Error removing port forward: {e}")

        self.device_serial = None
        self.device_model = None
        self.state = AndroidConnectionState.DISCONNECTED
        self._notify_state_change()
        logger.info("Disconnected from Android device")

    async def launch_companion_app(self) -> bool:
        """Launch the wearables-companion app on the connected device"""
        if not self.device_serial:
            return False

        try:
            # Launch the app (adjust package name as needed)
            result = await self._run_adb([
                "-s", self.device_serial,
                "shell", "am", "start",
                "-n", "com.example.wearables_companion/.MainActivity"
            ])
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Failed to launch app: {e}")
            return False

    def update_ring_state(self, state_data: Dict[str, Any]):
        """Update ring connection state from companion app"""
        self.ring_state.connected = state_data.get("connected", False)
        self.ring_state.device_id = state_data.get("deviceId")
        self.ring_state.device_name = state_data.get("deviceName")
        self.ring_state.battery_level = state_data.get("batteryLevel")
        logger.info(f"Ring state updated: connected={self.ring_state.connected}")

    def update_glasses_state(self, state_data: Dict[str, Any]):
        """Update glasses connection state from companion app"""
        self.glasses_state.connected = state_data.get("connected", False)
        self.glasses_state.device_id = state_data.get("deviceId")
        self.glasses_state.device_name = state_data.get("deviceName")
        self.glasses_state.battery_level = state_data.get("batteryLevel")
        logger.info(f"Glasses state updated: connected={self.glasses_state.connected}")

    def handle_button_event(self, event_data: Dict[str, Any]):
        """Handle button event from ring"""
        event = ButtonEvent(
            event_type=event_data.get("type", "unknown"),
            timestamp=event_data.get("timestamp", 0),
            device_id=event_data.get("deviceId")
        )

        self.ring_state.last_event = event.event_type
        logger.info(f"Ring button event: {event.event_type}")

        # Notify callbacks
        for callback in self._button_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Button callback error: {e}")

    def on_button_event(self, callback: Callable[[ButtonEvent], None]):
        """Register callback for button events"""
        self._button_callbacks.append(callback)

    def on_state_change(self, callback: Callable[[AndroidConnectionState], None]):
        """Register callback for connection state changes"""
        self._state_callbacks.append(callback)

    def on_peer_registered(self, callback: Callable[[APNPeerNode], None]):
        """Register callback for peer registration events"""
        self._peer_callbacks.append(callback)

    # ============ Peer Node Management ============

    def register_peer(self, registration_data: Dict[str, Any]) -> APNPeerNode:
        """
        Register an Android device as an APN peer node.
        Called when companion app sends registration request.
        """
        node_id = registration_data.get("nodeId", "")
        public_key = registration_data.get("publicKey", "")
        roles = registration_data.get("roles", [])
        settings = registration_data.get("settings", {})
        capabilities = settings.get("capabilities", {})

        # Check if this is a GrapheneOS device
        device_type = capabilities.get("device_type", "android")
        is_graphene = device_type == "graphene"

        # Create or update peer node
        peer = APNPeerNode(
            node_id=node_id,
            public_key=public_key,
            device_serial=self.device_serial,
            device_model=self.device_model or settings.get("device_name", "Android"),
            roles=roles,
            capabilities=capabilities,
            last_seen=datetime.now(),
            is_graphene=is_graphene,
        )

        self.peer_nodes[node_id] = peer
        self.active_peer_id = node_id

        # Initialize wearable states for this peer
        self.ring_states[node_id] = WearableState(device_type=WearableType.RING)
        self.glasses_states[node_id] = WearableState(device_type=WearableType.GLASSES)

        # Update legacy single-device states
        self.ring_state = self.ring_states[node_id]
        self.glasses_state = self.glasses_states[node_id]

        # Update connection state
        self.state = AndroidConnectionState.REGISTERED
        self._notify_state_change()

        # Notify callbacks
        for callback in self._peer_callbacks:
            try:
                callback(peer)
            except Exception as e:
                logger.error(f"Peer callback error: {e}")

        logger.info(f"Registered APN peer: {node_id} ({peer.device_model})")
        if is_graphene:
            logger.info(f"  → GrapheneOS detected - Full sovereign node")
        else:
            logger.info(f"  → Standard Android - Sovereign Stack participant")
        logger.info(f"  → Roles: {', '.join(roles)}")
        logger.info(f"  → Capabilities: {capabilities}")

        return peer

    def get_peer(self, node_id: str) -> Optional[APNPeerNode]:
        """Get peer node by ID"""
        return self.peer_nodes.get(node_id)

    def get_active_peer(self) -> Optional[APNPeerNode]:
        """Get currently active peer node"""
        if self.active_peer_id:
            return self.peer_nodes.get(self.active_peer_id)
        return None

    def get_all_peers(self) -> List[APNPeerNode]:
        """Get all registered peer nodes"""
        return list(self.peer_nodes.values())

    def update_peer_last_seen(self, node_id: str):
        """Update last seen timestamp for a peer"""
        if node_id in self.peer_nodes:
            self.peer_nodes[node_id].last_seen = datetime.now()

    def _notify_state_change(self):
        """Notify state change callbacks"""
        for callback in self._state_callbacks:
            try:
                callback(self.state)
            except Exception as e:
                logger.error(f"State callback error: {e}")

    async def _run_adb(self, args: List[str]) -> subprocess.CompletedProcess:
        """Run ADB command asynchronously"""
        process = await asyncio.create_subprocess_exec(
            "adb", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        return subprocess.CompletedProcess(
            args=["adb"] + args,
            returncode=process.returncode,
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else ""
        )

    def get_status(self) -> Dict[str, Any]:
        """Get current bridge and peer status"""
        active_peer = self.get_active_peer()

        return {
            "state": self.state.value,
            "usb_device": {
                "serial": self.device_serial,
                "model": self.device_model
            } if self.device_serial else None,
            "active_peer": active_peer.to_dict() if active_peer else None,
            "registered_peers": [p.to_dict() for p in self.peer_nodes.values()],
            "peer_count": len(self.peer_nodes),
            "wearables": {
                "ring": {
                    "connected": self.ring_state.connected,
                    "deviceName": self.ring_state.device_name,
                    "batteryLevel": self.ring_state.battery_level,
                    "lastEvent": self.ring_state.last_event
                },
                "glasses": {
                    "connected": self.glasses_state.connected,
                    "deviceName": self.glasses_state.device_name,
                    "batteryLevel": self.glasses_state.battery_level
                }
            }
        }
