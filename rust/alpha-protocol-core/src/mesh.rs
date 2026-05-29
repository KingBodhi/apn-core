//! Mesh Network - libp2p-based P2P networking
//!
//! Implements the transport layer with:
//! - mDNS for local peer discovery
//! - Kademlia DHT for wide-area discovery
//! - Gossipsub for pub/sub messaging
//! - Noise protocol for encrypted transport

use std::{collections::HashMap, time::Duration};

use anyhow::{Context, Result};
use libp2p::{
    futures::StreamExt,
    gossipsub, identify, kad, mdns, noise, ping,
    swarm::{NetworkBehaviour, SwarmEvent},
    tcp, Multiaddr, PeerId, Swarm, SwarmBuilder,
};
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

/// Gossipsub topics for the Alpha Protocol Network
pub mod topics {
    pub const PEERS: &str = "apn.peers";
    pub const TASKS: &str = "apn.tasks";
    pub const HEARTBEAT: &str = "apn.heartbeat";
    pub const TOPOLOGY: &str = "apn.topology";
    pub const MINING: &str = "apn.mining";
    pub const PYTHIA: &str = "apn.pythia";
}

/// Information about a discovered peer
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerInfo {
    pub peer_id: String,
    pub addresses: Vec<String>,
    pub wallet_address: Option<String>,
    pub capabilities: Vec<String>,
    pub reputation: f64,
    pub latency_ms: Option<u64>,
    pub last_seen: i64,
}

/// Messages that can be sent/received on the mesh network
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MeshMessage {
    /// Peer announcement
    PeerAnnouncement {
        wallet_address: String,
        capabilities: Vec<String>,
        resources: Option<crate::wire::NodeResources>,
    },
    /// Task available
    TaskAvailable {
        task_id: String,
        task_type: String,
        reward_vibe: f64,
        payload: String,
    },
    /// Task claimed
    TaskClaimed {
        task_id: String,
        worker_peer_id: String,
    },
    /// Task completed
    TaskCompleted { task_id: String, result: String },
    /// Heartbeat
    Heartbeat {
        timestamp: i64,
        resources: Option<crate::wire::NodeResources>,
    },
    /// Mining share
    MiningShare {
        algorithm: String,
        hashrate: u64,
        shares_submitted: u64,
    },
}

/// Custom network behaviour combining multiple libp2p protocols
#[derive(NetworkBehaviour)]
pub struct AlphaBehaviour {
    /// Gossipsub for message broadcasting
    pub gossipsub: gossipsub::Behaviour,
    /// mDNS for local peer discovery
    pub mdns: mdns::tokio::Behaviour,
    /// Kademlia DHT for wide-area discovery
    pub kademlia: kad::Behaviour<kad::store::MemoryStore>,
    /// Identify protocol for peer info exchange
    pub identify: identify::Behaviour,
    /// Ping for connection health monitoring
    pub ping: ping::Behaviour,
}

/// Mesh network node
pub struct MeshNode {
    swarm: Swarm<AlphaBehaviour>,
    local_peer_id: PeerId,
    peers: HashMap<PeerId, PeerInfo>,
    event_tx: mpsc::UnboundedSender<MeshEvent>,
}

/// Events emitted by the mesh network
#[derive(Debug, Clone)]
pub enum MeshEvent {
    PeerConnected(PeerInfo),
    PeerDisconnected(String),
    MessageReceived { from: String, message: MeshMessage },
    TopologyChanged,
}

