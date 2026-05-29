//! Alpha Node - High-level node abstraction
//!
//! Combines identity, mesh networking, and relay into a single
//! easy-to-use interface for building APN applications.

use std::sync::Arc;

use anyhow::Result;
use tokio::sync::{mpsc, RwLock};

use crate::{
    identity::NodeIdentity,
    mesh::{MeshEvent, MeshMessage, MeshNode, PeerInfo},
    relay::{NatsRelay, PeerAnnouncement, RelayConfig, RelayEvent},
};

/// Node configuration
#[derive(Debug, Clone)]
pub struct NodeConfig {
    /// Listen port for P2P connections
    pub p2p_port: u16,
    /// Use NATS relay for NAT traversal
    pub use_relay: bool,
    /// NATS relay URL
    pub relay_url: String,
    /// Bootstrap peers to connect to
    pub bootstrap_peers: Vec<String>,
    /// Node capabilities
    pub capabilities: Vec<String>,
    /// Human-readable display name (e.g. "Sirak Studios")
    pub device_name: Option<String>,
}

impl Default for NodeConfig {
    fn default() -> Self {
        Self {
            p2p_port: crate::DEFAULT_P2P_PORT,
            use_relay: true,
            relay_url: crate::DEFAULT_NATS_RELAY.to_string(),
            bootstrap_peers: vec![],
            capabilities: vec!["compute".to_string(), "relay".to_string()],
            device_name: None,
        }
    }
}

/// Events emitted by the Alpha Node
#[derive(Debug, Clone)]
pub enum NodeEvent {
    /// Node started successfully
    Started { peer_id: String, address: String },
    /// Connected to a peer
    PeerConnected(PeerInfo),
    /// Disconnected from a peer
    PeerDisconnected(String),
    /// Message received
    MessageReceived { from: String, message: MeshMessage },
    /// Relay connected
    RelayConnected,
    /// Relay disconnected
    RelayDisconnected,
    /// Error occurred
    Error(String),
}

/// High-level Alpha Protocol node
pub struct AlphaNode {
    /// Node identity (keypair, addresses)
    identity: NodeIdentity,
    /// Configuration
    config: NodeConfig,
    /// Mesh network node
    mesh: Option<MeshNode>,
    /// NATS relay (optional)
    relay: Option<NatsRelay>,
    /// Event sender
    event_tx: mpsc::UnboundedSender<NodeEvent>,
    /// Connected peers
    peers: Arc<RwLock<Vec<PeerInfo>>>,
}

impl AlphaNode {
    /// Create a new Alpha node with generated identity
    pub fn new(config: NodeConfig, event_tx: mpsc::UnboundedSender<NodeEvent>) -> Result<Self> {
        let identity = NodeIdentity::generate()?;

        tracing::info!(
            "Created Alpha node: {} ({})",
            identity.short_id(),
            identity.address()
        );

        Ok(Self {
            identity,
            config,
            mesh: None,
            relay: None,
            event_tx,
            peers: Arc::new(RwLock::new(Vec::new())),
        })
    }

    /// Create a new Alpha node with imported identity
    pub fn with_identity(
        mnemonic: &str,
        config: NodeConfig,
        event_tx: mpsc::UnboundedSender<NodeEvent>,
    ) -> Result<Self> {
        let identity = NodeIdentity::from_mnemonic_phrase(mnemonic)?;

        tracing::info!(
            "Imported Alpha node: {} ({})",
            identity.short_id(),
            identity.address()
        );

        Ok(Self {
            identity,
            config,
            mesh: None,
            relay: None,
            event_tx,
            peers: Arc::new(RwLock::new(Vec::new())),
        })
    }

    /// Get node identity
    pub fn identity(&self) -> &NodeIdentity {
        &self.identity
    }

    /// Get wallet address
    pub fn address(&self) -> &str {
        self.identity.address()
    }

