//! APN Node - Alpha Protocol Network CLI
//!
//! A simple CLI tool to test mesh networking between devices.
//!
//! Usage:
//!   apn_node [OPTIONS]
//!
//! Options:
//!   --port <PORT>       P2P port (default: 4001)
//!   --relay <URL>       NATS relay URL (default: nats://nonlocal.info:4222)
//!   --bootstrap <ADDR>  Bootstrap peer multiaddr
//!   --new               Generate new identity
//!   --import <PHRASE>   Import from mnemonic phrase

use std::env;

use alpha_protocol_core::{
    identity::NodeIdentity,
    identity_storage,
    node::{AlphaNodeBuilder, NodeEvent},
    DEFAULT_NATS_RELAY,
};
use tokio::sync::mpsc;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter("info,alpha_protocol_core=debug")
        .init();

    let args: Vec<String> = env::args().collect();

    let mut port: u16 = 4001;
    let mut relay_url = DEFAULT_NATS_RELAY.to_string();
    let mut bootstrap_peers: Vec<String> = vec![];
    let mut mnemonic: Option<String> = None;
    let mut heartbeat_interval: u64 = 30; // seconds
    let mut enable_heartbeat = true;
    let mut device_name: Option<String> = None;

    // Parse args
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--port" => {
                port = args
                    .get(i + 1)
                    .ok_or_else(|| anyhow::anyhow!("--port requires a value"))?
                    .parse()?;
                i += 2;
            }
            "--relay" => {
                relay_url = args
                    .get(i + 1)
                    .ok_or_else(|| anyhow::anyhow!("--relay requires a value"))?
                    .clone();
                i += 2;
            }
            "--bootstrap" => {
                let peer = args
                    .get(i + 1)
                    .ok_or_else(|| anyhow::anyhow!("--bootstrap requires a value"))?
                    .clone();
                bootstrap_peers.push(peer);
                i += 2;
            }
            "--import" => {
                mnemonic = Some(
                    args.get(i + 1)
                        .ok_or_else(|| anyhow::anyhow!("--import requires a mnemonic phrase"))?
                        .clone(),
                );
                i += 2;
            }
            "--new" => {
                mnemonic = None;
                i += 1;
            }
            "--heartbeat-interval" => {
                heartbeat_interval = args
                    .get(i + 1)
                    .ok_or_else(|| anyhow::anyhow!("--heartbeat-interval requires a value"))?
                    .parse()?;
                i += 2;
            }
            "--no-heartbeat" => {
                enable_heartbeat = false;
                i += 1;
            }
            "--name" => {
                device_name = Some(
                    args.get(i + 1)
                        .ok_or_else(|| anyhow::anyhow!("--name requires a value"))?
                        .clone(),
                );
                i += 2;
            }
            "--help" | "-h" => {
                print_help();
                return Ok(());
            }
            _ => {
                eprintln!("Unknown option: {}", args[i]);
                print_help();
                return Ok(());
            }
        }
    }

    // Create event channel
    let (event_tx, mut event_rx) = mpsc::unbounded_channel::<NodeEvent>();

    // Load or create identity (with persistence)
    let identity = identity_storage::load_or_create_identity(mnemonic.as_deref())?;

    // Build the node with persistent identity
    let mut builder = AlphaNodeBuilder::new()
        .with_port(port)
        .with_relay(&relay_url)
        .with_bootstrap_peers(bootstrap_peers)
        .with_capabilities(vec![
            "compute".to_string(),
            "relay".to_string(),
            "storage".to_string(),
        ])
        .with_identity(identity);

    if let Some(ref name) = device_name {
        builder = builder.with_device_name(name);
    }

    let mut node = builder.build(event_tx)?;

    println!("\n╔══════════════════════════════════════════════════════════════════╗");
    println!("║              ALPHA PROTOCOL NETWORK - Node                       ║");
    println!("╠══════════════════════════════════════════════════════════════════╣");
    if let Some(ref name) = device_name {
        println!("║  Name:     {:<53}║", name);
    }
    println!("║  Node ID:  {:<53}║", node.short_id());
    println!("║  Address:  {:<53}║", node.address());
    println!("║  P2P Port: {:<53}║", port);
    println!("║  Relay:    {:<53}║", relay_url);
    println!("╚══════════════════════════════════════════════════════════════════╝\n");

    // Print mnemonic for backup
    let identity = NodeIdentity::from_mnemonic_phrase(&node.identity().mnemonic_phrase())?;
    println!("📝 Mnemonic (save this for recovery):");
    println!("   {}\n", identity.mnemonic_phrase());

    // Start the node
    println!("🚀 Starting node...");
    node.start().await?;
    println!("✅ Node started!\n");

    // Announce to the network (may fail if no peers yet, that's OK)
    println!("📢 Announcing to network...");
    match node.announce().await {
        Ok(_) => println!("✅ Announced!\n"),
        Err(e) => println!("⚠️  Announce pending (no peers yet): {}\n", e),
    }

    // Setup heartbeat interval
    let mut heartbeat_timer = if enable_heartbeat {
        println!("💓 Heartbeat enabled (interval: {}s)\n", heartbeat_interval);
        Some(tokio::time::interval(std::time::Duration::from_secs(
            heartbeat_interval,
        )))
    } else {
        None
    };

    println!("👂 Listening for events... (Ctrl+C to quit)\n");

    // Event loop
    loop {
        tokio::select! {
            Some(event) = event_rx.recv() => {
                match event {
                    NodeEvent::Started { peer_id, address } => {
                        println!("🟢 Node started: {} @ {}", peer_id, address);
                    }
                    NodeEvent::PeerConnected(peer) => {
                        println!("🔗 Peer connected: {} ({} capabilities)",
                            peer.peer_id,
                            peer.capabilities.len()
                        );
                        if let Some(addr) = peer.wallet_address {
                            println!("   Wallet: {}", addr);
                        }
                        for cap in &peer.capabilities {
                            println!("   - {}", cap);
                        }
                    }
                    NodeEvent::PeerDisconnected(peer_id) => {
                        println!("🔴 Peer disconnected: {}", peer_id);
                    }
                    NodeEvent::MessageReceived { from, message } => {
                        println!("📨 Message from {}: {:?}", from, message);
                    }
                    NodeEvent::RelayConnected => {
                        println!("🌐 Relay connected");
                    }
                    NodeEvent::RelayDisconnected => {
                        println!("⚠️  Relay disconnected");
                    }
                    NodeEvent::Error(e) => {
                        eprintln!("❌ Error: {}", e);
                    }
                }
            }
            Some(_) = async {
                if let Some(ref mut timer) = heartbeat_timer {
                    Some(timer.tick().await)
                } else {
                    None
                }
            } => {
                // Send heartbeat
                tracing::debug!("Sending heartbeat...");
                if let Err(e) = node.send_heartbeat().await {
                    tracing::error!("Heartbeat failed: {}", e);
                } else {
                    tracing::debug!("Heartbeat sent successfully");
                }
            }
            _ = tokio::signal::ctrl_c() => {
                println!("\n👋 Shutting down...");
                break;
            }
        }
    }

    Ok(())
}

