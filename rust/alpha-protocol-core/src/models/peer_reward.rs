use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, SqlitePool};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum RewardType {
    Heartbeat,
    Task,
    Resource,
    Mining,
    Bonus,
}

impl std::fmt::Display for RewardType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RewardType::Heartbeat => write!(f, "heartbeat"),
            RewardType::Task => write!(f, "task"),
            RewardType::Resource => write!(f, "resource"),
            RewardType::Mining => write!(f, "mining"),
            RewardType::Bonus => write!(f, "bonus"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum RewardStatus {
    Pending,
    Batched,
    Distributed,
    Confirmed,
    Failed,
}

impl std::fmt::Display for RewardStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RewardStatus::Pending => write!(f, "pending"),
            RewardStatus::Batched => write!(f, "batched"),
            RewardStatus::Distributed => write!(f, "distributed"),
            RewardStatus::Confirmed => write!(f, "confirmed"),
            RewardStatus::Failed => write!(f, "failed"),
        }
    }
}

#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct PeerReward {
    pub id: Uuid,
    pub peer_node_id: Uuid,
    pub contribution_id: Option<Uuid>,
    pub reward_type: String,
    pub base_amount: i64,
    pub multiplier: f64,
    pub final_amount: i64,
    pub status: String,
    pub batch_id: Option<Uuid>,
    pub aptos_tx_hash: Option<String>,
    pub block_height: Option<i64>,
    pub error_message: Option<String>,
    pub retry_count: i64,
    pub description: Option<String>,
    pub metadata: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub distributed_at: Option<DateTime<Utc>>,
    pub confirmed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Deserialize)]
pub struct CreatePeerReward {
    pub peer_node_id: Uuid,
    pub contribution_id: Option<Uuid>,
    pub reward_type: RewardType,
    pub base_amount: i64,
    pub multiplier: f64,
    pub description: Option<String>,
    pub metadata: Option<serde_json::Value>,
}

#[derive(Debug, FromRow, Serialize)]
pub struct PeerRewardSummary {
    pub peer_node_id: Uuid,
    pub node_id: String,
    pub wallet_address: String,
    pub pending_vibe: i64,
    pub distributed_vibe: i64,
    pub confirmed_vibe: i64,
    pub total_earned: i64,
    pub reward_count: i64,
}

impl PeerReward {
    pub async fn create(pool: &SqlitePool, data: CreatePeerReward) -> Result<Self, sqlx::Error> {
        let id = Uuid::new_v4();
        let reward_type_str = data.reward_type.to_string();
        let final_amount = (data.base_amount as f64 * data.multiplier) as i64;
        let metadata_str = data.metadata.map(|v| v.to_string());

        sqlx::query_as::<_, PeerReward>(
            r#"
            INSERT INTO peer_rewards (
                id, peer_node_id, contribution_id, reward_type,
                base_amount, multiplier, final_amount,
                description, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            "#,
        )
        .bind(id)
        .bind(data.peer_node_id)
        .bind(data.contribution_id)
        .bind(&reward_type_str)
        .bind(data.base_amount)
        .bind(data.multiplier)
        .bind(final_amount)
        .bind(&data.description)
        .bind(&metadata_str)
        .fetch_one(pool)
        .await
    }

    pub async fn list_by_peer(
        pool: &SqlitePool,
        peer_node_id: Uuid,
        limit: i64,
    ) -> Result<Vec<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerReward>(
            "SELECT * FROM peer_rewards WHERE peer_node_id = ? ORDER BY created_at DESC LIMIT ?",
        )
        .bind(peer_node_id)
        .bind(limit)
        .fetch_all(pool)
        .await
    }

