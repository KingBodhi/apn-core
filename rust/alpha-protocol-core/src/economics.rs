//! Vibe Economics - Token rewards and resource accounting
//!
//! Implements the economic layer for the Alpha Protocol Network:
//! - Resource contribution tracking
//! - Proof-of-contribution mechanism
//! - Vibe token rewards
//! - Staking and reputation

use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};

/// Vibe token amount (smallest unit = 1e-8 VIBE)
pub type VibeAmount = u64;

/// Convert Vibe to display format
pub fn vibe_to_display(amount: VibeAmount) -> f64 {
    amount as f64 / 100_000_000.0
}

/// Convert display format to Vibe
pub fn display_to_vibe(amount: f64) -> VibeAmount {
    (amount * 100_000_000.0) as VibeAmount
}

/// Resource contribution snapshot
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ResourceContribution {
    /// CPU cycles contributed (in compute units)
    pub cpu_units: u64,
    /// GPU cycles contributed (in compute units)
    pub gpu_units: u64,
    /// Bandwidth contributed (in bytes)
    pub bandwidth_bytes: u64,
    /// Storage provided (in bytes)
    pub storage_bytes: u64,
    /// Relay messages forwarded
    pub relay_messages: u64,
    /// Uptime duration (in seconds)
    pub uptime_seconds: u64,
    /// Tasks completed
    pub tasks_completed: u64,
    /// Tasks failed
    pub tasks_failed: u64,
}

impl ResourceContribution {
    /// Calculate total contribution score
    pub fn contribution_score(&self) -> u64 {
        // Weighted scoring:
        // - CPU: 1 point per 1M compute units
        // - GPU: 5 points per 1M compute units (GPU is more valuable)
        // - Bandwidth: 1 point per 1GB
        // - Storage: 0.1 points per 1GB (persistent)
        // - Relay: 0.01 points per message
        // - Uptime: 10 points per hour
        // - Tasks: 100 points per completed task

        let cpu_score = self.cpu_units / 1_000_000;
        let gpu_score = (self.gpu_units / 1_000_000) * 5;
        let bandwidth_score = self.bandwidth_bytes / (1024 * 1024 * 1024);
        let storage_score = self.storage_bytes / (10 * 1024 * 1024 * 1024);
        let relay_score = self.relay_messages / 100;
        let uptime_score = (self.uptime_seconds / 3600) * 10;
        let task_score = self.tasks_completed * 100;

        cpu_score
            + gpu_score
            + bandwidth_score
            + storage_score
            + relay_score
            + uptime_score
            + task_score
    }

    /// Merge another contribution into this one
    pub fn merge(&mut self, other: &ResourceContribution) {
        self.cpu_units += other.cpu_units;
        self.gpu_units += other.gpu_units;
        self.bandwidth_bytes += other.bandwidth_bytes;
        self.storage_bytes = other.storage_bytes; // Storage is current, not cumulative
        self.relay_messages += other.relay_messages;
        self.uptime_seconds += other.uptime_seconds;
        self.tasks_completed += other.tasks_completed;
        self.tasks_failed += other.tasks_failed;
    }
}

/// Reward rates for different contribution types
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RewardRates {
    /// Vibe per million CPU units
    pub cpu_rate: VibeAmount,
    /// Vibe per million GPU units
    pub gpu_rate: VibeAmount,
    /// Vibe per GB bandwidth
    pub bandwidth_rate: VibeAmount,
    /// Vibe per GB storage per hour
    pub storage_rate: VibeAmount,
    /// Vibe per relay message
    pub relay_rate: VibeAmount,
    /// Vibe per hour uptime
    pub uptime_rate: VibeAmount,
    /// Vibe per completed task (base rate)
    pub task_rate: VibeAmount,
    /// Vibe per heartbeat (every 30s)
    pub heartbeat_base: VibeAmount,
    /// GPU multiplier for heartbeat rewards
    pub gpu_multiplier: f64,
    /// High CPU multiplier (>16 cores)
    pub high_cpu_multiplier: f64,
    /// High RAM multiplier (>32GB)
    pub high_ram_multiplier: f64,
}

impl Default for RewardRates {
    fn default() -> Self {
        Self {
            cpu_rate: display_to_vibe(0.001),      // 0.001 VIBE per 1M CPU units
            gpu_rate: display_to_vibe(0.005),      // 0.005 VIBE per 1M GPU units
            bandwidth_rate: display_to_vibe(0.01), // 0.01 VIBE per GB
            storage_rate: display_to_vibe(0.001),  // 0.001 VIBE per GB/hour
            relay_rate: display_to_vibe(0.0001),   // 0.0001 VIBE per relay
            uptime_rate: display_to_vibe(0.1),     // 0.1 VIBE per hour
            task_rate: display_to_vibe(1.0),       // 1.0 VIBE per task (base)
            heartbeat_base: display_to_vibe(0.1),  // 0.1 VIBE per heartbeat
            gpu_multiplier: 2.0,                   // 2x for GPU nodes
            high_cpu_multiplier: 1.5,              // 1.5x for >16 cores
            high_ram_multiplier: 1.3,              // 1.3x for >32GB RAM
        }
    }
}

