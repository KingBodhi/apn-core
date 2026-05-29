//! APN Send - Send a message to another node via NATS relay
//!
//! Usage: apn_send <recipient_node_id> <message>
//! Example: apn_send apn_09465b95 "Hello from Omega 1!"

use std::env;

use async_nats::ConnectOptions;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct DirectMessage {
    from_node: String,
    message_type: String,
    payload: String,
    timestamp: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = env::args().collect();

    if args.len() < 3 {
        eprintln!("Usage: apn_send <recipient_node_id> <message>");
        eprintln!("Example: apn_send apn_09465b95 \"Hello from Omega 1!\"");
        std::process::exit(1);
    }

    let recipient = &args[1];
    let message = args[2..].join(" ");

    println!("📤 Sending message to {}...", recipient);

    // Connect to NATS relay
    let client = async_nats::connect_with_options(
        "nats://nonlocal.info:4222",
        ConnectOptions::new().name("apn_sender"),
    )
    .await?;

    println!("✅ Connected to NATS relay");

    // Create the message
    let dm = DirectMessage {
        from_node: "omega1".to_string(),
        message_type: "direct_message".to_string(),
        payload: message.clone(),
        timestamp: chrono::Utc::now().to_rfc3339(),
    };

    let payload = serde_json::to_vec(&dm)?;

    // Send to the recipient's direct message subject
    let subject = format!("apn.dm.{}", recipient);
    client.publish(subject.clone(), payload.into()).await?;

    println!("✅ Message sent to {}", subject);
    println!("📨 Content: \"{}\"", message);

    // Also publish to discovery so both nodes can see activity
    let discovery_msg = serde_json::json!({
        "event": "direct_message_sent",
        "from": "omega1",
        "to": recipient,
        "timestamp": chrono::Utc::now().to_rfc3339(),
    });

    client
        .publish(
            "apn.discovery".to_string(),
            serde_json::to_vec(&discovery_msg)?.into(),
        )
        .await?;

    client.flush().await?;

    println!("\n✅ Message delivered via NATS relay!");

    Ok(())
}
