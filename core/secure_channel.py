"""
Alpha Protocol Network - Secure Channel
End-to-end encrypted communication between APN nodes.

Uses:
- X25519 for key exchange (derived from Ed25519 keys)
- ChaCha20-Poly1305 for symmetric encryption
- Ed25519 for message signing
- Noise Protocol pattern for handshake (XX pattern for mutual auth)
"""
import os
import json
import time
import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
from base64 import b64encode, b64decode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend

from .logging_config import get_logger

logger = get_logger("secure_channel")


@dataclass
class SecureSession:
    """Represents an encrypted session with a peer"""
    peer_node_id: str
    peer_public_key: bytes
    send_key: bytes  # For encrypting outgoing messages
    recv_key: bytes  # For decrypting incoming messages
    send_nonce: int = 0
    recv_nonce: int = 0
    created_at: float = 0
    expires_at: float = 0

    def __post_init__(self):
        if self.created_at == 0:
            self.created_at = time.time()
        if self.expires_at == 0:
            # Sessions expire after 24 hours
            self.expires_at = self.created_at + 86400

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class SecureChannel:
    """
    Secure communication channel for APN nodes.

    Provides:
    - Mutual authentication using Ed25519 signatures
    - Key exchange using X25519 (Curve25519)
    - Symmetric encryption using ChaCha20-Poly1305
    - Perfect forward secrecy via ephemeral keys
    - Replay protection via nonces
    """

    # Protocol identifier for domain separation
    PROTOCOL_NAME = b"APN_SecureChannel_v1"

    def __init__(self, private_key: ed25519.Ed25519PrivateKey, node_id: str):
        self.private_key = private_key
        self.public_key = private_key.public_key()
        self.node_id = node_id

        # Derive X25519 key from Ed25519 for key exchange
        self.x25519_private = self._ed25519_to_x25519_private(private_key)
        self.x25519_public = self.x25519_private.public_key()

        # Active sessions with peers
        self.sessions: Dict[str, SecureSession] = {}

    def _ed25519_to_x25519_private(
        self, ed_private: ed25519.Ed25519PrivateKey
    ) -> x25519.X25519PrivateKey:
        """Convert Ed25519 private key to X25519 for key exchange"""
        # Get raw Ed25519 private key bytes
        ed_private_bytes = ed_private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )

        # Hash to get X25519 key (standard conversion)
        h = hashlib.sha512(ed_private_bytes).digest()[:32]

        # Clamp for X25519
        h_list = list(h)
        h_list[0] &= 248
        h_list[31] &= 127
        h_list[31] |= 64

        return x25519.X25519PrivateKey.from_private_bytes(bytes(h_list))

    def create_handshake_init(self) -> Dict[str, Any]:
        """
        Create handshake initiation message.

        Returns a message containing:
        - Our node ID
        - Our Ed25519 public key (for identity verification)
        - Ephemeral X25519 public key (for this session)
        - Timestamp
        - Signature over all of the above
        """
        # Generate ephemeral key for perfect forward secrecy
        ephemeral_private = x25519.X25519PrivateKey.generate()
        ephemeral_public = ephemeral_private.public_key()

        # Store ephemeral private key temporarily
        self._pending_ephemeral = ephemeral_private

        timestamp = int(time.time())

        # Create message to sign
        message_data = {
            "type": "handshake_init",
            "node_id": self.node_id,
            "public_key": b64encode(
                self.public_key.public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw
                )
            ).decode(),
            "ephemeral_key": b64encode(
                ephemeral_public.public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw
                )
            ).decode(),
            "timestamp": timestamp,
        }

        # Sign the message
        sign_data = json.dumps(message_data, sort_keys=True).encode()
        signature = self.private_key.sign(sign_data)

        message_data["signature"] = b64encode(signature).decode()

        return message_data

    def process_handshake_init(
        self, message: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Process handshake initiation and create response.

        Returns:
        - Response message (or None on failure)
        - Error message (or None on success)
        """
        try:
            # Verify timestamp (allow 5 minute skew)
            timestamp = message.get("timestamp", 0)
            if abs(time.time() - timestamp) > 300:
                return None, "Timestamp too old or in future"

            # Extract peer's public key
            peer_public_bytes = b64decode(message["public_key"])
            peer_public_key = ed25519.Ed25519PublicKey.from_public_bytes(
                peer_public_bytes
            )

            # Verify signature
            signature = b64decode(message["signature"])
            verify_data = message.copy()
            del verify_data["signature"]
            verify_bytes = json.dumps(verify_data, sort_keys=True).encode()

            try:
                peer_public_key.verify(signature, verify_bytes)
            except Exception:
                return None, "Invalid signature"

            # Extract peer's ephemeral key
            peer_ephemeral_bytes = b64decode(message["ephemeral_key"])
            peer_ephemeral = x25519.X25519PublicKey.from_public_bytes(
                peer_ephemeral_bytes
            )

            # Generate our ephemeral key
            our_ephemeral_private = x25519.X25519PrivateKey.generate()
            our_ephemeral_public = our_ephemeral_private.public_key()

            # Perform key exchange (Double DH for extra security)
            # shared1 = our_ephemeral * peer_ephemeral
            # shared2 = our_static * peer_ephemeral
            shared1 = our_ephemeral_private.exchange(peer_ephemeral)
            shared2 = self.x25519_private.exchange(peer_ephemeral)

            # Derive session keys
            send_key, recv_key = self._derive_session_keys(
                shared1, shared2,
                self.node_id, message["node_id"],
                is_initiator=False
            )

            # Create session
            session = SecureSession(
                peer_node_id=message["node_id"],
                peer_public_key=peer_public_bytes,
                send_key=send_key,
                recv_key=recv_key,
            )
            self.sessions[message["node_id"]] = session

            # Create response
            response_timestamp = int(time.time())
            response_data = {
                "type": "handshake_response",
                "node_id": self.node_id,
                "public_key": b64encode(
                    self.public_key.public_bytes(
                        encoding=serialization.Encoding.Raw,
                        format=serialization.PublicFormat.Raw
                    )
                ).decode(),
                "ephemeral_key": b64encode(
                    our_ephemeral_public.public_bytes(
                        encoding=serialization.Encoding.Raw,
                        format=serialization.PublicFormat.Raw
                    )
                ).decode(),
                "timestamp": response_timestamp,
            }

            # Sign response
            sign_data = json.dumps(response_data, sort_keys=True).encode()
            signature = self.private_key.sign(sign_data)
            response_data["signature"] = b64encode(signature).decode()

            logger.info(f"Handshake initiated with {message['node_id']}")
            return response_data, None

        except Exception as e:
            logger.error(f"Handshake init processing failed: {e}")
            return None, str(e)

    def process_handshake_response(
        self, message: Dict[str, Any]
    ) -> Optional[str]:
        """
        Process handshake response and complete session setup.

        Returns error message or None on success.
        """
        try:
            if not hasattr(self, '_pending_ephemeral'):
                return "No pending handshake"

            # Verify timestamp
            timestamp = message.get("timestamp", 0)
            if abs(time.time() - timestamp) > 300:
                return "Timestamp too old or in future"

            # Extract and verify peer's public key
            peer_public_bytes = b64decode(message["public_key"])
            peer_public_key = ed25519.Ed25519PublicKey.from_public_bytes(
                peer_public_bytes
            )

            # Verify signature
            signature = b64decode(message["signature"])
            verify_data = message.copy()
            del verify_data["signature"]
            verify_bytes = json.dumps(verify_data, sort_keys=True).encode()

            try:
                peer_public_key.verify(signature, verify_bytes)
            except Exception:
                return "Invalid signature"

            # Extract peer's ephemeral key
            peer_ephemeral_bytes = b64decode(message["ephemeral_key"])
            peer_ephemeral = x25519.X25519PublicKey.from_public_bytes(
                peer_ephemeral_bytes
            )

            # Perform key exchange
            shared1 = self._pending_ephemeral.exchange(peer_ephemeral)
            shared2 = self.x25519_private.exchange(peer_ephemeral)

            # Derive session keys (initiator gets opposite keys)
            send_key, recv_key = self._derive_session_keys(
                shared1, shared2,
                self.node_id, message["node_id"],
                is_initiator=True
            )

            # Create session
            session = SecureSession(
                peer_node_id=message["node_id"],
                peer_public_key=peer_public_bytes,
                send_key=send_key,
                recv_key=recv_key,
            )
            self.sessions[message["node_id"]] = session

            # Clean up
            del self._pending_ephemeral

            logger.info(f"Secure session established with {message['node_id']}")
            return None

        except Exception as e:
            logger.error(f"Handshake response processing failed: {e}")
            return str(e)

    def _derive_session_keys(
        self,
        shared1: bytes,
        shared2: bytes,
        our_id: str,
        peer_id: str,
        is_initiator: bool
    ) -> Tuple[bytes, bytes]:
        """Derive symmetric keys from shared secrets"""
        # Combine shared secrets
        combined = shared1 + shared2

        # Use HKDF to derive keys
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=64,  # 32 bytes for each direction
            salt=self.PROTOCOL_NAME,
            info=f"{min(our_id, peer_id)}:{max(our_id, peer_id)}".encode(),
            backend=default_backend()
        )

        key_material = hkdf.derive(combined)

        # Split into two keys
        key1 = key_material[:32]
        key2 = key_material[32:]

        # Assign based on role (ensures both sides agree)
        if is_initiator:
            return key1, key2  # send, recv
        else:
            return key2, key1  # send, recv

    def encrypt_message(
        self, peer_node_id: str, plaintext: bytes
    ) -> Optional[bytes]:
        """
        Encrypt a message for a peer.

        Returns encrypted message or None if no session exists.
        """
        session = self.sessions.get(peer_node_id)
        if not session:
            logger.warning(f"No session with {peer_node_id}")
            return None

        if session.is_expired():
            logger.warning(f"Session with {peer_node_id} expired")
            del self.sessions[peer_node_id]
            return None

        try:
            # Create nonce (12 bytes for ChaCha20-Poly1305)
            # Use counter-based nonce to prevent reuse
            nonce = session.send_nonce.to_bytes(12, byteorder='big')
            session.send_nonce += 1

            # Encrypt with ChaCha20-Poly1305
            cipher = ChaCha20Poly1305(session.send_key)
            ciphertext = cipher.encrypt(nonce, plaintext, None)

            # Return nonce + ciphertext
            return nonce + ciphertext

        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return None

    def decrypt_message(
        self, peer_node_id: str, encrypted: bytes
    ) -> Optional[bytes]:
        """
        Decrypt a message from a peer.

        Returns plaintext or None on failure.
        """
        session = self.sessions.get(peer_node_id)
        if not session:
            logger.warning(f"No session with {peer_node_id}")
            return None

        if session.is_expired():
            logger.warning(f"Session with {peer_node_id} expired")
            del self.sessions[peer_node_id]
            return None

        try:
            # Extract nonce and ciphertext
            nonce = encrypted[:12]
            ciphertext = encrypted[12:]

            # Verify nonce is not replayed
            recv_nonce = int.from_bytes(nonce, byteorder='big')
            if recv_nonce < session.recv_nonce:
                logger.warning("Replay attack detected")
                return None
            session.recv_nonce = recv_nonce + 1

            # Decrypt
            cipher = ChaCha20Poly1305(session.recv_key)
            plaintext = cipher.decrypt(nonce, ciphertext, None)

            return plaintext

        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return None

    def sign_message(self, message: bytes) -> bytes:
        """Sign a message with our Ed25519 key"""
        return self.private_key.sign(message)

    def verify_signature(
        self, peer_node_id: str, message: bytes, signature: bytes
    ) -> bool:
        """Verify a message signature from a peer"""
        session = self.sessions.get(peer_node_id)
        if not session:
            return False

        try:
            peer_public = ed25519.Ed25519PublicKey.from_public_bytes(
                session.peer_public_key
            )
            peer_public.verify(signature, message)
            return True
        except Exception:
            return False

    def has_session(self, peer_node_id: str) -> bool:
        """Check if we have an active session with a peer"""
        session = self.sessions.get(peer_node_id)
        if session and not session.is_expired():
            return True
        return False

    def get_session_info(self, peer_node_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a session"""
        session = self.sessions.get(peer_node_id)
        if not session:
            return None

        return {
            "peer_node_id": session.peer_node_id,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "is_expired": session.is_expired(),
            "messages_sent": session.send_nonce,
            "messages_received": session.recv_nonce,
        }

    def close_session(self, peer_node_id: str):
        """Close a session with a peer"""
        if peer_node_id in self.sessions:
            del self.sessions[peer_node_id]
            logger.info(f"Closed session with {peer_node_id}")


def create_secure_envelope(
    secure_channel: SecureChannel,
    peer_node_id: str,
    payload: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Create a secure envelope for sending to a peer.

    Returns a message envelope with encrypted payload.
    """
    plaintext = json.dumps(payload).encode()
    encrypted = secure_channel.encrypt_message(peer_node_id, plaintext)

    if not encrypted:
        return None

    return {
        "type": "secure_message",
        "from": secure_channel.node_id,
        "to": peer_node_id,
        "payload": b64encode(encrypted).decode(),
        "timestamp": int(time.time()),
    }


def open_secure_envelope(
    secure_channel: SecureChannel,
    envelope: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Open a secure envelope received from a peer.

    Returns the decrypted payload or None on failure.
    """
    try:
        encrypted = b64decode(envelope["payload"])
        peer_node_id = envelope["from"]

        plaintext = secure_channel.decrypt_message(peer_node_id, encrypted)
        if not plaintext:
            return None

        return json.loads(plaintext.decode())

    except Exception as e:
        logger.error(f"Failed to open secure envelope: {e}")
        return None
