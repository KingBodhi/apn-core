//! Identity file storage and persistence
//!
//! Handles saving and loading node identities to prevent wallet regeneration
//! on restart. Similar to the Python APN Core implementation.

use std::{
    fs,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::identity::NodeIdentity;

const IDENTITY_FILE: &str = "node_identity.json";
const BACKUP_SUFFIX: &str = ".backup";

/// Stored identity data
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredIdentity {
    pub node_id: String,
    pub wallet_address: String,
    pub mnemonic: String,
    pub public_key: String,
}

impl From<&NodeIdentity> for StoredIdentity {
    fn from(identity: &NodeIdentity) -> Self {
        Self {
            node_id: identity.short_id(),
            wallet_address: identity.address().to_string(),
            mnemonic: identity.mnemonic_phrase(),
            public_key: identity.public_key_hex(),
        }
    }
}

/// Get default config directory (~/.apn)
pub fn get_config_dir() -> Result<PathBuf> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .context("Could not determine home directory")?;

    let config_dir = Path::new(&home).join(".apn");

    // Ensure directory exists with secure permissions
    if !config_dir.exists() {
        fs::create_dir_all(&config_dir)?;

        // Set permissions to 0o700 (owner only) on Unix
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&config_dir, fs::Permissions::from_mode(0o700))?;
        }
    }

    Ok(config_dir)
}

/// Get path to identity file
fn get_identity_file_path() -> Result<PathBuf> {
    Ok(get_config_dir()?.join(IDENTITY_FILE))
}

/// Load existing identity from file
pub fn load_identity() -> Result<Option<NodeIdentity>> {
    let identity_file = get_identity_file_path()?;

    if !identity_file.exists() {
        tracing::info!(
            "No existing identity file found at {}",
            identity_file.display()
        );
        return Ok(None);
    }

    tracing::info!("Loading existing identity from {}", identity_file.display());

    // Read and parse identity file
    let contents = fs::read_to_string(&identity_file)
        .context("Failed to read identity file. If corrupted, backup and delete it to generate new identity.")?;

    let stored: StoredIdentity = serde_json::from_str(&contents)
        .context("Identity file is corrupted (invalid JSON). Backup the file and delete it to generate new identity. WARNING: This will create a new wallet!")?;

    // Validate required fields
    if stored.node_id.is_empty() || stored.wallet_address.is_empty() || stored.mnemonic.is_empty() {
        anyhow::bail!(
            "Identity file is missing required fields. Backup and delete to generate new identity."
        );
    }

    // Recreate identity from mnemonic
    let identity = NodeIdentity::from_mnemonic_phrase(&stored.mnemonic)
        .context("Failed to recreate identity from stored mnemonic")?;

    // Verify it matches stored data
    if identity.short_id() != stored.node_id {
        anyhow::bail!(
            "Identity verification failed: node_id mismatch (file: {}, derived: {})",
            stored.node_id,
            identity.short_id()
        );
    }

    tracing::info!("✓ Loaded existing node identity: {}", identity.short_id());
    tracing::info!("✓ Wallet address: {}", identity.address());
    tracing::info!("✓ Identity file: {}", identity_file.display());

    Ok(Some(identity))
}

/// Save identity to file (with backup)
pub fn save_identity(identity: &NodeIdentity) -> Result<()> {
    let identity_file = get_identity_file_path()?;
    let backup_file = identity_file.with_extension(format!("json{}", BACKUP_SUFFIX));

    // Create backup if file exists
    if identity_file.exists() {
        fs::copy(&identity_file, &backup_file)
            .context("Failed to create backup of identity file")?;
        tracing::info!("Created backup: {}", backup_file.display());
    }

    // Prepare identity data
    let stored = StoredIdentity::from(identity);
    let json = serde_json::to_string_pretty(&stored).context("Failed to serialize identity")?;

    // Write identity file
    fs::write(&identity_file, &json).context("Failed to write identity file")?;

    // Set secure permissions (owner only) on Unix
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&identity_file, fs::Permissions::from_mode(0o600))?;
    }

    // Verify the saved file can be read back
    let verification =
        fs::read_to_string(&identity_file).context("Failed to verify saved identity file")?;
    let verified: StoredIdentity = serde_json::from_str(&verification)
        .context("Identity verification failed - saved file is not valid JSON")?;

    if verified.node_id != identity.short_id() {
        anyhow::bail!(
            "Identity verification failed: node_id mismatch after save (expected: {}, got: {})",
            identity.short_id(),
            verified.node_id
        );
    }

    tracing::info!("✓ Saved identity to {}", identity_file.display());

    Ok(())
}

/// Load or create identity (with persistence)
pub fn load_or_create_identity(import_mnemonic: Option<&str>) -> Result<NodeIdentity> {
    // If importing from mnemonic, use it directly
    if let Some(mnemonic) = import_mnemonic {
        tracing::info!("Importing identity from provided mnemonic");
        let identity = NodeIdentity::from_mnemonic_phrase(mnemonic)?;
        save_identity(&identity)?;
        return Ok(identity);
    }

    // Try to load existing identity
    if let Some(identity) = load_identity()? {
        return Ok(identity);
    }

    // Generate new identity
    tracing::info!("Generating new node identity");
    let identity = NodeIdentity::generate()?;

    tracing::warn!("⚠️  Generated NEW identity: {}", identity.short_id());
    tracing::warn!("⚠️  Wallet address: {}", identity.address());
    tracing::warn!("⚠️  This identity will be saved and reused on restart");

    // Save for future use
    save_identity(&identity)?;

    Ok(identity)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_stored_identity_roundtrip() {
        let identity = NodeIdentity::generate().unwrap();
        let stored = StoredIdentity::from(&identity);

        let json = serde_json::to_string(&stored).unwrap();
        let parsed: StoredIdentity = serde_json::from_str(&json).unwrap();

        assert_eq!(stored.node_id, parsed.node_id);
        assert_eq!(stored.wallet_address, parsed.wallet_address);
        assert_eq!(stored.mnemonic, parsed.mnemonic);
    }
}
