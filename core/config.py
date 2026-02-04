"""
APN CORE - Configuration Management
Centralized configuration system with proper validation and defaults.

Part of APN CORE v1.0.0 - Alpha Protocol Network
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)

# APN Core Version
APN_CORE_VERSION = "1.0.0"
APN_PROTOCOL_VERSION = "alpha/1.0.0"

# Default NATS relay for mesh networking
DEFAULT_NATS_RELAY = "nats://nonlocal.info:4222"

# Default known peers (Pythia master node)
DEFAULT_KNOWN_PEERS = [
    "https://dashboard.powerclubglobal.com",
    "https://pythia.nonlocal.info",
]

@dataclass
class NodeIdentity:
    """Node identity configuration"""
    node_id: str = ""
    private_key_path: str = ""
    public_key: str = ""
    payment_address: str = ""

@dataclass
class NetworkConfig:
    """Network and connectivity configuration"""
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    tunnel_enabled: bool = True
    tunnel_provider: str = "cloudflare"  # cloudflare, wireguard, i2p
    discovery_enabled: bool = True
    nats_relay: str = DEFAULT_NATS_RELAY
    known_peers: List[str] = None

    def __post_init__(self):
        if self.known_peers is None:
            self.known_peers = DEFAULT_KNOWN_PEERS.copy()

@dataclass
class RadioConfig:
    """Radio hardware configuration"""
    enabled_radios: List[str] = None
    meshtastic_device: str = "auto"  # auto-detect or specific path
    lora_frequency: float = 915.0
    wifi_interface: str = "wlan0"
    
    def __post_init__(self):
        if self.enabled_radios is None:
            self.enabled_radios = ["meshtastic"]

@dataclass
class ServicesConfig:
    """Node services configuration"""
    roles: List[str] = None
    relay_enabled: bool = False
    storage_enabled: bool = False
    compute_enabled: bool = False
    bridge_enabled: bool = False
    
    # Service settings
    storage_gb: int = 10
    storage_price_per_gb: int = 100
    compute_cores: int = 1
    compute_price_per_second: int = 10
    bridge_region: str = "US"
    bridge_price_per_mb: int = 1
    
    def __post_init__(self):
        if self.roles is None:
            self.roles = []

@dataclass
class APNConfig:
    """Main APN configuration container"""
    identity: NodeIdentity
    network: NetworkConfig
    radio: RadioConfig
    services: ServicesConfig

    @property
    def version(self) -> str:
        """Get APN Core version"""
        return APN_CORE_VERSION

    @property
    def protocol_version(self) -> str:
        """Get APN protocol version"""
        return APN_PROTOCOL_VERSION
    
    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> 'APNConfig':
        """Load configuration from file or create default"""
        if config_path is None:
            config_path = get_config_dir() / "apn_config.json"
            
        if config_path.exists():
            try:
                with config_path.open() as f:
                    data = json.load(f)
                return cls.from_dict(data)
            except Exception as e:
                logger.error(f"Failed to load config from {config_path}: {e}")
                
        # Create default config
        logger.info("Creating default configuration")
        config = cls.create_default()
        config.save(config_path)
        return config
    
    @classmethod
    def create_default(cls) -> 'APNConfig':
        """Create default configuration with generated identity"""
        identity = NodeIdentity()
        network = NetworkConfig()
        radio = RadioConfig()
        services = ServicesConfig()
        
        # Generate identity if not exists
        config_dir = get_config_dir()
        private_key_path = config_dir / "node.key"
        
        if not private_key_path.exists():
            private_key, public_key, node_id = generate_node_identity()
            save_private_key(private_key, private_key_path)
            identity.private_key_path = str(private_key_path)
            identity.public_key = public_key
            identity.node_id = node_id
            logger.info(f"Generated new node identity: {node_id}")
        else:
            private_key = load_private_key(private_key_path)
            public_key = get_public_key_string(private_key)
            node_id = generate_node_id_from_key(public_key)
            identity.private_key_path = str(private_key_path)
            identity.public_key = public_key
            identity.node_id = node_id
            
        return cls(identity, network, radio, services)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'APNConfig':
        """Create config from dictionary"""
        identity = NodeIdentity(**data.get("identity", {}))
        network = NetworkConfig(**data.get("network", {}))
        radio = RadioConfig(**data.get("radio", {}))
        services = ServicesConfig(**data.get("services", {}))
        return cls(identity, network, radio, services)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary"""
        return {
            "identity": asdict(self.identity),
            "network": asdict(self.network),
            "radio": asdict(self.radio),
            "services": asdict(self.services)
        }
    
    def save(self, config_path: Optional[Path] = None):
        """Save configuration to file"""
        if config_path is None:
            config_path = get_config_dir() / "apn_config.json"
            
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with config_path.open("w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Configuration saved to {config_path}")

def get_config_dir() -> Path:
    """Get the APN configuration directory"""
    return Path.home() / ".apn"

def generate_node_identity():
    """Generate a new Ed25519 keypair and node ID"""
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    public_key_hex = public_key_bytes.hex()
    node_id = f"apn_{public_key_hex[:16]}"
    return private_key, public_key_hex, node_id

def save_private_key(private_key, path: Path):
    """Save private key to file with proper permissions"""
    path.parent.mkdir(parents=True, exist_ok=True)
    
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    path.write_bytes(private_bytes)
    path.chmod(0o600)  # Owner read/write only

def load_private_key(path: Path):
    """Load private key from file"""
    private_bytes = path.read_bytes()
    return serialization.load_pem_private_key(private_bytes, password=None)

def get_public_key_string(private_key) -> str:
    """Get public key string from private key"""
    public_key = private_key.public_key()
    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    return public_key_bytes.hex()

def generate_node_id_from_key(public_key_hex: str) -> str:
    """Generate node ID from public key"""
    return f"apn_{public_key_hex[:16]}"