    /// Get short node ID
    pub fn short_id(&self) -> String {
        self.identity.short_id()
    }

    /// Get public key hex
    pub fn public_key(&self) -> String {
        self.identity.public_key_hex()
    }

    /// Get connected peers
    pub async fn peers(&self) -> Vec<PeerInfo> {
        self.peers.read().await.clone()
    }

    /// Start the node
    pub async fn start(&mut self) -> Result<()> {
        // Create mesh event channel
        let (mesh_tx, mut mesh_rx) = mpsc::unbounded_channel();

        // Create and start mesh node
        let mut mesh = MeshNode::new(mesh_tx).await?;
        let addr = mesh.listen(self.config.p2p_port).await?;

        // Subscribe to all topics
        mesh.subscribe_all()?;

        // Connect to bootstrap peers
        for peer_addr in &self.config.bootstrap_peers {
            if let Ok(multiaddr) = peer_addr.parse() {
                let _ = mesh.dial(multiaddr);
            }
        }

        let peer_id = mesh.peer_id().to_string();
        self.mesh = Some(mesh);

        // Start relay if configured
        if self.config.use_relay {
            let (relay_tx, mut relay_rx) = mpsc::unbounded_channel();

            let relay_config = RelayConfig {
                url: self.config.relay_url.clone(),
                node_id: self.identity.short_id(),
                auto_reconnect: true,
            };

            let mut relay = NatsRelay::new(relay_config, relay_tx);
            relay.connect().await?;

            // Collect resources for initial announcement
            let resources = crate::resources::collect_resources().await.ok();

            // Announce on relay
            relay
                .announce(
                    self.identity.address(),
                    &self.config.capabilities,
                    resources.as_ref(),
                    self.config.device_name.as_deref(),
                )
                .await?;

            // Spawn relay listener (subscribes to incoming messages)
            let relay_for_run = relay.clone_for_listener();
            tokio::spawn(async move {
                if let Err(e) = relay_for_run.run().await {
                    tracing::error!("Relay listener error: {}", e);
                }
            });

            self.relay = Some(relay);

            // Spawn relay event handler
            let event_tx = self.event_tx.clone();
            tokio::spawn(async move {
                while let Some(event) = relay_rx.recv().await {
                    match event {
                        RelayEvent::Connected => {
                            let _ = event_tx.send(NodeEvent::RelayConnected);
                        }
                        RelayEvent::Disconnected => {
                            let _ = event_tx.send(NodeEvent::RelayDisconnected);
                        }
                        RelayEvent::MessageReceived { subject, payload } => {
                            // Try parsing as MeshMessage first
                            if let Ok(message) = serde_json::from_slice::<MeshMessage>(&payload) {
                                let _ = event_tx.send(NodeEvent::MessageReceived {
                                    from: subject,
                                    message,
                                });
                            } else if let Ok(announcement) =
                                serde_json::from_slice::<PeerAnnouncement>(&payload)
                            {
                                // Convert PeerAnnouncement to MeshMessage
                                let message = MeshMessage::PeerAnnouncement {
                                    wallet_address: announcement.wallet_address,
                                    capabilities: announcement.capabilities,
                                    resources: announcement.resources.clone(),
                                };
                                let _ = event_tx.send(NodeEvent::MessageReceived {
                                    from: format!("{} ({})", subject, announcement.node_id),
                                    message,
                                });
                            } else if subject.contains("heartbeat") {
                                // Parse heartbeat message
                                if let Ok(heartbeat) =
                                    serde_json::from_slice::<serde_json::Value>(&payload)
                                {
                                    if let (Some(node_id), Some(resources)) = (
                                        heartbeat.get("node_id").and_then(|v| v.as_str()),
                                        heartbeat.get("resources"),
                                    ) {
                                        if !resources.is_null() {
                                            if let Ok(res) = serde_json::from_value::<
                                                crate::wire::NodeResources,
                                            >(
                                                resources.clone()
                                            ) {
                                                let message = MeshMessage::Heartbeat {
                                                    timestamp: chrono::Utc::now().timestamp(),
                                                    resources: Some(res),
                                                };
                                                let _ = event_tx.send(NodeEvent::MessageReceived {
                                                    from: format!("apn.heartbeat ({})", node_id),
                                                    message,
                                                });
                                            }
                                        }
                                    }
                                }
                            } else {
                                tracing::debug!(
                                    "Could not parse relay message from {}: {:?}",
                                    subject,
                                    String::from_utf8_lossy(&payload)
                                );
                            }
                        }
                        RelayEvent::Error(e) => {
                            let _ = event_tx.send(NodeEvent::Error(e));
                        }
                    }
                }
            });
        }

        // Spawn mesh event handler
        let event_tx = self.event_tx.clone();
        let peers = self.peers.clone();
        tokio::spawn(async move {
            while let Some(event) = mesh_rx.recv().await {
                match event {
                    MeshEvent::PeerConnected(peer_info) => {
                        peers.write().await.push(peer_info.clone());
                        let _ = event_tx.send(NodeEvent::PeerConnected(peer_info));
                    }
                    MeshEvent::PeerDisconnected(peer_id) => {
                        peers.write().await.retain(|p| p.peer_id != peer_id);
                        let _ = event_tx.send(NodeEvent::PeerDisconnected(peer_id));
                    }
                    MeshEvent::MessageReceived { from, message } => {
                        let _ = event_tx.send(NodeEvent::MessageReceived { from, message });
                    }
                    MeshEvent::TopologyChanged => {}
                }
            }
        });

        let _ = self.event_tx.send(NodeEvent::Started {
            peer_id,
            address: addr.to_string(),
        });

        Ok(())
    }

