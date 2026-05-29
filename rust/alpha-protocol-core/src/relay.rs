//! NATS Relay - Fallback for NAT traversal
//!
//! When direct P2P connections fail due to NAT, the NATS relay at
//! nonlocal.info:4222 provides message routing.

use anyhow::{Context, Result};
use async_nats::{Client, ConnectOptions};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

use crate::mesh::MeshMessage;

/// NATS relay configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelayConfig {
    /// NATS server URL
    pub url: String,
    /// Node identifier
    pub node_id: String,
    /// Reconnect on disconnect
    pub auto_reconnect: bool,
}

impl Default for RelayConfig {
    fn default() -> Self {
        Self {
            url: crate::DEFAULT_NATS_RELAY.to_string(),
            node_id: String::new(),
            auto_reconnect: true,
        }
    }
}

/// NATS relay client for NAT traversal
pub struct NatsRelay {
    config: RelayConfig,
    client: Option<Client>,
    message_tx: mpsc::UnboundedSender<RelayEvent>,
}

/// Events from the relay
#[derive(Debug, Clone)]
pub enum RelayEvent {
    Connected,
    Disconnected,
    MessageReceived { subject: String, payload: Vec<u8> },
    Error(String),
}

/// NATS subjects for APN
pub mod subjects {
    /// Peer registry: apn.registry.<node_id>
    pub fn registry(node_id: &str) -> String {
        format!("apn.registry.{}", node_id)
    }

    /// Peer discovery broadcast
    pub const DISCOVERY: &str = "apn.discovery";

    /// Task announcements
    pub const TASKS: &str = "apn.tasks";

    /// Heartbeat/status updates
    pub const HEARTBEAT: &str = "apn.heartbeat";

    /// Direct messages: apn.dm.<recipient_node_id>
    pub fn direct_message(recipient: &str) -> String {
        format!("apn.dm.{}", recipient)
    }

    /// Signaling for WebRTC/P2P setup: apn.signal.<node_id>
    pub fn signaling(node_id: &str) -> String {
        format!("apn.signal.{}", node_id)
    }
}

impl NatsRelay {
    /// Create a new NATS relay client
    pub fn new(config: RelayConfig, message_tx: mpsc::UnboundedSender<RelayEvent>) -> Self {
        Self {
            config,
            client: None,
            message_tx,
        }
    }

    /// Clone the relay for spawning the listener task
    /// This shares the same NATS client connection
    pub fn clone_for_listener(&self) -> Self {
        Self {
            config: self.config.clone(),
            client: self.client.clone(),
            message_tx: self.message_tx.clone(),
        }
    }

    /// Connect to the NATS server
    pub async fn connect(&mut self) -> Result<()> {
        let options = ConnectOptions::new()
            .name(&self.config.node_id)
            .retry_on_initial_connect();

        let client = async_nats::connect_with_options(&self.config.url, options)
            .await
            .context("Failed to connect to NATS relay")?;

        tracing::info!("Connected to NATS relay at {}", self.config.url);
        self.client = Some(client);

        let _ = self.message_tx.send(RelayEvent::Connected);
        Ok(())
    }

    /// Disconnect from the NATS server
    pub async fn disconnect(&mut self) -> Result<()> {
        if let Some(client) = self.client.take() {
            client.flush().await?;
            // Client will close when dropped
        }

        let _ = self.message_tx.send(RelayEvent::Disconnected);
        Ok(())
    }

    /// Publish a message to a subject
    pub async fn publish(&self, subject: &str, payload: &[u8]) -> Result<()> {
        let client = self.client.as_ref().context("Not connected to NATS")?;

        client
            .publish(subject.to_string(), payload.to_vec().into())
            .await
            .context("Failed to publish message")?;

        tracing::debug!("Published {} bytes to {}", payload.len(), subject);
        Ok(())
    }

    /// Publish a mesh message
    pub async fn publish_message(&self, subject: &str, message: &MeshMessage) -> Result<()> {
        let payload = serde_json::to_vec(message)?;
        self.publish(subject, &payload).await
    }

    /// Subscribe to a subject
    pub async fn subscribe(&self, subject: &str) -> Result<async_nats::Subscriber> {
        let client = self.client.as_ref().context("Not connected to NATS")?;

        let subscriber = client
            .subscribe(subject.to_string())
            .await
            .context("Failed to subscribe")?;

        tracing::debug!("Subscribed to {}", subject);
        Ok(subscriber)
    }

