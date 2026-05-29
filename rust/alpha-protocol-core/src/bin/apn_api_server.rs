//! APN API Server - Lightweight HTTP server for network monitoring
//!
//! Provides a simple HTTP API to view Alpha Protocol Network nodes
//! Can be accessed from any device on the network

use std::{
    collections::HashMap,
    fs::File,
    io::{BufRead, BufReader},
    sync::Arc,
};

use axum::{
    extract::State,
    response::{Html, Json},
    routing::get,
    Router,
};
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;
use tower_http::cors::{Any, CorsLayer};

#[derive(Debug, Clone, Serialize, Deserialize)]
struct PeerInfo {
    node_id: String,
    wallet_address: String,
    capabilities: Vec<String>,
    resources: Option<alpha_protocol_core::wire::NodeResources>,
    #[serde(skip_serializing_if = "Option::is_none")]
    device_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct NetworkStatus {
    master_node_id: String,
    total_nodes: usize,
    peers: Vec<PeerInfo>,
    relay_connected: bool,
    last_updated: String,
}

struct AppState {
    network_status: Arc<RwLock<NetworkStatus>>,
}

/// Fetch peers from master node log
fn fetch_network_info() -> (Vec<PeerInfo>, bool, String) {
    use regex::Regex;

    let log_path = "/tmp/apn_node.log";
    let mut peers = HashMap::new();
    let mut relay_connected = false;
    let mut master_node_id = "unknown".to_string();

    if let Ok(file) = File::open(log_path) {
        let reader = BufReader::new(file);
        let lines: Vec<String> = reader.lines().map_while(Result::ok).collect();

        // Get master node ID
        if let Some(line) = lines.iter().find(|l| l.contains("Node ID:")) {
            if let Some(id_part) = line.split("Node ID:").nth(1) {
                master_node_id = id_part
                    .trim()
                    .split_whitespace()
                    .next()
                    .unwrap_or("unknown")
                    .to_string();
            }
        }

        // Regex to match peer announcements with resources
        let peer_regex = Regex::new(
            r#"Message from apn\.discovery \(([^)]+)\): PeerAnnouncement \{ wallet_address: "([^"]+)", capabilities: \[([^\]]+)\], resources: Some\(NodeResources \{ cpu_cores: (\d+), ram_mb: (\d+), storage_gb: (\d+), gpu_available: (true|false), gpu_model: (Some\("([^"]+)"\)|None)"#
        ).unwrap();

        // Check for relay connection
        let relay_regex = Regex::new(r"Relay connected|🌐 Relay connected").unwrap();

        for line in lines.iter().rev().take(1000) {
            if relay_regex.is_match(line) {
                relay_connected = true;
            }

            if let Some(caps) = peer_regex.captures(line) {
                let node_id = caps.get(1).map(|m| m.as_str()).unwrap_or("").to_string();
                let wallet = caps.get(2).map(|m| m.as_str()).unwrap_or("").to_string();
                let caps_str = caps.get(3).map(|m| m.as_str()).unwrap_or("");

                let cpu_cores: u32 = caps
                    .get(4)
                    .and_then(|m| m.as_str().parse().ok())
                    .unwrap_or(0);
                let ram_mb: u64 = caps
                    .get(5)
                    .and_then(|m| m.as_str().parse().ok())
                    .unwrap_or(0);
                let storage_gb: u64 = caps
                    .get(6)
                    .and_then(|m| m.as_str().parse().ok())
                    .unwrap_or(0);
                let gpu_available = caps.get(7).map(|m| m.as_str() == "true").unwrap_or(false);
                let gpu_model = if gpu_available {
                    caps.get(9).map(|m| m.as_str().to_string())
                } else {
                    None
                };

                let capabilities: Vec<String> = caps_str
                    .split(',')
                    .map(|s| s.trim().trim_matches('"').to_string())
                    .collect();

                if !peers.contains_key(&node_id) {
                    peers.insert(
                        node_id.clone(),
                        PeerInfo {
                            node_id,
                            wallet_address: wallet,
                            capabilities,
                            resources: Some(alpha_protocol_core::wire::NodeResources {
                                cpu_cores,
                                ram_mb,
                                storage_gb,
                                gpu_available,
                                gpu_model,
                                hashrate: None,
                                bandwidth_mbps: None,
                            }),
                            device_name: None, // Parsed from NATS heartbeats, not logs
                        },
                    );
                }
            }
        }
    }

    (
        peers.into_values().collect(),
        relay_connected,
        master_node_id,
    )
}

/// API endpoint: Get network status
async fn get_network_status(State(state): State<Arc<AppState>>) -> Json<NetworkStatus> {
    let status = state.network_status.read().await;
    Json(status.clone())
}

/// Web UI: Simple HTML dashboard
async fn web_dashboard() -> Html<&'static str> {
    Html(
        r#"
<!DOCTYPE html>
<html>
<head>
    <title>Alpha Protocol Network Monitor</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 2rem;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            background: white;
            border-radius: 1rem;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        h1 { color: #667eea; margin-bottom: 0.5rem; }
        .status { display: flex; align-items: center; gap: 0.5rem; margin-top: 1rem; }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #10b981;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
        }
        .card {
            background: white;
            border-radius: 1rem;
            padding: 1.5rem;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        .card h2 {
            color: #667eea;
            margin-bottom: 1rem;
            font-size: 1.1rem;
        }
        .peer-id {
            font-family: 'Courier New', monospace;
            font-size: 0.9rem;
            color: #666;
            margin-bottom: 0.5rem;
        }
        .resource-list {
            list-style: none;
            margin-top: 1rem;
        }
        .resource-list li {
            padding: 0.5rem 0;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
        }
        .resource-list li:last-child { border-bottom: none; }
        .label { color: #666; }
        .value { font-weight: 600; color: #333; }
        .capabilities {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 1rem;
        }
        .badge {
            background: #667eea;
            color: white;
            padding: 0.25rem 0.75rem;
            border-radius: 1rem;
            font-size: 0.75rem;
        }
        .loading {
            text-align: center;
            color: white;
            font-size: 1.5rem;
            margin-top: 3rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⬡ Alpha Protocol Network</h1>
            <div class="status">
                <div class="status-dot"></div>
                <span>Live Network Monitor</span>
            </div>
            <p style="margin-top: 1rem; color: #666;">
                <strong>Master:</strong> <span id="master-id">Loading...</span> |
                <strong>Nodes:</strong> <span id="node-count">-</span> |
                <strong>Last Updated:</strong> <span id="last-updated">-</span>
            </p>
        </div>

        <div id="peers-grid" class="grid">
            <div class="loading">Loading network data...</div>
        </div>
    </div>

    <script>
        async function fetchNetworkStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();

                document.getElementById('master-id').textContent = data.master_node_id;
                document.getElementById('node-count').textContent = data.total_nodes;
                document.getElementById('last-updated').textContent = new Date(data.last_updated).toLocaleTimeString();

                const grid = document.getElementById('peers-grid');
                if (data.peers.length === 0) {
                    grid.innerHTML = '<div class="card"><p>No peers connected yet</p></div>';
                } else {
                    grid.innerHTML = data.peers.map(peer => `
                        <div class="card">
                            <h2>${peer.node_id}</h2>
                            <div class="peer-id">${peer.wallet_address.slice(0, 20)}...</div>
                            ${peer.resources ? `
                                <ul class="resource-list">
                                    <li><span class="label">CPU</span> <span class="value">${peer.resources.cpu_cores} cores</span></li>
                                    <li><span class="label">RAM</span> <span class="value">${(peer.resources.ram_mb / 1024).toFixed(1)} GB</span></li>
                                    <li><span class="label">Storage</span> <span class="value">${peer.resources.storage_gb} GB</span></li>
                                    ${peer.resources.gpu_available ? `<li><span class="label">GPU</span> <span class="value">${peer.resources.gpu_model || 'Yes'}</span></li>` : ''}
                                </ul>
                            ` : '<p style="color: #999;">No resource data</p>'}
                            <div class="capabilities">
                                ${peer.capabilities.map(cap => `<span class="badge">${cap}</span>`).join('')}
                            </div>
                        </div>
                    `).join('');
                }
            } catch (error) {
                console.error('Failed to fetch network status:', error);
            }
        }

        // Initial fetch
        fetchNetworkStatus();

        // Refresh every 5 seconds
        setInterval(fetchNetworkStatus, 5000);
    </script>
</body>
</html>
    "#,
    )
}

/// Background task to update network status
async fn update_network_status(state: Arc<AppState>) {
    loop {
        let (peers, relay_connected, master_node_id) =
            tokio::task::spawn_blocking(fetch_network_info)
                .await
                .unwrap();

        let mut status = state.network_status.write().await;
        status.master_node_id = master_node_id;
        status.total_nodes = peers.len() + 1; // +1 for master
        status.peers = peers;
        status.relay_connected = relay_connected;
        status.last_updated = chrono::Utc::now().to_rfc3339();

        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt().with_env_filter("info").init();

    let initial_status = NetworkStatus {
        master_node_id: "loading".to_string(),
        total_nodes: 0,
        peers: vec![],
        relay_connected: false,
        last_updated: chrono::Utc::now().to_rfc3339(),
    };

    let state = Arc::new(AppState {
        network_status: Arc::new(RwLock::new(initial_status)),
    });

    // Spawn background task
    let state_clone = Arc::clone(&state);
    tokio::spawn(update_network_status(state_clone));

    // Build router
    let app = Router::new()
        .route("/", get(web_dashboard))
        .route("/api/status", get(get_network_status))
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .with_state(Arc::clone(&state));

    let addr = "0.0.0.0:8081";
    println!("╔══════════════════════════════════════════════════════════╗");
    println!("║   Alpha Protocol Network - API Server                   ║");
    println!("╠══════════════════════════════════════════════════════════╣");
    println!("║   HTTP API:      http://0.0.0.0:8081/api/status        ║");
    println!("║   Web Dashboard: http://0.0.0.0:8081/                   ║");
    println!("║                                                          ║");
    println!("║   Access from any device on your network:              ║");
    println!("║   http://192.168.1.77:8081/                            ║");
    println!("╚══════════════════════════════════════════════════════════╝");

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