    /// Run the node (blocks until shutdown)
    pub async fn run(&mut self) -> Result<()> {
        if let Some(mesh) = self.mesh.as_mut() {
            mesh.run().await?;
        }
        Ok(())
    }

    /// Broadcast a message to the network
    pub fn broadcast(&mut self, topic: &str, message: &MeshMessage) -> Result<()> {
        if let Some(mesh) = self.mesh.as_mut() {
            mesh.publish(topic, message)?;
        }
        Ok(())
    }

    /// Announce capabilities to the network with resources
    pub async fn announce(&mut self) -> Result<()> {
        // Collect system resources
        let resources = crate::resources::collect_resources().await.ok();

        // Announce on mesh
        if let Some(mesh) = self.mesh.as_mut() {
            mesh.announce(
                self.identity.address().to_string(),
                self.config.capabilities.clone(),
                resources.clone(),
            )?;
        }

        // Announce on relay
        if let Some(relay) = &self.relay {
            relay
                .announce(
                    self.identity.address(),
                    &self.config.capabilities,
                    resources.as_ref(),
                    self.config.device_name.as_deref(),
                )
                .await?;
        }

        Ok(())
    }

    /// Send heartbeat with current resource status
    pub async fn send_heartbeat(&mut self) -> Result<()> {
        // Collect fresh resources
        let resources = crate::resources::collect_resources().await.ok();

        // Send via mesh (don't fail if mesh has insufficient peers)
        if let Some(mesh) = self.mesh.as_mut() {
            if let Err(e) = mesh.send_heartbeat(resources.clone()) {
                tracing::debug!("Mesh heartbeat failed (expected if no peers): {}", e);
            }
        }

        // Send via relay (with full peer announcement format for reward tracker)
        if let Some(relay) = &self.relay {
            // Build PeerAnnouncement for reward tracking
            let announcement = crate::relay::PeerAnnouncement {
                node_id: format!("apn_{}", &self.identity.address()[2..10]),
                wallet_address: self.identity.address().to_string(),
                capabilities: self.config.capabilities.clone(),
                timestamp: chrono::Utc::now().to_rfc3339(),
                resources: resources.clone(),
                hostname: crate::resources::get_hostname(),
                device_name: self.config.device_name.clone(),
            };

            let payload = serde_json::to_vec(&announcement)?;
            relay.publish("apn.heartbeat", &payload).await?;

            tracing::debug!(
                "💓 Published heartbeat to apn.heartbeat with hostname={:?}",
                announcement.hostname
            );
        }

        Ok(())
    }

