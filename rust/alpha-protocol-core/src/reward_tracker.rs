use std::{collections::HashMap, sync::Arc};

///! Reward Tracker - Listens to peer heartbeats and calculates VIBE rewards
///!
///! This service:
///! - Subscribes to apn.heartbeat NATS channel
///! - Tracks peer uptime and contributions
///! - Calculates rewards based on economics.rs formulas
///! - Creates reward records in the database
///! - Applies multipliers for GPU, high resources, etc.
use anyhow::{Context, Result};
use async_nats::Client as NatsClient;
use futures::StreamExt;
use sqlx::SqlitePool;
use tokio::{
    sync::RwLock,
    time::{interval, Duration},
};

use crate::{
    economics::{RewardRates, VibeAmount},
    relay::PeerAnnouncement,
    wire::NodeResources,
};

/// Configuration for the reward tracker
#[derive(Debug, Clone)]
pub struct RewardTrackerConfig {
    pub nats_url: String,
    pub reward_interval_secs: u64,
    pub db_path: String,
}

impl Default for RewardTrackerConfig {
    fn default() -> Self {
        Self {
            nats_url: "nats://nonlocal.info:4222".to_string(),
            reward_interval_secs: 60,
            db_path: "sqlite:dev_assets/db.sqlite".to_string(),
        }
    }
}

/// Peer state for reward calculation
#[derive(Debug, Clone)]
#[allow(dead_code)]
struct PeerState {
    node_id: String,
    wallet_address: String,
    last_heartbeat: chrono::DateTime<chrono::Utc>,
    heartbeat_count: u64,
    resources: Option<NodeResources>,
    pending_rewards: VibeAmount,
}

/// Reward Tracker Service
pub struct RewardTracker {
    nats: Option<NatsClient>,
    db: SqlitePool,
    rates: RewardRates,
    peers: Arc<RwLock<HashMap<String, PeerState>>>,
    config: RewardTrackerConfig,
}

impl RewardTracker {
    /// Create a new reward tracker from an existing NATS client
    pub fn new(nats: NatsClient, db: SqlitePool) -> Self {
        Self {
            nats: Some(nats),
            db,
            rates: RewardRates::default(),
            peers: Arc::new(RwLock::new(HashMap::new())),
            config: RewardTrackerConfig::default(),
        }
    }

    /// Create a new reward tracker with config (will connect to NATS later)
    pub fn new_with_config(
        db: SqlitePool,
        config: RewardTrackerConfig,
        rates: RewardRates,
    ) -> Self {
        Self {
            nats: None,
            db,
            rates,
            peers: Arc::new(RwLock::new(HashMap::new())),
            config,
        }
    }

    /// Initialize the tracker (connect to NATS)
    pub async fn init(&mut self) -> Result<()> {
        if self.nats.is_none() {
            tracing::info!("📡 Connecting to NATS: {}", self.config.nats_url);
            let nats_client = async_nats::connect(&self.config.nats_url)
                .await
                .context("Failed to connect to NATS")?;
            self.nats = Some(nats_client);
            tracing::info!("✅ Connected to NATS");
        }
        Ok(())
    }

    /// Start the reward tracker service
    pub async fn start(self: Arc<Self>) -> Result<()> {
        tracing::info!("🎁 Starting Reward Tracker service");

        // Spawn heartbeat listener
        let tracker = self.clone();
        tokio::spawn(async move {
            if let Err(e) = tracker.listen_heartbeats().await {
                tracing::error!("Heartbeat listener error: {}", e);
            }
        });

        // Spawn reward processor (processes rewards every minute)
        let tracker = self.clone();
        tokio::spawn(async move {
            if let Err(e) = tracker.process_rewards_periodically().await {
                tracing::error!("Reward processor error: {}", e);
            }
        });

        tracing::info!("✅ Reward Tracker service started");
        Ok(())
    }

    /// Listen to heartbeats on NATS
    async fn listen_heartbeats(&self) -> Result<()> {
        let nats = self
            .nats
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("NATS not initialized"))?;

        let mut sub = nats
            .subscribe("apn.heartbeat")
            .await
            .context("Failed to subscribe to apn.heartbeat")?;

        tracing::info!("📡 Listening for peer heartbeats on apn.heartbeat");

        while let Some(msg) = sub.next().await {
            if let Err(e) = self.handle_heartbeat(&msg.payload).await {
                tracing::warn!("Failed to handle heartbeat: {}", e);
            }
        }