/// Calculate rewards for a contribution period
pub fn calculate_rewards(contribution: &ResourceContribution, rates: &RewardRates) -> VibeAmount {
    let cpu_reward = (contribution.cpu_units / 1_000_000) * rates.cpu_rate;
    let gpu_reward = (contribution.gpu_units / 1_000_000) * rates.gpu_rate;
    let bandwidth_reward =
        (contribution.bandwidth_bytes / (1024 * 1024 * 1024)) * rates.bandwidth_rate;
    let storage_hours =
        (contribution.storage_bytes / (1024 * 1024 * 1024)) * (contribution.uptime_seconds / 3600);
    let storage_reward = storage_hours * rates.storage_rate;
    let relay_reward = contribution.relay_messages * rates.relay_rate;
    let uptime_reward = (contribution.uptime_seconds / 3600) * rates.uptime_rate;
    let task_reward = contribution.tasks_completed * rates.task_rate;

    cpu_reward
        + gpu_reward
        + bandwidth_reward
        + storage_reward
        + relay_reward
        + uptime_reward
        + task_reward
}

/// Node reputation score (affects task assignment priority)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeReputation {
    /// Total contribution score (lifetime)
    pub total_contribution: u64,
    /// Success rate (0-100)
    pub success_rate: u8,
    /// Average response time (ms)
    pub avg_response_time_ms: u32,
    /// Time since first contribution (hours)
    pub age_hours: u64,
    /// Stake amount (affects priority)
    pub stake_amount: VibeAmount,
    /// Slash count (negative events)
    pub slash_count: u32,
}

impl Default for NodeReputation {
    fn default() -> Self {
        Self {
            total_contribution: 0,
            success_rate: 100,
            avg_response_time_ms: 0,
            age_hours: 0,
            stake_amount: 0,
            slash_count: 0,
        }
    }
}

impl NodeReputation {
    /// Calculate reputation score (0-1000)
    pub fn reputation_score(&self) -> u32 {
        // Base score from contribution
        let contribution_score = (self.total_contribution.min(1_000_000) / 1000) as u32;

        // Success rate multiplier (0.5 - 1.5)
        let success_mult = (self.success_rate as u32 + 50) / 100;

        // Age bonus (max 100 points for 1000+ hours)
        let age_bonus = (self.age_hours.min(1000) / 10) as u32;

        // Stake bonus (max 200 points)
        let stake_bonus = (vibe_to_display(self.stake_amount).min(1000.0) / 5.0) as u32;

        // Slash penalty (25 points per slash)
        let slash_penalty = self.slash_count * 25;

        let score = (contribution_score * success_mult + age_bonus + stake_bonus)
            .saturating_sub(slash_penalty);

        score.min(1000)
    }
}

