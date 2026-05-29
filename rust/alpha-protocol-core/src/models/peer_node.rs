use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, SqlitePool};
use uuid::Uuid;

#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct PeerNode {
    pub id: Uuid,
    pub node_id: String,
    pub peer_id: Option<String>,
    pub wallet_address: String,
    pub capabilities: Option<String>,
    pub cpu_cores: Option<i64>,
    pub ram_mb: Option<i64>,
    pub storage_gb: Option<i64>,
    pub gpu_available: bool,
    pub gpu_model: Option<String>,
    pub hostname: Option<String>,
    pub first_seen_at: DateTime<Utc>,
    pub last_heartbeat_at: Option<DateTime<Utc>>,
    pub is_active: bool,
    pub is_banned: bool,
    pub ban_reason: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    #[sqlx(default)]
    pub organization_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct CreatePeerNode {
    pub node_id: String,
    pub peer_id: Option<String>,
    pub wallet_address: String,
    pub capabilities: Option<Vec<String>>,
    pub cpu_cores: Option<i64>,
    pub ram_mb: Option<i64>,
    pub storage_gb: Option<i64>,
    pub gpu_available: bool,
    pub gpu_model: Option<String>,
    pub hostname: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct UpdatePeerResources {
    pub cpu_cores: Option<i64>,
    pub ram_mb: Option<i64>,
    pub storage_gb: Option<i64>,
    pub gpu_available: bool,
    pub gpu_model: Option<String>,
}

impl PeerNode {
    pub async fn upsert(pool: &SqlitePool, data: CreatePeerNode) -> Result<Self, sqlx::Error> {
        let id = Uuid::new_v4();
        let capabilities_json = data
            .capabilities
            .map(|c| serde_json::to_string(&c).unwrap_or_else(|_| "[]".to_string()));

        sqlx::query(
            r#"
            INSERT INTO peer_nodes (
                id, node_id, peer_id, wallet_address, capabilities,
                cpu_cores, ram_mb, storage_gb, gpu_available, gpu_model, hostname,
                last_heartbeat_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'subsec'))
            ON CONFLICT(node_id) DO UPDATE SET
                peer_id = excluded.peer_id,
                wallet_address = excluded.wallet_address,
                capabilities = excluded.capabilities,
                cpu_cores = excluded.cpu_cores,
                ram_mb = excluded.ram_mb,
                storage_gb = excluded.storage_gb,
                gpu_available = excluded.gpu_available,
                gpu_model = excluded.gpu_model,
                hostname = excluded.hostname,
                last_heartbeat_at = datetime('now', 'subsec'),
                updated_at = datetime('now', 'subsec')
            "#,
        )
        .bind(id)
        .bind(&data.node_id)
        .bind(&data.peer_id)
        .bind(&data.wallet_address)
        .bind(&capabilities_json)
        .bind(data.cpu_cores)
        .bind(data.ram_mb)
        .bind(data.storage_gb)
        .bind(data.gpu_available)
        .bind(&data.gpu_model)
        .bind(&data.hostname)
        .execute(pool)
        .await?;

        Self::find_by_node_id(pool, &data.node_id)
            .await?
            .ok_or(sqlx::Error::RowNotFound)
    }

    pub async fn find_by_id(pool: &SqlitePool, id: Uuid) -> Result<Option<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerNode>("SELECT * FROM peer_nodes WHERE id = ?")
            .bind(id)
            .fetch_optional(pool)
            .await
    }

    pub async fn find_by_node_id(
        pool: &SqlitePool,
        node_id: &str,
    ) -> Result<Option<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerNode>("SELECT * FROM peer_nodes WHERE node_id = ?")
            .bind(node_id)
            .fetch_optional(pool)
            .await
    }

    pub async fn find_by_wallet(
        pool: &SqlitePool,
        wallet_address: &str,
    ) -> Result<Option<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerNode>("SELECT * FROM peer_nodes WHERE wallet_address = ?")
            .bind(wallet_address)
            .fetch_optional(pool)
            .await
    }

    pub async fn update_heartbeat(pool: &SqlitePool, node_id: &str) -> Result<(), sqlx::Error> {
        sqlx::query(
            "UPDATE peer_nodes SET last_heartbeat_at = datetime('now', 'subsec'), updated_at = datetime('now', 'subsec') WHERE node_id = ?",
        )
        .bind(node_id)
        .execute(pool)
        .await?;
        Ok(())
    }

    pub async fn update_resources(
        pool: &SqlitePool,
        node_id: &str,
        resources: UpdatePeerResources,
    ) -> Result<(), sqlx::Error> {
        sqlx::query(
            r#"
            UPDATE peer_nodes
            SET cpu_cores = ?, ram_mb = ?, storage_gb = ?,
                gpu_available = ?, gpu_model = ?,
                updated_at = datetime('now', 'subsec')
            WHERE node_id = ?
            "#,
        )
        .bind(resources.cpu_cores)
        .bind(resources.ram_mb)
        .bind(resources.storage_gb)
        .bind(resources.gpu_available)
        .bind(&resources.gpu_model)
        .bind(node_id)
        .execute(pool)
        .await?;
        Ok(())
    }

    pub async fn list_active(pool: &SqlitePool) -> Result<Vec<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerNode>(
            "SELECT * FROM peer_nodes WHERE is_active = 1 AND is_banned = 0 ORDER BY last_heartbeat_at DESC",
        )
        .fetch_all(pool)
        .await
    }

    pub async fn ban(pool: &SqlitePool, node_id: &str, reason: &str) -> Result<(), sqlx::Error> {
        sqlx::query(
            "UPDATE peer_nodes SET is_banned = 1, ban_reason = ?, updated_at = datetime('now', 'subsec') WHERE node_id = ?",
        )
        .bind(reason)
        .bind(node_id)
        .execute(pool)
        .await?;
        Ok(())
    }

    pub async fn unban(pool: &SqlitePool, node_id: &str) -> Result<(), sqlx::Error> {
        sqlx::query(
            "UPDATE peer_nodes SET is_banned = 0, ban_reason = NULL, updated_at = datetime('now', 'subsec') WHERE node_id = ?",
        )
        .bind(node_id)
        .execute(pool)
        .await?;
        Ok(())
    }

    pub async fn cleanup_duplicates(pool: &SqlitePool) -> Result<i64, sqlx::Error> {
        let result = sqlx::query(
            r#"
            UPDATE peer_nodes SET is_active = 0
            WHERE id IN (
                SELECT p1.id FROM peer_nodes p1
                INNER JOIN peer_nodes p2 ON
                    p1.cpu_cores = p2.cpu_cores AND p1.ram_mb = p2.ram_mb
                    AND ((p1.gpu_model = p2.gpu_model AND p1.gpu_model IS NOT NULL)
                         OR (p1.gpu_model IS NULL AND p2.gpu_model IS NULL))
                    AND p1.node_id != p2.node_id
                    AND p1.last_heartbeat_at < p2.last_heartbeat_at
                WHERE p1.is_active = 1
            )
            "#,
        )
        .execute(pool)
        .await?;
        Ok(result.rows_affected() as i64)
    }

    pub async fn mark_stale_inactive(pool: &SqlitePool) -> Result<i64, sqlx::Error> {
        let result = sqlx::query(
            "UPDATE peer_nodes SET is_active = 0 WHERE is_active = 1 AND last_heartbeat_at < datetime('now', '-5 minutes')",
        )
        .execute(pool)
        .await?;
        Ok(result.rows_affected() as i64)
    }

    pub async fn find_by_organization(
        pool: &SqlitePool,
        organization_id: &str,
    ) -> Result<Vec<Self>, sqlx::Error> {
        sqlx::query_as::<_, PeerNode>(
            "SELECT * FROM peer_nodes WHERE organization_id = ? AND is_active = 1 ORDER BY last_heartbeat_at DESC",
        )
        .bind(organization_id)
        .fetch_all(pool)
        .await
    }
}
