//! Wire Protocol - Message types and serialization
//!
//! Defines the wire format for Alpha Protocol Network messages:
//!
//! | Type | Code | Description |
//! |------|------|-------------|
//! | HANDSHAKE | 0x01 | Initial connection setup |
//! | DATA | 0x02 | Encrypted payload |
//! | ACK | 0x03 | Acknowledgment |
//! | PEER_ANNOUNCE | 0x04 | Broadcast peer capabilities |
//! | TOPOLOGY_UPDATE | 0x05 | Network topology change |
//! | TASK_BROADCAST | 0x06 | Distributed task announcement |
//! | PYTHIA_GRADIENT | 0x07 | Federated learning gradient |

use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

/// Wire message type codes
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MessageType {
    Handshake = 0x01,
    Data = 0x02,
    Ack = 0x03,
    PeerAnnounce = 0x04,
    TopologyUpdate = 0x05,
    TaskBroadcast = 0x06,
    PythiaGradient = 0x07,
    Heartbeat = 0x08,
    TaskClaim = 0x09,
    TaskResult = 0x0A,
    PaymentConfirm = 0x0B,
}

/// Wire message header (fixed 32 bytes)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageHeader {
    /// Protocol version (2 bytes)
    pub version: u16,
    /// Message type (1 byte)
    pub msg_type: MessageType,
    /// Flags (1 byte): encrypted, compressed, etc.
    pub flags: u8,
    /// Sequence number (4 bytes)
    pub sequence: u32,
    /// Timestamp (8 bytes, unix millis)
    pub timestamp: u64,
    /// Sender peer ID short (8 bytes)
    pub sender: [u8; 8],
    /// Payload length (4 bytes)
    pub payload_len: u32,
    /// Reserved (4 bytes)
    pub reserved: [u8; 4],
}

impl MessageHeader {
    pub fn new(msg_type: MessageType, sender: [u8; 8], payload_len: u32) -> Self {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        Self {
            version: 1,
            msg_type,
            flags: 0,
            sequence: 0,
            timestamp,
            sender,
            payload_len,
            reserved: [0; 4],
        }
    }

    /// Set encrypted flag
    pub fn set_encrypted(&mut self) {
        self.flags |= 0x01;
    }

    /// Check if encrypted
    pub fn is_encrypted(&self) -> bool {
        self.flags & 0x01 != 0
    }

    /// Set compressed flag
    pub fn set_compressed(&mut self) {
        self.flags |= 0x02;
    }

    /// Check if compressed
    pub fn is_compressed(&self) -> bool {
        self.flags & 0x02 != 0
    }
}

/// Complete wire message
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub header: MessageHeader,
    pub payload: Vec<u8>,
}

impl Message {
    /// Create a new message
    pub fn new(msg_type: MessageType, sender: [u8; 8], payload: Vec<u8>) -> Self {
        let header = MessageHeader::new(msg_type, sender, payload.len() as u32);
        Self { header, payload }
    }

    /// Serialize to bytes
    pub fn to_bytes(&self) -> Vec<u8> {
        // Use bincode or custom serialization
        serde_json::to_vec(self).unwrap_or_default()
    }

    /// Deserialize from bytes
    pub fn from_bytes(bytes: &[u8]) -> anyhow::Result<Self> {
        serde_json::from_slice(bytes).map_err(|e| anyhow::anyhow!("Deserialization failed: {}", e))
    }
}

/// Handshake payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandshakePayload {
    /// Node's public key
    pub public_key: [u8; 32],
    /// Ephemeral X25519 public key for session
    pub ephemeral_key: [u8; 32],
    /// Node capabilities
    pub capabilities: Vec<String>,
    /// Wallet address (for payments)
    pub wallet_address: String,
    /// Protocol version string
    pub protocol_version: String,
}

/// Peer announcement payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerAnnouncePayload {
    /// Wallet address
    pub wallet_address: String,
    /// Capabilities (compute, storage, relay, mining, etc.)
    pub capabilities: Vec<String>,
    /// Current reputation score
    pub reputation: f64,
    /// Available resources
    pub resources: NodeResources,
}

/// Node resources
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeResources {
    /// CPU cores available
    pub cpu_cores: u32,
    /// RAM available (MB)
    pub ram_mb: u64,
    /// Storage available (GB)
    pub storage_gb: u64,
    /// GPU available
    pub gpu_available: bool,
    /// GPU model (if available)
    pub gpu_model: Option<String>,
    /// Hashrate (H/s) for mining
    pub hashrate: Option<u64>,
    /// Bandwidth (Mbps)
    pub bandwidth_mbps: Option<u32>,
}

impl Default for NodeResources {
    fn default() -> Self {
        Self {
            cpu_cores: num_cpus::get() as u32,
            ram_mb: 0,
            storage_gb: 0,
            gpu_available: false,
            gpu_model: None,
            hashrate: None,
            bandwidth_mbps: None,
        }
    }
}

/// Task broadcast payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskBroadcastPayload {
    /// Unique task ID
    pub task_id: String,
    /// Task type (python, shell, wasm, mining, inference)
    pub task_type: String,
    /// Reward in VIBE tokens
    pub reward_vibe: f64,
    /// Required capabilities
    pub required_capabilities: Vec<String>,
    /// Task data (encrypted)
    pub payload: Vec<u8>,
    /// Deadline (unix timestamp)
    pub deadline: Option<u64>,
}

/// Topology update payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TopologyUpdatePayload {
    /// List of known peers
    pub peers: Vec<PeerEntry>,
    /// Routing table updates
    pub routes: Vec<RouteEntry>,
}

/// Peer entry in topology
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerEntry {
    pub peer_id: String,
    pub addresses: Vec<String>,
    pub last_seen: u64,
    pub reputation: f64,
}

/// Route entry for onion routing
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RouteEntry {
    pub destination: String,
    pub next_hop: String,
    pub hops: u8,
    pub latency_ms: u32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_message_creation() {
        let sender = [1u8; 8];
        let payload = b"Hello, APN!".to_vec();

        let msg = Message::new(MessageType::Data, sender, payload.clone());

        assert_eq!(msg.header.msg_type, MessageType::Data);
        assert_eq!(msg.header.sender, sender);
        assert_eq!(msg.payload, payload);
    }

    #[test]
    fn test_message_serialization() {
        let sender = [1u8; 8];
        let msg = Message::new(MessageType::Handshake, sender, vec![1, 2, 3]);

        let bytes = msg.to_bytes();
        let decoded = Message::from_bytes(&bytes).unwrap();

        assert_eq!(decoded.header.msg_type, MessageType::Handshake);
        assert_eq!(decoded.payload, vec![1, 2, 3]);
    }

    #[test]
    fn test_header_flags() {
        let mut header = MessageHeader::new(MessageType::Data, [0; 8], 0);

        assert!(!header.is_encrypted());
        header.set_encrypted();
        assert!(header.is_encrypted());

        assert!(!header.is_compressed());
        header.set_compressed();
        assert!(header.is_compressed());
    }
}