/// Proof of contribution record
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContributionProof {
    /// Node that made the contribution
    pub node_id: String,
    /// Contribution period start (unix timestamp)
    pub period_start: i64,
    /// Contribution period end (unix timestamp)
    pub period_end: i64,
    /// Resource contribution during period
    pub contribution: ResourceContribution,
    /// Calculated rewards
    pub rewards: VibeAmount,
    /// Merkle root of contribution data
    pub merkle_root: String,
    /// Signatures from validators
    pub validator_signatures: Vec<ValidatorSignature>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidatorSignature {
    pub validator_id: String,
    pub signature: String,
    pub timestamp: i64,
}

impl ContributionProof {
    /// Check if proof has enough validator signatures
    pub fn is_valid(&self, min_validators: usize) -> bool {
        self.validator_signatures.len() >= min_validators
    }
}

/// Staking pool for a node
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StakePool {
    /// Node's own stake
    pub self_stake: VibeAmount,
    /// Delegated stakes (delegator -> amount)
    pub delegations: HashMap<String, VibeAmount>,
    /// Pending unstake requests
    pub pending_unstake: Vec<UnstakeRequest>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnstakeRequest {
    pub delegator: String,
    pub amount: VibeAmount,
    pub request_time: i64,
    /// Unstake available after this time (cooldown period)
    pub available_time: i64,
}

impl StakePool {
    /// Total stake in the pool
    pub fn total_stake(&self) -> VibeAmount {
        self.self_stake + self.delegations.values().sum::<VibeAmount>()
    }

    /// Add stake
    pub fn add_stake(&mut self, delegator: Option<String>, amount: VibeAmount) {
        match delegator {
            Some(d) => {
                *self.delegations.entry(d).or_insert(0) += amount;
            }
            None => {
                self.self_stake += amount;
            }
        }
    }

    /// Request unstake (starts cooldown)
    pub fn request_unstake(
        &mut self,
        delegator: Option<String>,
        amount: VibeAmount,
        cooldown_hours: u64,
    ) {
        let now = chrono::Utc::now().timestamp();
        let available = now + (cooldown_hours * 3600) as i64;

        self.pending_unstake.push(UnstakeRequest {
            delegator: delegator.unwrap_or_else(|| "self".to_string()),
            amount,
            request_time: now,
            available_time: available,
        });
    }

    /// Process completed unstake requests
    pub fn process_unstakes(&mut self) -> Vec<(String, VibeAmount)> {
        let now = chrono::Utc::now().timestamp();
        let mut completed = Vec::new();

        self.pending_unstake.retain(|req| {
            if req.available_time <= now {
                if req.delegator == "self" {
                    self.self_stake = self.self_stake.saturating_sub(req.amount);
                } else if let Some(stake) = self.delegations.get_mut(&req.delegator) {
                    *stake = stake.saturating_sub(req.amount);
                }
                completed.push((req.delegator.clone(), req.amount));
                false
            } else {
                true
            }
        });

        // Remove empty delegations
        self.delegations.retain(|_, v| *v > 0);

        completed
    }
}

/// Resource tracker for real-time monitoring
pub struct ResourceTracker {
    start_time: Instant,
    last_snapshot: Instant,
    current: ResourceContribution,
    total: ResourceContribution,
}

impl ResourceTracker {
    pub fn new() -> Self {
        Self {
            start_time: Instant::now(),
            last_snapshot: Instant::now(),
            current: ResourceContribution::default(),
            total: ResourceContribution::default(),
        }
    }

    /// Record CPU usage
    pub fn record_cpu(&mut self, units: u64) {
        self.current.cpu_units += units;
    }

    /// Record GPU usage
    pub fn record_gpu(&mut self, units: u64) {
        self.current.gpu_units += units;
    }

    /// Record bandwidth usage
    pub fn record_bandwidth(&mut self, bytes: u64) {
        self.current.bandwidth_bytes += bytes;
    }

    /// Update storage amount
    pub fn set_storage(&mut self, bytes: u64) {
        self.current.storage_bytes = bytes;
    }

    /// Record relay message
    pub fn record_relay(&mut self) {
        self.current.relay_messages += 1;
    }

    /// Record task completion
    pub fn record_task(&mut self, success: bool) {
        if success {
            self.current.tasks_completed += 1;
        } else {
            self.current.tasks_failed += 1;
        }
    }

    /// Take a snapshot and reset current period
    pub fn snapshot(&mut self) -> ResourceContribution {
        let elapsed = self.last_snapshot.elapsed().as_secs();
        self.current.uptime_seconds = elapsed;

        let snapshot = self.current.clone();
        self.total.merge(&snapshot);

        self.current = ResourceContribution::default();
        self.last_snapshot = Instant::now();

        snapshot
    }

    /// Get current contribution (without snapshot)
    pub fn current(&self) -> ResourceContribution {
        let mut contrib = self.current.clone();
        contrib.uptime_seconds = self.last_snapshot.elapsed().as_secs();
        contrib
    }

    /// Get total contribution
    pub fn total(&self) -> &ResourceContribution {
        &self.total
    }

    /// Get total uptime
    pub fn uptime(&self) -> Duration {
        self.start_time.elapsed()
    }
}

impl Default for ResourceTracker {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vibe_conversion() {
        assert_eq!(display_to_vibe(1.0), 100_000_000);
        assert_eq!(display_to_vibe(0.5), 50_000_000);
        assert_eq!(vibe_to_display(100_000_000), 1.0);
    }

    #[test]
    fn test_contribution_score() {
        let contrib = ResourceContribution {
            cpu_units: 10_000_000,
            gpu_units: 2_000_000,
            bandwidth_bytes: 5 * 1024 * 1024 * 1024,
            storage_bytes: 100 * 1024 * 1024 * 1024,
            relay_messages: 1000,
            uptime_seconds: 3600 * 24,
            tasks_completed: 10,
            tasks_failed: 1,
        };

        let score = contrib.contribution_score();
        assert!(score > 0);
    }

    #[test]
    fn test_reputation_score() {
        let rep = NodeReputation {
            total_contribution: 100_000,
            success_rate: 95,
            avg_response_time_ms: 100,
            age_hours: 500,
            stake_amount: display_to_vibe(100.0),
            slash_count: 0,
        };

        let score = rep.reputation_score();
        assert!(score > 0);
        assert!(score <= 1000);
    }

    #[test]
    fn test_stake_pool() {
        let mut pool = StakePool::default();

        pool.add_stake(None, display_to_vibe(100.0));
        pool.add_stake(Some("delegator1".to_string()), display_to_vibe(50.0));

        assert_eq!(vibe_to_display(pool.total_stake()), 150.0);
    }
}