        Ok(())
    }

    /// Handle a single heartbeat message
    async fn handle_heartbeat(&self, payload: &[u8]) -> Result<()> {
        // Parse heartbeat announcement
        let announcement: PeerAnnouncement =
            serde_json::from_slice(payload).context("Failed to parse heartbeat")?;

        let node_id = format!("apn_{}", &announcement.wallet_address[2..10]); // Extract node_id from wallet
        let wallet = announcement.wallet_address.clone();

        tracing::debug!("💓 Heartbeat from {}", node_id);

        // Update peer state
        let mut peers = self.peers.write().await;
        let peer = peers.entry(node_id.clone()).or_insert_with(|| PeerState {
            node_id: node_id.clone(),
            wallet_address: wallet.clone(),
            last_heartbeat: chrono::Utc::now(),
            heartbeat_count: 0,
            resources: announcement.resources.clone(),
            pending_rewards: 0,
        });

        peer.last_heartbeat = chrono::Utc::now();
        peer.heartbeat_count += 1;
        peer.resources = announcement.resources.clone();

        // Calculate reward for this heartbeat
        let multiplier = self.calculate_multiplier(&peer.resources);
        let reward = (self.rates.heartbeat_base as f64 * multiplier) as VibeAmount;
        peer.pending_rewards += reward;

        tracing::debug!(
            "💰 Peer {} earned {} VIBE ({}x multiplier)",
            node_id,
            crate::economics::vibe_to_display(reward),
            multiplier
        );

        // Update database: register/update peer and update heartbeat
        self.update_peer_in_db(&announcement).await?;

        Ok(())
    }

    /// Calculate reward multiplier based on resources
    fn calculate_multiplier(&self, resources: &Option<NodeResources>) -> f64 {
        let mut multiplier = 1.0;

        if let Some(res) = resources {
            // GPU multiplier
            if res.gpu_available {
                multiplier *= self.rates.gpu_multiplier;
            }

            // High CPU multiplier
            if res.cpu_cores > 16 {
                multiplier *= self.rates.high_cpu_multiplier;
            }

            // High RAM multiplier
            if res.ram_mb > 32768 {
                // 32GB
                multiplier *= self.rates.high_ram_multiplier;
            }
        }

        multiplier
    }

    /// Update peer in database
    async fn update_peer_in_db(&self, announcement: &PeerAnnouncement) -> Result<()> {
        use crate::models::peer_node::{CreatePeerNode, PeerNode};

        let node_id = format!("apn_{}", &announcement.wallet_address[2..10]);

        // Upsert peer node
        let peer_data = CreatePeerNode {
            node_id: node_id.clone(),
            peer_id: None, // We don't have LibP2P peer ID in heartbeat
            wallet_address: announcement.wallet_address.clone(),
            capabilities: Some(announcement.capabilities.clone()),
            cpu_cores: announcement.resources.as_ref().map(|r| r.cpu_cores as i64),
            ram_mb: announcement.resources.as_ref().map(|r| r.ram_mb as i64),
            storage_gb: announcement.resources.as_ref().map(|r| r.storage_gb as i64),
            gpu_available: announcement
                .resources
                .as_ref()
                .map(|r| r.gpu_available)
                .unwrap_or(false),
            gpu_model: announcement
                .resources
                .as_ref()
                .and_then(|r| r.gpu_model.clone()),
            hostname: announcement.hostname.clone(),
        };

        PeerNode::upsert(&self.db, peer_data)
            .await
            .context("Failed to upsert peer node")?;

        // Update heartbeat timestamp
        PeerNode::update_heartbeat(&self.db, &node_id)
            .await
            .context("Failed to update heartbeat")?;

        Ok(())
    }

    /// Process pending rewards periodically (every minute)
    async fn process_rewards_periodically(&self) -> Result<()> {
        let mut ticker = interval(Duration::from_secs(60));

        loop {
            ticker.tick().await;

            if let Err(e) = self.process_pending_rewards().await {
                tracing::error!("Failed to process rewards: {}", e);
            }
        }
    }

    /// Process all pending rewards and create database records
    async fn process_pending_rewards(&self) -> Result<()> {
        use crate::models::{
            peer_node::PeerNode,
            peer_reward::{CreatePeerReward, PeerReward, RewardType},
        };

        let mut peers = self.peers.write().await;

        for (node_id, peer) in peers.iter_mut() {
            if peer.pending_rewards == 0 {
                continue;
            }

            // Get peer from database
            let peer_node = match PeerNode::find_by_node_id(&self.db, node_id).await? {
                Some(p) => p,
                None => {
                    tracing::warn!("Peer {} not found in database", node_id);
                    continue;
                }
            };

            // Calculate multiplier
            let multiplier = self.calculate_multiplier(&peer.resources);

            // Create reward record
            let reward_data = CreatePeerReward {
                peer_node_id: peer_node.id,
                contribution_id: None,
                reward_type: RewardType::Heartbeat,
                base_amount: self.rates.heartbeat_base as i64,
                multiplier,
                description: Some(format!("{} heartbeats", peer.heartbeat_count)),
                metadata: Some(serde_json::json!({
                    "heartbeat_count": peer.heartbeat_count,
                    "has_gpu": peer.resources.as_ref().map(|r| r.gpu_available).unwrap_or(false),
                    "cpu_cores": peer.resources.as_ref().map(|r| r.cpu_cores),
                    "ram_mb": peer.resources.as_ref().map(|r| r.ram_mb),
                })),
            };

            match PeerReward::create(&self.db, reward_data).await {
                Ok(reward) => {
                    tracing::info!(
                        "💎 Created reward for {}: {} VIBE (ID: {})",
                        node_id,
                        crate::economics::vibe_to_display(reward.final_amount as u64),
                        reward.id
                    );
                    peer.pending_rewards = 0;
                    peer.heartbeat_count = 0;
                }
                Err(e) => {
                    tracing::error!("Failed to create reward for {}: {}", node_id, e);
                }
            }
        }

        Ok(())
    }

    /// Get current stats
    pub async fn get_stats(&self) -> RewardTrackerStats {
        let peers = self.peers.read().await;

        let active_peers = peers.len();
        let total_pending = peers
            .values()
            .map(|p| p.pending_rewards)
            .sum::<VibeAmount>();
        let total_heartbeats = peers.values().map(|p| p.heartbeat_count).sum::<u64>();

        RewardTrackerStats {
            active_peers,
            total_pending_rewards: total_pending,
            total_heartbeats,
        }
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct RewardTrackerStats {
    pub active_peers: usize,
    pub total_pending_rewards: VibeAmount,
    pub total_heartbeats: u64,
}
