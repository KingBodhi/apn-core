use std::sync::Arc;

///! Reward Distributor - Batches and distributes VIBE rewards to peer wallets
///!
///! This service:
///! - Collects pending rewards from database
///! - Batches multiple rewards into single transactions
///! - Sends tokens from rewards wallet to peer wallets
///! - Tracks confirmations on Aptos blockchain
///! - Handles failures and retries
use anyhow::{Context, Result};
use sqlx::SqlitePool;
use tokio::time::{interval, Duration};
use uuid::Uuid;

use crate::identity::NodeIdentity;

/// Configuration for reward distribution
#[derive(Debug, Clone)]
pub struct DistributorConfig {
    /// Rewards wallet mnemonic
    pub rewards_wallet_mnemonic: String,
    /// Minimum VIBE to distribute (don't distribute tiny amounts)
    pub min_distribution_amount: i64,
    /// Maximum rewards per batch
    pub batch_size: usize,
    /// Distribution interval (seconds)
    pub distribution_interval_secs: u64,
    /// Aptos node URL
    pub aptos_node_url: String,
}

impl Default for DistributorConfig {
    fn default() -> Self {
        Self {
            rewards_wallet_mnemonic: String::new(),
            min_distribution_amount: 100_000_000, // 1 VIBE minimum
            batch_size: 50,                       // Max 50 rewards per batch
            distribution_interval_secs: 300,      // 5 minutes
            aptos_node_url: "https://fullnode.testnet.aptoslabs.com/v1".to_string(),
        }
    }
}

/// Reward distribution batch
#[derive(Debug, Clone)]
struct DistributionBatch {
    batch_id: Uuid,
    rewards: Vec<Uuid>,
    total_amount: i64,
    recipients: Vec<(String, i64)>, // (wallet_address, amount)
}

/// Reward Distributor Service
pub struct RewardDistributor {
    db: SqlitePool,
    config: DistributorConfig,
    rewards_wallet: Option<NodeIdentity>,
}

impl RewardDistributor {
    pub fn new(db: SqlitePool, config: DistributorConfig) -> Self {
        Self {
            db,
            config,
            rewards_wallet: None,
        }
    }

    /// Initialize the distributor (load rewards wallet)
    pub async fn init(&mut self) -> Result<()> {
        tracing::info!("🔐 Loading rewards wallet...");

        // Import rewards wallet from mnemonic
        let wallet = NodeIdentity::from_mnemonic_phrase(&self.config.rewards_wallet_mnemonic)
            .context("Failed to load rewards wallet")?;

        tracing::info!("✅ Rewards wallet loaded: {}", wallet.address());
        self.rewards_wallet = Some(wallet);

        Ok(())
    }

    /// Start the distributor service
    pub async fn start(self: Arc<Self>) -> Result<()> {
        tracing::info!(
            "💸 Starting Reward Distributor (every {}s)",
            self.config.distribution_interval_secs
        );

        let mut ticker = interval(Duration::from_secs(self.config.distribution_interval_secs));

        loop {
            ticker.tick().await;

            if let Err(e) = self.distribute_pending_rewards().await {
                tracing::error!("Failed to distribute rewards: {}", e);
            }
        }
    }

    /// Main distribution logic
    async fn distribute_pending_rewards(&self) -> Result<()> {
        use crate::models::peer_reward::PeerReward;

        // Get pending rewards from database
        let pending =
            PeerReward::list_pending_for_distribution(&self.db, self.config.batch_size as i64)
                .await
                .context("Failed to fetch pending rewards")?;

        if pending.is_empty() {
            tracing::debug!("No pending rewards to distribute");
            return Ok(());
        }

        tracing::info!("📦 Found {} pending rewards to distribute", pending.len());

        // Group rewards by peer wallet
        let mut grouped: std::collections::HashMap<String, Vec<Uuid>> =
            std::collections::HashMap::new();
        let mut total_per_peer: std::collections::HashMap<String, i64> =
            std::collections::HashMap::new();

        for reward in &pending {
            // Get peer wallet address
            let peer_node =
                crate::models::peer_node::PeerNode::find_by_id(&self.db, reward.peer_node_id)
                    .await?
                    .ok_or_else(|| anyhow::anyhow!("Peer node not found"))?;

            grouped
                .entry(peer_node.wallet_address.clone())
                .or_default()
                .push(reward.id);

            *total_per_peer
                .entry(peer_node.wallet_address.clone())
                .or_insert(0) += reward.final_amount;
        }

        // Create distribution batch
        let batch = self.create_batch(grouped, total_per_peer).await?;

        tracing::info!(
            "🎁 Created batch #{} with {} peers, total {} VIBE",
            batch.batch_id,
            batch.recipients.len(),
            crate::economics::vibe_to_display(batch.total_amount as u64)
        );

        // Distribute to blockchain
        self.send_batch_to_blockchain(&batch).await?;

        Ok(())
    }

    /// Create a distribution batch in the database
    async fn create_batch(
        &self,
        grouped: std::collections::HashMap<String, Vec<Uuid>>,
        total_per_peer: std::collections::HashMap<String, i64>,
    ) -> Result<DistributionBatch> {
        use crate::models::peer_reward::{PeerReward, RewardStatus};

        let batch_id = Uuid::new_v4();

        // Get next batch number
        let batch_number: i64 =
            sqlx::query_scalar("SELECT COALESCE(MAX(batch_number), 0) + 1 FROM reward_batches")
                .fetch_one(&self.db)
                .await?;

        let total_rewards = grouped.values().map(|v| v.len()).sum::<usize>();
        let total_amount: i64 = total_per_peer.values().sum();

        let rewards_wallet = self
            .rewards_wallet
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("Rewards wallet not loaded"))?;