impl MeshNode {
    /// Create a new mesh network node
    pub async fn new(event_tx: mpsc::UnboundedSender<MeshEvent>) -> Result<Self> {
        // Generate a unique PeerId from Ed25519 keypair
        let local_key = libp2p::identity::Keypair::generate_ed25519();
        let local_peer_id = PeerId::from(local_key.public());

        tracing::info!("Local peer ID: {}", local_peer_id);

        // Create Gossipsub configuration
        let gossipsub_config = gossipsub::ConfigBuilder::default()
            .heartbeat_interval(Duration::from_secs(10))
            .validation_mode(gossipsub::ValidationMode::Strict)
            .build()
            .map_err(|msg| anyhow::anyhow!("Gossipsub config error: {msg}"))?;

        // Build Gossipsub behaviour
        let gossipsub = gossipsub::Behaviour::new(
            gossipsub::MessageAuthenticity::Signed(local_key.clone()),
            gossipsub_config,
        )
        .map_err(|msg| anyhow::anyhow!("Gossipsub creation error: {msg}"))?;

        // Create mDNS behaviour for local discovery
        #[allow(deprecated)]
        let mdns = mdns::tokio::Behaviour::new(mdns::Config::default(), local_peer_id)?;

        // Create Kademlia DHT
        let mut kad_config = kad::Config::new(libp2p::kad::PROTOCOL_NAME);
        kad_config.set_query_timeout(Duration::from_secs(60));
        let store = kad::store::MemoryStore::new(local_peer_id);
        let kademlia = kad::Behaviour::with_config(local_peer_id, store, kad_config);

        // Create Identify behaviour
        let identify = identify::Behaviour::new(identify::Config::new(
            crate::PROTOCOL_VERSION.to_string(),
            local_key.public(),
        ));

        // Create Ping behaviour
        let ping = ping::Behaviour::new(ping::Config::new());

        // Combine all behaviours
        let behaviour = AlphaBehaviour {
            gossipsub,
            mdns,
            kademlia,
            identify,
            ping,
        };

        // Build the Swarm
        let swarm = SwarmBuilder::with_existing_identity(local_key)
            .with_tokio()
            .with_tcp(tcp::Config::default(), noise::Config::new, || {
                libp2p::yamux::Config::default()
            })?
            .with_behaviour(|_| behaviour)?
            .with_swarm_config(|c| c.with_idle_connection_timeout(Duration::from_secs(60)))
            .build();

        Ok(Self {
            swarm,
            local_peer_id,
            peers: HashMap::new(),
            event_tx,
        })
    }

    /// Get local peer ID
    pub fn peer_id(&self) -> &PeerId {
        &self.local_peer_id
    }

    /// Start listening on a port
    pub async fn listen(&mut self, port: u16) -> Result<Multiaddr> {
        let addr: Multiaddr = format!("/ip4/0.0.0.0/tcp/{port}")
            .parse()
            .context("Failed to parse listen address")?;

        self.swarm.listen_on(addr.clone())?;
        tracing::info!("Listening on port {}", port);

        Ok(addr)
    }

    /// Connect to a bootstrap peer
    pub fn dial(&mut self, addr: Multiaddr) -> Result<()> {
        self.swarm.dial(addr.clone())?;
        tracing::info!("Dialing {}", addr);
        Ok(())
    }

    /// Subscribe to a gossipsub topic
    pub fn subscribe(&mut self, topic: &str) -> Result<()> {
        let topic = gossipsub::IdentTopic::new(topic);
        self.swarm
            .behaviour_mut()
            .gossipsub
            .subscribe(&topic)
            .map_err(|e| anyhow::anyhow!("Subscribe error: {e:?}"))?;

        tracing::debug!("Subscribed to topic: {}", topic);
        Ok(())
    }

    /// Subscribe to all standard APN topics
    pub fn subscribe_all(&mut self) -> Result<()> {
        self.subscribe(topics::PEERS)?;
        self.subscribe(topics::TASKS)?;
        self.subscribe(topics::HEARTBEAT)?;
        self.subscribe(topics::TOPOLOGY)?;
        self.subscribe(topics::MINING)?;
        self.subscribe(topics::PYTHIA)?;
        Ok(())
    }

    /// Publish a message to a topic
    pub fn publish(&mut self, topic: &str, message: &MeshMessage) -> Result<()> {
        let topic = gossipsub::IdentTopic::new(topic);
        let payload = serde_json::to_vec(message)?;

        self.swarm
            .behaviour_mut()
            .gossipsub
            .publish(topic.clone(), payload)
            .map_err(|e| anyhow::anyhow!("Publish error: {e:?}"))?;

        tracing::debug!("Published to topic: {}", topic);
        Ok(())
    }

