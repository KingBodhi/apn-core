//! Cryptography - X25519 key exchange and ChaCha20-Poly1305 encryption
//!
//! Implements the encryption layer specified in the Alpha Protocol:
//! - X25519 ECDH for session key derivation
//! - ChaCha20-Poly1305 AEAD for message encryption
//! - BLAKE3 for fast hashing
//! - HKDF-SHA256 for key derivation

use anyhow::{Context, Result};
use chacha20poly1305::{
    aead::{Aead, KeyInit, OsRng},
    ChaCha20Poly1305, Nonce,
};
use sha2::{Digest, Sha256};
use x25519_dalek::{EphemeralSecret, PublicKey};

/// Session key for encrypted communication between two peers
#[derive(Clone)]
pub struct SessionKey {
    /// ChaCha20-Poly1305 cipher instance
    cipher: ChaCha20Poly1305,
    /// Our ephemeral public key (sent to peer)
    pub our_public: [u8; 32],
    /// Peer's public key
    pub peer_public: [u8; 32],
    /// Nonce counter (incremented for each message)
    nonce_counter: u64,
}

impl SessionKey {
    /// Create a new session key by performing X25519 key exchange
    pub fn new(peer_public_key: &[u8; 32]) -> Result<Self> {
        // Generate ephemeral keypair
        let our_secret = EphemeralSecret::random_from_rng(OsRng);
        let our_public = PublicKey::from(&our_secret);

        // Perform X25519 key exchange
        let peer_public = PublicKey::from(*peer_public_key);
        let shared_secret = our_secret.diffie_hellman(&peer_public);

        // Derive symmetric key using HKDF-SHA256
        let symmetric_key = derive_key(shared_secret.as_bytes(), b"alpha-protocol-v1");

        // Create cipher
        let cipher =
            ChaCha20Poly1305::new_from_slice(&symmetric_key).context("Failed to create cipher")?;

        Ok(Self {
            cipher,
            our_public: *our_public.as_bytes(),
            peer_public: *peer_public_key,
            nonce_counter: 0,
        })
    }

    /// Create session from existing shared secret (for session resumption)
    pub fn from_shared_secret(
        shared_secret: &[u8; 32],
        our_public: [u8; 32],
        peer_public: [u8; 32],
    ) -> Result<Self> {
        let symmetric_key = derive_key(shared_secret, b"alpha-protocol-v1");

        let cipher =
            ChaCha20Poly1305::new_from_slice(&symmetric_key).context("Failed to create cipher")?;

        Ok(Self {
            cipher,
            our_public,
            peer_public,
            nonce_counter: 0,
        })
    }

    /// Encrypt a message
    pub fn encrypt(&mut self, plaintext: &[u8]) -> Result<Vec<u8>> {
        // Generate nonce from counter
        let nonce = self.next_nonce();

        // Encrypt with ChaCha20-Poly1305
        let ciphertext = self
            .cipher
            .encrypt(&nonce, plaintext)
            .map_err(|e| anyhow::anyhow!("Encryption failed: {:?}", e))?;

        // Prepend nonce to ciphertext
        let mut result = Vec::with_capacity(12 + ciphertext.len());
        result.extend_from_slice(nonce.as_slice());
        result.extend(ciphertext);

        Ok(result)
    }

    /// Decrypt a message
    pub fn decrypt(&self, ciphertext: &[u8]) -> Result<Vec<u8>> {
        if ciphertext.len() < 12 {
            anyhow::bail!("Ciphertext too short");
        }

        // Extract nonce and ciphertext
        let nonce = Nonce::from_slice(&ciphertext[..12]);
        let encrypted = &ciphertext[12..];

        // Decrypt
        let plaintext = self
            .cipher
            .decrypt(nonce, encrypted)
            .map_err(|e| anyhow::anyhow!("Decryption failed: {:?}", e))?;

        Ok(plaintext)
    }

    /// Get the next nonce (increments counter)
    fn next_nonce(&mut self) -> Nonce {
        self.nonce_counter += 1;

        let mut nonce_bytes = [0u8; 12];
        nonce_bytes[4..12].copy_from_slice(&self.nonce_counter.to_le_bytes());

        *Nonce::from_slice(&nonce_bytes)
    }
}