fn print_help() {
    println!("APN Node - Alpha Protocol Network CLI\n");
    println!("Usage: apn_node [OPTIONS]\n");
    println!("Options:");
    println!("  --port <PORT>       P2P port (default: 4001)");
    println!("  --relay <URL>       NATS relay URL (default: nats://nonlocal.info:4222)");
    println!("  --bootstrap <ADDR>  Bootstrap peer multiaddr (can be used multiple times)");
    println!("  --new               Generate new identity (WARNING: Creates new wallet!)");
    println!("  --import <PHRASE>   Import from mnemonic phrase (quoted)");
    println!("  --heartbeat-interval <SECS>  Heartbeat interval in seconds (default: 30)");
    println!("  --name <NAME>       Display name for this node (e.g. \"Sirak Studios\")");
    println!("  --no-heartbeat      Disable heartbeat broadcasts");
    println!("  -h, --help          Show this help\n");
    println!("Identity Persistence:");
    println!("  Identities are automatically saved to ~/.apn/node_identity.json");
    println!("  On restart, the same identity (and wallet) is reused");
    println!("  Backup your identity file to prevent loss of accumulated VIBE!\n");
    println!("Examples:");
    println!("  # Start (loads existing identity or creates new one)");
    println!("  apn_node\n");
    println!("  # Start on custom port");
    println!("  apn_node --port 4002\n");
    println!("  # Import existing identity (overwrites saved identity)");
    println!("  apn_node --import \"word1 word2 word3 ...\"");
}