    /// Broadcast peer announcement
    pub fn announce(
        &mut self,
        wallet_address: String,
        capabilities: Vec<String>,
        resources: Option<crate::wire::NodeResources>,
    ) -> Result<()> {
        let message = MeshMessage::PeerAnnouncement {
            wallet_address,
            capabilities,
            resources,
        };

        self.publish(topics::PEERS, &message)
    }

    /// Send heartbeat with current resource status
    pub fn send_heartbeat(&mut self, resources: Option<crate::wire::NodeResources>) -> Result<()> {
        let message = MeshMessage::Heartbeat {
            timestamp: chrono::Utc::now().timestamp(),
            resources,
        };

        self.publish(topics::HEARTBEAT, &message)
    }

    /// Get list of connected peers
    pub fn get_peers(&self) -> Vec<PeerInfo> {
        self.peers.values().cloned().collect()
    }

    /// Run the mesh network event loop
    pub async fn run(&mut self) -> Result<()> {
        loop {
            match self.swarm.select_next_some().await {
                SwarmEvent::ConnectionEstablished {
                    peer_id, endpoint, ..
                } => {
                    tracing::info!(
                        "Connected to peer: {} at {}",
                        peer_id,
                        endpoint.get_remote_address()
                    );

                    let peer_info = PeerInfo {
                        peer_id: peer_id.to_string(),
                        addresses: vec![endpoint.get_remote_address().to_string()],
                        wallet_address: None,
                        capabilities: vec![],
                        reputation: 0.0,
                        latency_ms: None,
                        last_seen: chrono::Utc::now().timestamp(),
                    };

                    self.peers.insert(peer_id, peer_info.clone());
                    let _ = self.event_tx.send(MeshEvent::PeerConnected(peer_info));
                }

                SwarmEvent::ConnectionClosed { peer_id, cause, .. } => {
                    tracing::info!("Disconnected from peer: {} (cause: {:?})", peer_id, cause);
                    self.peers.remove(&peer_id);
                    let _ = self
                        .event_tx
                        .send(MeshEvent::PeerDisconnected(peer_id.to_string()));
                }

                SwarmEvent::Behaviour(AlphaBehaviourEvent::Mdns(mdns::Event::Discovered(
                    peers,
                ))) => {
                    for (peer_id, addr) in peers {
                        tracing::info!("mDNS discovered peer: {} at {}", peer_id, addr);
                        let _ = self.swarm.dial(addr.clone());
                        self.swarm
                            .behaviour_mut()
                            .kademlia
                            .add_address(&peer_id, addr);
                    }
                }

                SwarmEvent::Behaviour(AlphaBehaviourEvent::Gossipsub(
                    gossipsub::Event::Message {
                        propagation_source,
                        message,
                        ..
                    },
                )) => {
                    if let Ok(mesh_message) = serde_json::from_slice::<MeshMessage>(&message.data) {
                        tracing::debug!(
                            "Received message from {}: {:?}",
                            propagation_source,
                            mesh_message
                        );

                        // Update peer info if it's an announcement
                        if let MeshMessage::PeerAnnouncement {
                            wallet_address,
                            capabilities,
                            ..
                        } = &mesh_message
                        {
                            if let Some(peer) = self.peers.get_mut(&propagation_source) {
                                peer.wallet_address = Some(wallet_address.clone());
                                peer.capabilities = capabilities.clone();
                                peer.last_seen = chrono::Utc::now().timestamp();
                            }
                        }

                        let _ = self.event_tx.send(MeshEvent::MessageReceived {
                            from: propagation_source.to_string(),
                            message: mesh_message,
                        });
                    }
                }

                SwarmEvent::Behaviour(AlphaBehaviourEvent::Ping(ping::Event {
                    peer,
                    result,
                    ..
                })) => {
                    if let Ok(rtt) = result {
                        if let Some(peer_info) = self.peers.get_mut(&peer) {
                            peer_info.latency_ms = Some(rtt.as_millis() as u64);
                        }
                    }
                }

                _ => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_mesh_node_creation() {
        let (tx, _rx) = mpsc::unbounded_channel();
        let node = MeshNode::new(tx).await;
        assert!(node.is_ok());
    }
}