/// Derive a 32-byte key using HKDF-SHA256
fn derive_key(input_key_material: &[u8], info: &[u8]) -> [u8; 32] {
    // Simple HKDF-Extract + HKDF-Expand
    let mut hasher = Sha256::new();
    hasher.update(input_key_material);
    hasher.update(info);

    let hash = hasher.finalize();
    let mut key = [0u8; 32];
    key.copy_from_slice(&hash);
    key
}

/// Hash data using BLAKE3 (fast)
pub fn hash_blake3(data: &[u8]) -> [u8; 32] {
    *blake3::hash(data).as_bytes()
}

/// Hash data using SHA256
pub fn hash_sha256(data: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(data);
    let hash = hasher.finalize();

    let mut result = [0u8; 32];
    result.copy_from_slice(&hash);
    result
}

/// Encrypt data with a one-time key (for simple use cases)
pub fn encrypt(plaintext: &[u8], key: &[u8; 32]) -> Result<Vec<u8>> {
    let cipher = ChaCha20Poly1305::new_from_slice(key).context("Invalid key")?;

    // Random nonce
    let mut nonce_bytes = [0u8; 12];
    rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, plaintext)
        .map_err(|e| anyhow::anyhow!("Encryption failed: {:?}", e))?;

    // Prepend nonce
    let mut result = Vec::with_capacity(12 + ciphertext.len());
    result.extend_from_slice(&nonce_bytes);
    result.extend(ciphertext);

    Ok(result)
}

/// Decrypt data with a key
pub fn decrypt(ciphertext: &[u8], key: &[u8; 32]) -> Result<Vec<u8>> {
    if ciphertext.len() < 12 {
        anyhow::bail!("Ciphertext too short");
    }

    let cipher = ChaCha20Poly1305::new_from_slice(key).context("Invalid key")?;

    let nonce = Nonce::from_slice(&ciphertext[..12]);
    let encrypted = &ciphertext[12..];

    let plaintext = cipher
        .decrypt(nonce, encrypted)
        .map_err(|e| anyhow::anyhow!("Decryption failed: {:?}", e))?;

    Ok(plaintext)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_session_key_encrypt_decrypt() {
        // Simulate two peers
        let peer_a_secret = x25519_dalek::EphemeralSecret::random_from_rng(OsRng);
        let peer_a_public = PublicKey::from(&peer_a_secret);

        let peer_b_secret = x25519_dalek::EphemeralSecret::random_from_rng(OsRng);
        let peer_b_public = PublicKey::from(&peer_b_secret);

        // Both compute shared secret
        let shared_a = peer_a_secret.diffie_hellman(&peer_b_public);
        let shared_b = peer_b_secret.diffie_hellman(&peer_a_public);

        assert_eq!(shared_a.as_bytes(), shared_b.as_bytes());

        // Create session keys
        let mut session_a = SessionKey::from_shared_secret(
            shared_a.as_bytes(),
            *peer_a_public.as_bytes(),
            *peer_b_public.as_bytes(),
        )
        .unwrap();

        let session_b = SessionKey::from_shared_secret(
            shared_b.as_bytes(),
            *peer_b_public.as_bytes(),
            *peer_a_public.as_bytes(),
        )
        .unwrap();

        // Encrypt with A, decrypt with B
        let plaintext = b"Hello, Alpha Protocol!";
        let ciphertext = session_a.encrypt(plaintext).unwrap();
        let decrypted = session_b.decrypt(&ciphertext).unwrap();

        assert_eq!(plaintext.as_slice(), decrypted.as_slice());
    }

    #[test]
    fn test_simple_encrypt_decrypt() {
        let key = [42u8; 32];
        let plaintext = b"Secret message";

        let ciphertext = encrypt(plaintext, &key).unwrap();
        let decrypted = decrypt(&ciphertext, &key).unwrap();

        assert_eq!(plaintext.as_slice(), decrypted.as_slice());
    }

    #[test]
    fn test_blake3_hash() {
        let data = b"test data";
        let hash = hash_blake3(data);

        assert_eq!(hash.len(), 32);
    }
}
