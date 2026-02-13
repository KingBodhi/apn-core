"""
End-to-end encryption for APN task payloads.

Uses ChaCha20-Poly1305 (AEAD) for encrypting task data between nodes.
Key exchange uses ECDH with the node's Ed25519 keys (converted to X25519).

Flow:
1. Sender derives shared secret from their Ed25519 key + recipient's public key
2. Sender encrypts payload with ChaCha20-Poly1305 using derived key
3. Recipient derives same shared secret and decrypts

This ensures that even though tasks are relayed through NATS,
only the intended recipient can read the payload.
"""

import base64
import json
import os
import logging
from pathlib import Path
from typing import Optional, Tuple

from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger("apn.crypto")

IDENTITY_FILE = Path.home() / ".apn" / "node_identity.json"


def _load_private_key() -> Optional[ed25519.Ed25519PrivateKey]:
    """Load the node's Ed25519 private key from identity file."""
    if not IDENTITY_FILE.exists():
        return None

    try:
        data = json.loads(IDENTITY_FILE.read_text())
        key_hex = data.get("private_key")
        if not key_hex:
            return None

        key_bytes = bytes.fromhex(key_hex)
        return ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes[:32])
    except Exception as e:
        logger.error(f"Failed to load private key: {e}")
        return None


def _ed25519_to_x25519_private(ed_key: ed25519.Ed25519PrivateKey) -> x25519.X25519PrivateKey:
    """Convert Ed25519 private key to X25519 for key exchange."""
    raw = ed_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return x25519.X25519PrivateKey.from_private_bytes(raw)


def _ed25519_public_to_x25519(ed_pub_hex: str) -> x25519.X25519PublicKey:
    """Convert Ed25519 public key (hex) to X25519 for key exchange."""
    pub_bytes = bytes.fromhex(ed_pub_hex)
    return x25519.X25519PublicKey.from_public_bytes(pub_bytes)


def derive_shared_key(peer_public_key_hex: str) -> Optional[bytes]:
    """
    Derive a shared symmetric key from our private key and peer's public key.
    Uses ECDH + HKDF to produce a 32-byte key suitable for ChaCha20-Poly1305.
    """
    private_key = _load_private_key()
    if not private_key:
        return None

    try:
        x_private = _ed25519_to_x25519_private(private_key)
        x_peer_public = _ed25519_public_to_x25519(peer_public_key_hex)

        shared_secret = x_private.exchange(x_peer_public)

        # Derive key using HKDF
        derived_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"apn-task-encryption-v1",
            info=b"chacha20-poly1305",
        ).derive(shared_secret)

        return derived_key

    except Exception as e:
        logger.error(f"Key derivation failed: {e}")
        return None


def encrypt_payload(plaintext: bytes, peer_public_key_hex: str) -> Optional[dict]:
    """
    Encrypt a payload for a specific peer using ChaCha20-Poly1305.

    Returns a dict with:
    - nonce: base64-encoded 12-byte nonce
    - ciphertext: base64-encoded encrypted data
    - encrypted: True
    """
    key = derive_shared_key(peer_public_key_hex)
    if not key:
        return None

    try:
        nonce = os.urandom(12)
        cipher = ChaCha20Poly1305(key)
        ciphertext = cipher.encrypt(nonce, plaintext, None)

        return {
            "encrypted": True,
            "algorithm": "chacha20-poly1305",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return None


def decrypt_payload(encrypted_data: dict, peer_public_key_hex: str) -> Optional[bytes]:
    """
    Decrypt a payload received from a specific peer.

    Args:
        encrypted_data: dict with nonce, ciphertext (base64-encoded)
        peer_public_key_hex: the sender's Ed25519 public key (hex)

    Returns:
        Decrypted bytes, or None on failure.
    """
    if not encrypted_data.get("encrypted"):
        return None

    key = derive_shared_key(peer_public_key_hex)
    if not key:
        return None

    try:
        nonce = base64.b64decode(encrypted_data["nonce"])
        ciphertext = base64.b64decode(encrypted_data["ciphertext"])

        cipher = ChaCha20Poly1305(key)
        plaintext = cipher.decrypt(nonce, ciphertext, None)

        return plaintext
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return None


def encrypt_task_payload(task_data: dict, peer_public_key_hex: str) -> dict:
    """
    Encrypt a task payload dict for a specific peer.
    Falls back to plaintext if encryption is not possible.
    """
    plaintext = json.dumps(task_data).encode("utf-8")
    encrypted = encrypt_payload(plaintext, peer_public_key_hex)

    if encrypted:
        return encrypted

    # Fallback: send unencrypted (local network or missing keys)
    logger.warning("Encryption not available, sending task unencrypted")
    return task_data


def decrypt_task_payload(message: dict, peer_public_key_hex: str) -> dict:
    """
    Decrypt a received task message.
    If not encrypted, returns the message as-is.
    """
    if not message.get("encrypted"):
        return message

    decrypted = decrypt_payload(message, peer_public_key_hex)
    if decrypted:
        return json.loads(decrypted.decode("utf-8"))

    logger.error("Failed to decrypt task payload")
    return message