    pub async fn get_pending(
        pool: &SqlitePool,
        peer_node_id: Uuid,
    ) -> Result<Vec<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerReward>(
            "SELECT * FROM peer_rewards WHERE peer_node_id = ? AND status = 'pending' ORDER BY created_at ASC",
        )
        .bind(peer_node_id)
        .fetch_all(pool)
        .await
    }

    pub async fn list_pending_for_distribution(
        pool: &SqlitePool,
        limit: i64,
    ) -> Result<Vec<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerReward>(
            "SELECT * FROM peer_rewards WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
        )
        .bind(limit)
        .fetch_all(pool)
        .await
    }

    pub async fn update_status(
        pool: &SqlitePool,
        id: Uuid,
        status: RewardStatus,
        batch_id: Option<Uuid>,
    ) -> Result<Self, sqlx::Error> {
        let status_str = status.to_string();
        sqlx::query_as::<_, PeerReward>(
            r#"
            UPDATE peer_rewards
            SET status = ?, batch_id = ?, updated_at = datetime('now', 'subsec')
            WHERE id = ?
            RETURNING *
            "#,
        )
        .bind(&status_str)
        .bind(batch_id)
        .bind(id)
        .fetch_one(pool)
        .await
    }

    pub async fn mark_distributed(
        pool: &SqlitePool,
        id: Uuid,
        tx_hash: &str,
        block_height: Option<i64>,
    ) -> Result<Self, sqlx::Error> {
        sqlx::query_as::<_, PeerReward>(
            r#"
            UPDATE peer_rewards
            SET status = 'distributed', aptos_tx_hash = ?, block_height = ?,
                distributed_at = datetime('now', 'subsec'), updated_at = datetime('now', 'subsec')
            WHERE id = ?
            RETURNING *
            "#,
        )
        .bind(tx_hash)
        .bind(block_height)
        .bind(id)
        .fetch_one(pool)
        .await
    }

    pub async fn mark_confirmed(pool: &SqlitePool, id: Uuid) -> Result<Self, sqlx::Error> {
        sqlx::query_as::<_, PeerReward>(
            r#"
            UPDATE peer_rewards
            SET status = 'confirmed', confirmed_at = datetime('now', 'subsec'),
                updated_at = datetime('now', 'subsec')
            WHERE id = ?
            RETURNING *
            "#,
        )
        .bind(id)
        .fetch_one(pool)
        .await
    }

    pub async fn mark_failed(
        pool: &SqlitePool,
        id: Uuid,
        error: &str,
    ) -> Result<Self, sqlx::Error> {
        sqlx::query_as::<_, PeerReward>(
            r#"
            UPDATE peer_rewards
            SET status = 'failed', error_message = ?,
                retry_count = retry_count + 1, updated_at = datetime('now', 'subsec')
            WHERE id = ?
            RETURNING *
            "#,
        )
        .bind(error)
        .bind(id)
        .fetch_one(pool)
        .await
    }

    pub async fn get_summary(
        pool: &SqlitePool,
        peer_node_id: Uuid,
    ) -> Result<PeerRewardSummary, sqlx::Error> {
        sqlx::query_as::<_, PeerRewardSummary>(
            r#"
            SELECT
                pr.peer_node_id,
                pn.node_id,
                pn.wallet_address,
                COALESCE(SUM(CASE WHEN pr.status = 'pending' THEN pr.final_amount ELSE 0 END), 0) as pending_vibe,
                COALESCE(SUM(CASE WHEN pr.status = 'distributed' THEN pr.final_amount ELSE 0 END), 0) as distributed_vibe,
                COALESCE(SUM(CASE WHEN pr.status = 'confirmed' THEN pr.final_amount ELSE 0 END), 0) as confirmed_vibe,
                COALESCE(SUM(pr.final_amount), 0) as total_earned,
                COUNT(*) as reward_count
            FROM peer_rewards pr
            JOIN peer_nodes pn ON pn.id = pr.peer_node_id
            WHERE pr.peer_node_id = ?
            GROUP BY pr.peer_node_id, pn.node_id, pn.wallet_address
            "#,
        )
        .bind(peer_node_id)
        .fetch_one(pool)
        .await
    }

    pub async fn total_pending_amount(pool: &SqlitePool) -> Result<i64, sqlx::Error> {
        let result: (i64,) =
            sqlx::query_as("SELECT COALESCE(SUM(final_amount), 0) FROM peer_rewards WHERE status = 'pending'")
                .fetch_one(pool)
                .await?;
        Ok(result.0)
    }
}