    /// Announce this node to the network
    pub async fn announce(
        &self,
        wallet_address: &str,
        capabilities: &[String],
        resources: Option<&crate::wire::NodeResources>,
        device_name: Option<&str>,
    ) -> Result<()> {
        let announcement = PeerAnnouncement {
            node_id: self.config.node_id.clone(),
            wallet_address: wallet_address.to_string(),
            capabilities: capabilities.to_vec(),
            timestamp: chrono::Utc::now().to_rfc3339(),
            resources: resources.cloned(),
            hostname: crate::resources::get_hostname(),
            device_name: device_name.map(String::from),
        };

        let payload = serde_json::to_vec(&announcement)?;

        // Publish to registry
        self.publish(&subjects::registry(&self.config.node_id), &payload)
            .await?;

        // Broadcast to discovery
        self.publish(subjects::DISCOVERY, &payload).await?;

        Ok(())
    }

    /// Send heartbeat with resource status
    pub async fn send_heartbeat(
        &self,
        resources: Option<&crate::wire::NodeResources>,
    ) -> Result<()> {
        let heartbeat = serde_json::json!({
            "node_id": self.config.node_id,
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "resources": resources,
        });

        let payload = serde_json::to_vec(&heartbeat)?;
        self.publish(subjects::HEARTBEAT, &payload).await?;

        tracing::debug!("Heartbeat sent with resources");
        Ok(())
    }

    /// Send a direct message to another node
    pub async fn send_direct(&self, recipient: &str, message: &MeshMessage) -> Result<()> {
        let subject = subjects::direct_message(recipient);
        self.publish_message(&subject, message).await
    }

    /// Request-reply pattern for signaling
    pub async fn request(&self, subject: &str, payload: &[u8]) -> Result<Vec<u8>> {
        let client = self.client.as_ref().context("Not connected to NATS")?;

        let response = client
            .request(subject.to_string(), payload.to_vec().into())
            .await
            .context("Request failed")?;

        Ok(response.payload.to_vec())
    }

    /// Run the relay event loop (processes incoming messages)
    pub async fn run(&self) -> Result<()> {
        let client = self.client.as_ref().context("Not connected to NATS")?;

        // Subscribe to our direct message subject
        let dm_subject = subjects::direct_message(&self.config.node_id);
        let mut dm_subscriber = client.subscribe(dm_subject.clone()).await?;

        // Subscribe to signaling
        let signal_subject = subjects::signaling(&self.config.node_id);
        let mut signal_subscriber = client.subscribe(signal_subject.clone()).await?;

        // Subscribe to discovery
        let mut discovery_subscriber = client.subscribe(subjects::DISCOVERY.to_string()).await?;

        // Subscribe to heartbeat
        let mut heartbeat_subscriber = client.subscribe(subjects::HEARTBEAT.to_string()).await?;

        tracing::info!(
            "Relay listening on: {}, {}, {}, {}",
            dm_subject,
            signal_subject,
            subjects::DISCOVERY,
            subjects::HEARTBEAT
        );

        loop {
            tokio::select! {
                Some(msg) = dm_subscriber.next() => {
                    let _ = self.message_tx.send(RelayEvent::MessageReceived {
                        subject: msg.subject.to_string(),
                        payload: msg.payload.to_vec(),
                    });
                }

                Some(msg) = signal_subscriber.next() => {
                    let _ = self.message_tx.send(RelayEvent::MessageReceived {
                        subject: msg.subject.to_string(),
                        payload: msg.payload.to_vec(),
                    });
                }

                Some(msg) = discovery_subscriber.next() => {
                    let _ = self.message_tx.send(RelayEvent::MessageReceived {
                        subject: msg.subject.to_string(),
                        payload: msg.payload.to_vec(),
                    });
                }

                Some(msg) = heartbeat_subscriber.next() => {
                    let _ = self.message_tx.send(RelayEvent::MessageReceived {
                        subject: msg.subject.to_string(),
                        payload: msg.payload.to_vec(),
                    });
                }
            }
        }
    }

    /// Check if connected
    pub fn is_connected(&self) -> bool {
        self.client.is_some()
    }
}

/// Peer announcement message
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerAnnouncement {
    pub node_id: String,
    pub wallet_address: String,
    pub capabilities: Vec<String>,
    pub timestamp: String,
    pub resources: Option<crate::wire::NodeResources>,
    #[serde(default)]
    pub hostname: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub device_name: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_relay_config_default() {
        let config = RelayConfig::default();
        assert_eq!(config.url, crate::DEFAULT_NATS_RELAY);
    }

    #[test]
    fn test_subjects() {
        assert_eq!(subjects::registry("node123"), "apn.registry.node123");
        assert_eq!(subjects::direct_message("peer456"), "apn.dm.peer456");
        assert_eq!(subjects::signaling("node789"), "apn.signal.node789");
    }
}
