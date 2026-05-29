use alpha_protocol_core::{mesh::MeshMessage, wire::NodeResources};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Connect to NATS relay
    println!("Connecting to NATS relay...");
    let client = async_nats::connect("nats://nonlocal.info:4222").await?;
    println!("✅ Connected to NATS relay");

    // Create a proper MeshMessage::PeerAnnouncement
    let announcement = MeshMessage::PeerAnnouncement {
        wallet_address: "0x09465b9572fb354fdf4e34040386f180d1ff0c2a3a668333bedee17b266a4b74"
            .to_string(),
        capabilities: vec![
            "compute".to_string(),
            "relay".to_string(),
            "storage".to_string(),
        ],
        resources: Some(NodeResources::default()),
    };

    // Broadcast on discovery channel
    println!("📤 Broadcasting PeerAnnouncement on apn.discovery");
    let payload = serde_json::to_vec(&announcement)?;
    client
        .publish("apn.discovery".to_string(), payload.into())
        .await?;
    println!("✅ Discovery announcement broadcasted!");

    // Send direct message to Omega 1
    let omega_dm_subject = "apn.dm.apn_9c47c2fb";
    println!(
        "📤 Sending direct message to Omega 1 at {}",
        omega_dm_subject
    );
    client
        .publish(
            omega_dm_subject.to_string(),
            serde_json::to_vec(&announcement)?.into(),
        )
        .await?;
    println!("✅ Direct message sent to Omega 1!");

    // Flush to ensure messages are sent
    client.flush().await?;
    println!("\n🎉 All messages sent successfully!");
    println!("\nSent MeshMessage::PeerAnnouncement with:");
    println!("  Wallet: 0x09465b95...4b74");
    println!("  Capabilities: compute, relay, storage");

    Ok(())
}