        let wallet_address = rewards_wallet.address().to_string();
        let total_rewards_i64 = total_rewards as i64;

        // Create batch record
        sqlx::query!(
            r#"
            INSERT INTO reward_batches (
                id, batch_number, total_rewards, total_amount, from_wallet
            )
            VALUES ($1, $2, $3, $4, $5)
            "#,
            batch_id,
            batch_number,
            total_rewards_i64,
            total_amount,
            wallet_address
        )
        .execute(&self.db)
        .await?;

        // Update all rewards in this batch
        for reward_ids in grouped.values() {
            for reward_id in reward_ids {
                PeerReward::update_status(
                    &self.db,
                    *reward_id,
                    RewardStatus::Batched,
                    Some(batch_id),
                )
                .await?;
            }
        }

        // Build recipient list
        let recipients: Vec<(String, i64)> = total_per_peer.into_iter().collect();

        Ok(DistributionBatch {
            batch_id,
            rewards: grouped.values().flatten().copied().collect(),
            total_amount,
            recipients,
        })
    }

    /// Send batch to Aptos blockchain
    async fn send_batch_to_blockchain(&self, batch: &DistributionBatch) -> Result<()> {
        let _rewards_wallet = self
            .rewards_wallet
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("Rewards wallet not loaded"))?;

        tracing::info!("🚀 Sending batch to Aptos blockchain...");

        // For each recipient, send tokens
        for (recipient_address, amount) in &batch.recipients {
            // Skip if below minimum
            if *amount < self.config.min_distribution_amount {
                tracing::debug!(
                    "Skipping {} - amount too small: {} VIBE",
                    recipient_address,
                    crate::economics::vibe_to_display(*amount as u64)
                );
                continue;
            }

            tracing::info!(
                "💸 Sending {} VIBE to {}...",
                crate::economics::vibe_to_display(*amount as u64),
                recipient_address
            );

            // TODO: Actually send Aptos transaction
            // For now, simulate the transaction
            let tx_hash = self
                .simulate_aptos_transfer(recipient_address, *amount)
                .await?;

            tracing::info!("✅ Transaction submitted: {}", tx_hash);

            // Mark rewards as distributed
            for reward_id in &batch.rewards {
                crate::models::peer_reward::PeerReward::mark_distributed(
                    &self.db, *reward_id, &tx_hash,
                    None, // block_height will be filled when confirmed
                )
                .await?;
            }
        }

        // Update batch status
        self.mark_batch_submitted(batch.batch_id, "simulated_tx_hash")
            .await?;

        tracing::info!("🎉 Batch distribution complete!");

        Ok(())
    }

    /// Simulate Aptos token transfer (placeholder for real implementation)
    async fn simulate_aptos_transfer(&self, _recipient: &str, _amount: i64) -> Result<String> {
        // TODO: Implement actual Aptos transfer using aptos-sdk
        // This would involve:
        // 1. Building a coin::transfer transaction
        // 2. Signing with rewards wallet private key
        // 3. Submitting to Aptos node
        // 4. Returning transaction hash

        // For now, return simulated hash
        tokio::time::sleep(Duration::from_millis(100)).await;
        Ok(format!("0x{}", hex::encode(Uuid::new_v4().as_bytes())))
    }

    /// Mark batch as submitted to blockchain
    async fn mark_batch_submitted(&self, batch_id: Uuid, tx_hash: &str) -> Result<()> {
        sqlx::query!(
            r#"
            UPDATE reward_batches
            SET status = 'submitted',
                aptos_tx_hash = $2,
                submitted_at = datetime('now', 'subsec'),
                updated_at = datetime('now', 'subsec')
            WHERE id = $1
            "#,
            batch_id,
            tx_hash
        )
        .execute(&self.db)
        .await?;

        Ok(())
    }

    /// Get distributor stats
    pub async fn get_stats(&self) -> Result<DistributorStats> {
        use crate::models::peer_reward::PeerReward;

        let total_pending = PeerReward::total_pending_amount(&self.db).await?;

        let (total_distributed,): (i64,) = sqlx::query_as(
            r#"
            SELECT COALESCE(SUM(final_amount), 0)
            FROM peer_rewards
            WHERE status IN ('distributed', 'confirmed')
            "#,
        )
        .fetch_one(&self.db)
        .await?;

        let (total_batches,): (i64,) = sqlx::query_as(
            r#"
            SELECT COUNT(*)
            FROM reward_batches
            "#,
        )
        .fetch_one(&self.db)
        .await?;

        Ok(DistributorStats {
            total_pending_vibe: total_pending,
            total_distributed_vibe: total_distributed,
            total_batches: total_batches as usize,
            rewards_wallet: self
                .rewards_wallet
                .as_ref()
                .map(|w| w.address().to_string())
                .unwrap_or_default(),
        })
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct DistributorStats {
    pub total_pending_vibe: i64,
    pub total_distributed_vibe: i64,
    pub total_batches: usize,
    pub rewards_wallet: String,
}
