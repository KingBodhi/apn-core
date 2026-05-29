//! APN Reward Distributor Service
//!
//! Batches pending rewards and distributes VIBE tokens to peer wallets.
//! This service should run on the master node only.

use std::sync::Arc;

use alpha_protocol_core::reward_distributor::{DistributorConfig, RewardDistributor};
use sqlx::sqlite::SqlitePoolOptions;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Load environment
    dotenv::dotenv().ok();

    // Initialize logging
    tracing_subscriber::fmt()
        .with_env_filter("info,alpha_protocol_core=debug")
        .init();

    println!("\n╔══════════════════════════════════════════════════════════════════╗");
    println!("║           APN REWARD DISTRIBUTOR SERVICE                         ║");
    println!("╠══════════════════════════════════════════════════════════════════╣");
    println!("║  Batches and distributes VIBE rewards to peer wallets            ║");
    println!("╚══════════════════════════════════════════════════════════════════╝\n");

    // Connect to database
    let db_path =
        std::env::var("DATABASE_URL").unwrap_or_else(|_| "sqlite:dev_assets/db.sqlite".to_string());

    println!("📊 Connecting to database: {}", db_path);
    let db_pool = SqlitePoolOptions::new()
        .max_connections(5)
        .connect(&db_path)
        .await?;

    println!("✅ Database connected");

    // Load rewards wallet from environment
    let rewards_wallet_seed =
        std::env::var("REWARDS_WALLET_SEED").expect("REWARDS_WALLET_SEED must be set in .env");

    let rewards_wallet_address = std::env::var("REWARDS_WALLET_ADDRESS")
        .expect("REWARDS_WALLET_ADDRESS must be set in .env");

    println!("💎 Rewards Wallet: {}", rewards_wallet_address);

    // Configure distributor
    let config = DistributorConfig {
        rewards_wallet_mnemonic: rewards_wallet_seed,
        min_distribution_amount: 100_000_000, // 1 VIBE minimum
        batch_size: 50,
        distribution_interval_secs: 300, // 5 minutes
        aptos_node_url: std::env::var("APTOS_NODE_URL")
            .unwrap_or_else(|_| "https://fullnode.testnet.aptoslabs.com/v1".to_string()),
    };

    println!("\n⚙️  Configuration:");
    println!(
        "   Min Distribution: {} VIBE",
        alpha_protocol_core::economics::vibe_to_display(config.min_distribution_amount as u64)
    );
    println!("   Batch Size: {}", config.batch_size);
    println!("   Interval: {}s", config.distribution_interval_secs);
    println!("   Aptos Node: {}", config.aptos_node_url);

    // Create distributor
    let mut distributor = RewardDistributor::new(db_pool.clone(), config);

    // Initialize distributor
    println!("\n🔐 Loading rewards wallet...");
    distributor.init().await?;
    println!("✅ Distributor initialized");

    // Get stats
    let stats = distributor.get_stats().await?;
    println!("\n📈 Current Stats:");
    println!(
        "   Pending: {} VIBE",
        alpha_protocol_core::economics::vibe_to_display(stats.total_pending_vibe as u64)
    );
    println!(
        "   Distributed: {} VIBE",
        alpha_protocol_core::economics::vibe_to_display(stats.total_distributed_vibe as u64)
    );
    println!("   Total Batches: {}", stats.total_batches);

    // Start distributor
    println!("\n🚀 Starting distributor service...");
    println!(
        "💸 Will distribute rewards every {} seconds\n",
        distributor.get_stats().await?.total_batches
    );

    Arc::new(distributor).start().await?;

    Ok(())
}
