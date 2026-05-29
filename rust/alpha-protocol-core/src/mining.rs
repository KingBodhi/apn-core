//! Bitcoin Mining Integration
//!
//! Enables APN nodes to contribute hashpower to mining pools
//! and receive Vibe tokens proportional to their contribution.

use serde::{Deserialize, Serialize};

/// Mining pool configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MiningPoolConfig {
    /// Stratum server URL (e.g., stratum+tcp://pool.example.com:3333)
    pub stratum_url: String,
    /// Worker name (typically wallet address or username)
    pub worker_name: String,
    /// Worker password (often 'x' for most pools)
    pub worker_password: String,
    /// Target difficulty
    pub difficulty: u64,
}

impl Default for MiningPoolConfig {
    fn default() -> Self {
        Self {
            stratum_url: "stratum+tcp://public-pool.io:21496".to_string(),
            worker_name: "apn_node".to_string(),
            worker_password: "x".to_string(),
            difficulty: 1,
        }
    }
}

/// Mining statistics
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct MiningStats {
    /// Current hashrate (H/s)
    pub hashrate: u64,
    /// Shares submitted
    pub shares_submitted: u64,
    /// Shares accepted
    pub shares_accepted: u64,
    /// Shares rejected
    pub shares_rejected: u64,
    /// Total hashes computed
    pub total_hashes: u64,
    /// Mining uptime (seconds)
    pub uptime_seconds: u64,
    /// Estimated earnings (satoshis)
    pub estimated_earnings_sats: u64,
}

impl MiningStats {
    /// Calculate acceptance rate (0-100)
    pub fn acceptance_rate(&self) -> f64 {
        if self.shares_submitted == 0 {
            100.0
        } else {
            (self.shares_accepted as f64 / self.shares_submitted as f64) * 100.0
        }
    }

    /// Merge another stats snapshot
    pub fn merge(&mut self, other: &MiningStats) {
        self.hashrate = other.hashrate; // Current, not cumulative
        self.shares_submitted += other.shares_submitted;
        self.shares_accepted += other.shares_accepted;
        self.shares_rejected += other.shares_rejected;
        self.total_hashes += other.total_hashes;
        self.uptime_seconds += other.uptime_seconds;
        self.estimated_earnings_sats += other.estimated_earnings_sats;
    }
}

/// Stratum job from pool
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StratumJob {
    pub job_id: String,
    pub prev_hash: String,
    pub coinbase1: String,
    pub coinbase2: String,
    pub merkle_branches: Vec<String>,
    pub version: String,
    pub nbits: String,
    pub ntime: String,
    pub clean_jobs: bool,
}

/// Share submission result
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ShareResult {
    Accepted,
    Rejected(String),
    Stale,
}

/// Mining coordinator for distributing work across APN nodes
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MiningCoordinator {
    /// Pool configuration
    pub config: MiningPoolConfig,
    /// Connected miners (node_id -> hashrate)
    pub miners: std::collections::HashMap<String, u64>,
    /// Current difficulty target
    pub network_difficulty: f64,
    /// Bitcoin price (for earnings estimation)
    pub btc_price_usd: f64,
    /// Vibe per satoshi earned
    pub vibe_per_sat: f64,
}

impl MiningCoordinator {
    pub fn new(config: MiningPoolConfig) -> Self {
        Self {
            config,
            miners: std::collections::HashMap::new(),
            network_difficulty: 0.0,
            btc_price_usd: 0.0,
            vibe_per_sat: 10.0, // 10 VIBE per satoshi earned
        }
    }

    /// Total network hashrate from all APN miners
    pub fn total_hashrate(&self) -> u64 {
        self.miners.values().sum()
    }

    /// Register a miner
    pub fn register_miner(&mut self, node_id: String, hashrate: u64) {
        self.miners.insert(node_id, hashrate);
    }

    /// Remove a miner
    pub fn unregister_miner(&mut self, node_id: &str) {
        self.miners.remove(node_id);
    }

    /// Calculate Vibe reward for mining contribution
    pub fn calculate_vibe_reward(&self, sats_earned: u64) -> u64 {
        (sats_earned as f64 * self.vibe_per_sat) as u64
    }
}

/// CPU miner (for testing/small contributions)
pub struct CpuMiner {
    config: MiningPoolConfig,
    stats: MiningStats,
    running: bool,
}

impl CpuMiner {
    pub fn new(config: MiningPoolConfig) -> Self {
        Self {
            config,
            stats: MiningStats::default(),
            running: false,
        }
    }

    /// Start mining (connects to pool)
    pub async fn start(&mut self) -> anyhow::Result<()> {
        tracing::info!(
            "Starting CPU miner, connecting to {}",
            self.config.stratum_url
        );
        self.running = true;

        // TODO: Implement actual stratum connection
        // For now, this is a stub

        Ok(())
    }

    /// Stop mining
    pub fn stop(&mut self) {
        tracing::info!("Stopping CPU miner");
        self.running = false;
    }

    /// Get current stats
    pub fn stats(&self) -> &MiningStats {
        &self.stats
    }

    /// Check if miner is running
    pub fn is_running(&self) -> bool {
        self.running
    }
}

/// Mining work unit for distribution
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkUnit {
    /// Unique work ID
    pub work_id: String,
    /// Block header data
    pub header: Vec<u8>,
    /// Target difficulty
    pub target: [u8; 32],
    /// Nonce range start
    pub nonce_start: u32,
    /// Nonce range end
    pub nonce_end: u32,
    /// Expiration time
    pub expires_at: i64,
}

/// Mining result from worker
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkResult {
    /// Work ID this result is for
    pub work_id: String,
    /// Found nonce (if any)
    pub nonce: Option<u32>,
    /// Hashes computed
    pub hashes_computed: u64,
    /// Time taken (ms)
    pub time_ms: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mining_stats() {
        let mut stats = MiningStats::default();
        stats.shares_submitted = 100;
        stats.shares_accepted = 95;
        stats.shares_rejected = 5;

        assert_eq!(stats.acceptance_rate(), 95.0);
    }

    #[test]
    fn test_coordinator() {
        let config = MiningPoolConfig::default();
        let mut coordinator = MiningCoordinator::new(config);

        coordinator.register_miner("node1".to_string(), 1000);
        coordinator.register_miner("node2".to_string(), 2000);

        assert_eq!(coordinator.total_hashrate(), 3000);

        let reward = coordinator.calculate_vibe_reward(100);
        assert_eq!(reward, 1000); // 100 sats * 10 VIBE/sat
    }
}
