//! APN Reward Tracker Service
//!
//! Listens to heartbeats on NATS and creates reward records in the database.
//! This service should run on the master node only.

use std::sync::Arc;

use alpha_protocol_core::{
    economics::RewardRates,
    reward_tracker::{RewardTracker, RewardTrackerConfig},
};
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
    println!("║           APN REWARD TRACKER SERVICE                             ║");
    println!("╠══════════════════════════════════════════════════════════════════╣");
    println!("║  Tracks peer heartbeats and calculates VIBE rewards              ║");
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

    // Setup reward rates
    let rates = RewardRates::default();
    println!("\n💰 Reward Configuration:");
    println!(
        "   Base: {} VIBE per heartbeat",
        alpha_protocol_core::economics::vibe_to_display(rates.heartbeat_base)
    );
    println!("   GPU Multiplier: {}x", rates.gpu_multiplier);
    println!("   High CPU Multiplier: {}x", rates.high_cpu_multiplier);
    println!("   High RAM Multiplier: {}x", rates.high_ram_multiplier);

    // Configure tracker
    let config = RewardTrackerConfig {
        nats_url: std::env::var("NATS_URL")
            .unwrap_or_else(|_| "nats://nonlocal.info:4222".to_string()),
        reward_interval_secs: 60, // Calculate rewards every 60 seconds
        db_path: db_path.clone(),
    };

    println!("\n📡 NATS Configuration:");
    println!("   URL: {}", config.nats_url);
    println!("   Reward Interval: {}s", config.reward_interval_secs);

    // Create tracker
    let mut tracker = RewardTracker::new_with_config(db_pool.clone(), config, rates);

    // Initialize tracker (connect to NATS)
    println!("\n🚀 Initializing reward tracker...");
    tracker.init().await?;
    println!("✅ Reward tracker initialized");

    let tracker = Arc::new(tracker);

    // Start listening
    println!("👂 Listening for heartbeats on apn.heartbeat...\n");
    tracker.start().await?;

    // Keep the service running forever
    tokio::signal::ctrl_c().await?;
    println!("\n👋 Shutting down reward tracker...");

    Ok(())
}
