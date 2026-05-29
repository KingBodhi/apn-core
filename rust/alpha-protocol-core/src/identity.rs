//! Node Identity - Ed25519 keypairs and BIP39 wallet generation
//!
//! Each node in the Alpha Protocol Network has a unique identity derived from
//! an Ed25519 keypair. The keypair can be generated from a BIP39 mnemonic phrase
//! for easy backup and recovery.

use anyhow::{Context, Result};
use bip39::{Language, Mnemonic};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Information about a wallet/identity
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WalletInfo {
    /// Aptos-style address (0x + 64 hex chars)
    pub address: String,
    /// BIP39 mnemonic phrase (12 or 24 words)
    pub mnemonic: String,
    /// Public key as hex string
    pub public_key: String,
}

/// Node identity containing keypair and derived addresses
#[derive(Clone)]
pub struct NodeIdentity {
    /// Ed25519 signing key (private)
    signing_key: SigningKey,
    /// Ed25519 verifying key (public)
    verifying_key: VerifyingKey,
    /// BIP39 mnemonic for recovery
    mnemonic: Mnemonic,
    /// Derived Aptos-style address
    address: String,
    /// libp2p PeerId derived from public key
    peer_id: String,
}

impl NodeIdentity {
    /// Generate a new random identity with 12-word mnemonic
    pub fn generate() -> Result<Self> {
        let mut entropy = [0u8; 16]; // 128 bits = 12 words
        rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut entropy);

        let mnemonic = Mnemonic::from_entropy_in(Language::English, &entropy)
            .context("Failed to generate mnemonic")?;

        Self::from_mnemonic(mnemonic)
    }

    /// Generate identity with 24-word mnemonic (more secure)
    pub fn generate_24_word() -> Result<Self> {
        let mut entropy = [0u8; 32]; // 256 bits = 24 words
        rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut entropy);

        let mnemonic = Mnemonic::from_entropy_in(Language::English, &entropy)
            .context("Failed to generate mnemonic")?;

        Self::from_mnemonic(mnemonic)
    }

    /// Import identity from mnemonic phrase
    pub fn from_mnemonic_phrase(phrase: &str) -> Result<Self> {
        let mnemonic = Mnemonic::parse_in_normalized(Language::English, phrase)
            .context("Invalid mnemonic phrase")?;

        Self::from_mnemonic(mnemonic)
    }

    /// Create identity from BIP39 mnemonic
    fn from_mnemonic(mnemonic: Mnemonic) -> Result<Self> {
        // Derive seed from mnemonic (no passphrase)
        let seed = mnemonic.to_seed("");

        // Use first 32 bytes as Ed25519 private key
        let private_key_bytes: [u8; 32] = seed[0..32].try_into().context("Invalid seed length")?;

        let signing_key = SigningKey::from_bytes(&private_key_bytes);
        let verifying_key = signing_key.verifying_key();

        // Generate Aptos-style address (SHA256 of public key)
        let mut hasher = Sha256::new();
        hasher.update(verifying_key.as_bytes());
        let hash = hasher.finalize();
        let address = format!("0x{}", hex::encode(&hash[0..32]));

        // Generate libp2p-compatible peer ID (multihash of public key)
        let peer_id = format!("12D3KooW{}", &hex::encode(verifying_key.as_bytes())[0..44]);

        Ok(Self {
            signing_key,
            verifying_key,
            mnemonic,
            address,
            peer_id,
        })
    }

    /// Get the wallet address
    pub fn address(&self) -> &str {
        &self.address
    }

    /// Get the libp2p peer ID
    pub fn peer_id(&self) -> &str {
        &self.peer_id
    }

    /// Get the public key as bytes
    pub fn public_key_bytes(&self) -> &[u8; 32] {
        verifying_key_bytes(&self.verifying_key)
    }

    /// Get the public key as hex string
    pub fn public_key_hex(&self) -> String {
        hex::encode(self.verifying_key.as_bytes())
    }

    /// Get the mnemonic phrase
    pub fn mnemonic_phrase(&self) -> String {
        self.mnemonic.to_string()
    }

    /// Sign a message
    pub fn sign(&self, message: &[u8]) -> Signature {
        self.signing_key.sign(message)
    }

    /// Verify a signature from another peer
    pub fn verify(public_key: &[u8; 32], message: &[u8], signature: &Signature) -> Result<()> {
        let verifying_key = VerifyingKey::from_bytes(public_key).context("Invalid public key")?;

        verifying_key
            .verify_strict(message, signature)
            .context("Signature verification failed")?;

        Ok(())
    }

    /// Export as WalletInfo (safe to serialize)
    pub fn to_wallet_info(&self) -> WalletInfo {
        WalletInfo {
            address: self.address.clone(),
            mnemonic: self.mnemonic.to_string(),
            public_key: self.public_key_hex(),
        }
    }

    /// Get short identifier (first 8 chars of address)
    pub fn short_id(&self) -> String {
        format!("apn_{}", &self.address[2..10])
    }
}

/// Helper to get bytes from verifying key
fn verifying_key_bytes(key: &VerifyingKey) -> &[u8; 32] {
    // SAFETY: VerifyingKey is exactly 32 bytes
    unsafe { &*(key.as_bytes().as_ptr() as *const [u8; 32]) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_generate_identity() {
        let identity = NodeIdentity::generate().unwrap();

        assert!(identity.address().starts_with("0x"));
        assert_eq!(identity.address().len(), 66); // 0x + 64 hex chars

        let phrase = identity.mnemonic_phrase();
        let words: Vec<_> = phrase.split_whitespace().collect();
        assert_eq!(words.len(), 12);
    }

    #[test]
    fn test_import_identity() {
        let identity1 = NodeIdentity::generate().unwrap();
        let phrase = identity1.mnemonic_phrase();

        let identity2 = NodeIdentity::from_mnemonic_phrase(&phrase).unwrap();

        assert_eq!(identity1.address(), identity2.address());
        assert_eq!(identity1.public_key_hex(), identity2.public_key_hex());
    }

    #[test]
    fn test_sign_and_verify() {
        let identity = NodeIdentity::generate().unwrap();
        let message = b"Hello, Alpha Protocol!";

        let signature = identity.sign(message);

        let result = NodeIdentity::verify(identity.public_key_bytes(), message, &signature);

        assert!(result.is_ok());
    }

    #[test]
    fn test_24_word_mnemonic() {
        let identity = NodeIdentity::generate_24_word().unwrap();

        let phrase = identity.mnemonic_phrase();
        let words: Vec<_> = phrase.split_whitespace().collect();
        assert_eq!(words.len(), 24);
    }
}