    /// Send a direct message to a peer via relay
    pub async fn send_direct(&self, recipient: &str, message: &MeshMessage) -> Result<()> {
        if let Some(relay) = &self.relay {
            relay.send_direct(recipient, message).await?;
        } else {
            anyhow::bail!("Relay not configured");
        }
        Ok(())
    }
}

/// Builder for AlphaNode
pub struct AlphaNodeBuilder {
    config: NodeConfig,
    mnemonic: Option<String>,
    identity: Option<NodeIdentity>,
}

impl AlphaNodeBuilder {
    pub fn new() -> Self {
        Self {
            config: NodeConfig::default(),
            mnemonic: None,
            identity: None,
        }
    }

    pub fn with_port(mut self, port: u16) -> Self {
        self.config.p2p_port = port;
        self
    }

    pub fn with_relay(mut self, url: &str) -> Self {
        self.config.use_relay = true;
        self.config.relay_url = url.to_string();
        self
    }

    pub fn without_relay(mut self) -> Self {
        self.config.use_relay = false;
        self
    }

    pub fn with_bootstrap_peers(mut self, peers: Vec<String>) -> Self {
        self.config.bootstrap_peers = peers;
        self
    }

    pub fn with_capabilities(mut self, capabilities: Vec<String>) -> Self {
        self.config.capabilities = capabilities;
        self
    }

    pub fn with_capability(mut self, capability: &str) -> Self {
        self.config.capabilities.push(capability.to_string());
        self
    }

    pub fn with_mnemonic(mut self, mnemonic: &str) -> Self {
        self.mnemonic = Some(mnemonic.to_string());
        self
    }

    pub fn with_identity(mut self, identity: NodeIdentity) -> Self {
        self.identity = Some(identity);
        self
    }

    pub fn with_device_name(mut self, name: &str) -> Self {
        self.config.device_name = Some(name.to_string());
        self
    }

    pub fn build(self, event_tx: mpsc::UnboundedSender<NodeEvent>) -> Result<AlphaNode> {
        // Priority: provided identity > mnemonic > generate new
        if let Some(identity) = self.identity {
            Ok(AlphaNode {
                identity,
                config: self.config,
                mesh: None,
                relay: None,
                event_tx,
                peers: Arc::new(RwLock::new(Vec::new())),
            })
        } else if let Some(mnemonic) = self.mnemonic {
            AlphaNode::with_identity(&mnemonic, self.config, event_tx)
        } else {
            AlphaNode::new(self.config, event_tx)
        }
    }
}

impl Default for AlphaNodeBuilder {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_node_builder() {
        let (tx, _rx) = mpsc::unbounded_channel();

        let node = AlphaNodeBuilder::new()
            .with_port(4002)
            .with_capabilities(vec!["compute".to_string(), "mining".to_string()])
            .build(tx);

        assert!(node.is_ok());

        let node = node.unwrap();
        assert!(node.address().starts_with("0x"));
    }

    #[test]
    fn test_node_with_mnemonic() {
        let (tx, _rx) = mpsc::unbounded_channel();

        // First create a node to get a mnemonic
        let node1 = AlphaNode::new(NodeConfig::default(), tx.clone()).unwrap();
        let mnemonic = node1.identity().mnemonic_phrase();

        // Import with same mnemonic
        let node2 = AlphaNodeBuilder::new()
            .with_mnemonic(&mnemonic)
            .build(tx)
            .unwrap();

        assert_eq!(node1.address(), node2.address());
    }
}
