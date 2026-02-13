"""
P2P File Transfer System for APN Core

Transfers files between APN nodes using NATS for signaling and data:
- Small files (<768KB): Single NATS message (base64 encoded)
- Medium files (768KB - 100MB): Chunked via NATS (512KB chunks)
- Progress reporting via NATS events

NATS Topics:
- apn.files.request.{node_id}    - Incoming transfer requests
- apn.files.offer.{node_id}      - Transfer offer (metadata before sending)
- apn.files.accept.{transfer_id} - Accept a transfer offer
- apn.files.chunk.{transfer_id}  - File data chunks
- apn.files.ack.{transfer_id}    - Chunk acknowledgements
- apn.files.progress             - Progress updates (broadcast)
- apn.files.complete             - Transfer complete notifications
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from nats.aio.client import Client as NATS

logger = logging.getLogger("apn.file_transfer")

# Constants
CHUNK_SIZE = 512 * 1024        # 512KB per chunk
SMALL_FILE_LIMIT = 768 * 1024  # Files under 768KB sent in single message
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB max via NATS
TRANSFER_TIMEOUT = 600         # 10 minutes max per transfer
ACK_TIMEOUT = 30               # 30 seconds to wait for chunk ack
MAX_RETRIES = 3                # Retry failed chunks
RECEIVE_DIR = Path.home() / "topos" / "received"


class TransferStatus(str, Enum):
    PENDING = "pending"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransferDirection(str, Enum):
    SEND = "send"
    RECEIVE = "receive"


@dataclass
class TransferInfo:
    transfer_id: str
    file_name: str
    file_size: int
    file_hash: str
    source_node: str
    target_node: str
    direction: str
    status: str = TransferStatus.PENDING
    chunks_total: int = 0
    chunks_transferred: int = 0
    bytes_transferred: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0
    error: Optional[str] = None
    local_path: Optional[str] = None

    @property
    def progress_pct(self) -> float:
        if self.chunks_total == 0:
            return 0.0
        return (self.chunks_transferred / self.chunks_total) * 100.0

    @property
    def speed_bps(self) -> float:
        elapsed = (self.completed_at or time.time()) - self.started_at
        if elapsed <= 0:
            return 0.0
        return self.bytes_transferred / elapsed

    def to_dict(self) -> dict:
        d = asdict(self)
        d["progress_pct"] = self.progress_pct
        d["speed_bps"] = self.speed_bps
        return d


class FileTransferService:
    """Manages P2P file transfers over NATS."""

    def __init__(self, nats_url: str, node_id: str, receive_dir: Optional[Path] = None):
        self.nats_url = nats_url
        self.node_id = node_id
        self.receive_dir = receive_dir or RECEIVE_DIR
        self.nats: Optional[NATS] = None
        self._running = False

        # Transfer tracking
        self._active_transfers: Dict[str, TransferInfo] = {}
        self._transfer_history: List[TransferInfo] = []
        self._chunk_buffers: Dict[str, Dict[int, bytes]] = {}
        self._ack_events: Dict[str, asyncio.Event] = {}

        # Auto-accept transfers (can be toggled via settings)
        self.auto_accept = True

        # Ensure receive directory exists
        self.receive_dir.mkdir(parents=True, exist_ok=True)

    async def start(self):
        """Connect to NATS and subscribe to file transfer topics."""
        self.nats = NATS()
        await self.nats.connect(self.nats_url)
        self._running = True

        # Subscribe to incoming transfer requests/offers
        request_topic = f"apn.files.request.{self.node_id}"
        offer_topic = f"apn.files.offer.{self.node_id}"
        await self.nats.subscribe(request_topic, cb=self._handle_request)
        await self.nats.subscribe(offer_topic, cb=self._handle_offer)

        logger.info(f"File transfer service started, listening on {request_topic}")

    async def stop(self):
        """Disconnect and clean up."""
        self._running = False
        if self.nats and self.nats.is_connected:
            await self.nats.drain()
        logger.info("File transfer service stopped")

    # ── Sending ──────────────────────────────────────────────────

    async def send_file(self, target_node_id: str, file_path: str) -> TransferInfo:
        """
        Send a file to a target node.

        For small files, sends directly. For larger files, sends an offer
        first and waits for acceptance before chunked transfer.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"File too large ({file_size} bytes). Max: {MAX_FILE_SIZE}")
        if file_size == 0:
            raise ValueError("Cannot transfer empty file")

        # Calculate file hash
        file_hash = await self._hash_file(path)

        transfer_id = str(uuid.uuid4())[:12]
        chunks_total = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)

        info = TransferInfo(
            transfer_id=transfer_id,
            file_name=path.name,
            file_size=file_size,
            file_hash=file_hash,
            source_node=self.node_id,
            target_node=target_node_id,
            direction=TransferDirection.SEND,
            status=TransferStatus.PENDING,
            chunks_total=chunks_total,
            started_at=time.time(),
            local_path=str(path),
        )
        self._active_transfers[transfer_id] = info

        if file_size <= SMALL_FILE_LIMIT:
            # Small file: send directly in a single message
            await self._send_small_file(info, path)
        else:
            # Larger file: send offer, then chunked transfer on accept
            await self._send_offer(info, target_node_id)

        return info

    async def _send_small_file(self, info: TransferInfo, path: Path):
        """Send a small file as a single base64-encoded NATS message."""
        data = path.read_bytes()
        encoded = base64.b64encode(data).decode("ascii")

        message = {
            "type": "small_file",
            "transfer_id": info.transfer_id,
            "file_name": info.file_name,
            "file_size": info.file_size,
            "file_hash": info.file_hash,
            "source_node": self.node_id,
            "data": encoded,
        }

        topic = f"apn.files.request.{info.target_node}"
        await self.nats.publish(topic, json.dumps(message).encode())

        info.status = TransferStatus.TRANSFERRING
        info.chunks_transferred = 1
        info.bytes_transferred = info.file_size

        # Wait briefly for ack (fire-and-forget for small files is also acceptable)
        info.status = TransferStatus.COMPLETED
        info.completed_at = time.time()
        await self._broadcast_progress(info)
        self._archive_transfer(info)

        logger.info(f"Small file sent: {info.file_name} ({info.file_size}B) -> {info.target_node}")

    async def _send_offer(self, info: TransferInfo, target_node_id: str):
        """Send a transfer offer to target node (for chunked transfers)."""
        offer = {
            "type": "offer",
            "transfer_id": info.transfer_id,
            "file_name": info.file_name,
            "file_size": info.file_size,
            "file_hash": info.file_hash,
            "chunks_total": info.chunks_total,
            "chunk_size": CHUNK_SIZE,
            "source_node": self.node_id,
        }

        topic = f"apn.files.offer.{target_node_id}"
        await self.nats.publish(topic, json.dumps(offer).encode())
        info.status = TransferStatus.OFFERED

        # Subscribe to accept/reject for this transfer
        accept_topic = f"apn.files.accept.{info.transfer_id}"
        ack_topic = f"apn.files.ack.{info.transfer_id}"

        accept_event = asyncio.Event()
        self._ack_events[info.transfer_id] = accept_event

        async def on_accept(msg):
            accept_event.set()

        await self.nats.subscribe(accept_topic, cb=on_accept)
        await self.nats.subscribe(ack_topic, cb=lambda msg: self._handle_chunk_ack(msg, info))

        # Wait for acceptance (with timeout)
        try:
            await asyncio.wait_for(accept_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            info.status = TransferStatus.FAILED
            info.error = "Transfer offer timed out (not accepted)"
            self._archive_transfer(info)
            return

        # Accepted - start chunked transfer
        info.status = TransferStatus.ACCEPTED
        asyncio.create_task(self._send_chunks(info))

    async def _send_chunks(self, info: TransferInfo):
        """Send file in chunks via NATS."""
        info.status = TransferStatus.TRANSFERRING
        path = Path(info.local_path)
        chunk_topic = f"apn.files.chunk.{info.transfer_id}"

        try:
            with open(path, "rb") as f:
                for seq in range(info.chunks_total):
                    if not self._running:
                        info.status = TransferStatus.CANCELLED
                        return

                    chunk_data = f.read(CHUNK_SIZE)
                    encoded = base64.b64encode(chunk_data).decode("ascii")

                    chunk_msg = {
                        "seq": seq,
                        "data": encoded,
                        "size": len(chunk_data),
                    }

                    await self.nats.publish(chunk_topic, json.dumps(chunk_msg).encode())
                    info.chunks_transferred = seq + 1
                    info.bytes_transferred += len(chunk_data)

                    # Report progress every 10 chunks or on last chunk
                    if seq % 10 == 0 or seq == info.chunks_total - 1:
                        await self._broadcast_progress(info)

                    # Small delay to avoid overwhelming NATS
                    await asyncio.sleep(0.01)

            # Send completion signal
            complete_msg = {
                "type": "transfer_complete",
                "transfer_id": info.transfer_id,
                "file_hash": info.file_hash,
                "chunks_total": info.chunks_total,
            }
            await self.nats.publish(chunk_topic, json.dumps(complete_msg).encode())

            info.status = TransferStatus.COMPLETED
            info.completed_at = time.time()
            await self._broadcast_progress(info)
            self._archive_transfer(info)

            logger.info(
                f"Chunked transfer complete: {info.file_name} "
                f"({info.chunks_total} chunks, {info.file_size}B) -> {info.target_node}"
            )

        except Exception as e:
            info.status = TransferStatus.FAILED
            info.error = str(e)
            self._archive_transfer(info)
            logger.error(f"Transfer failed: {info.transfer_id} - {e}")

    # ── Receiving ────────────────────────────────────────────────

    async def _handle_request(self, msg):
        """Handle incoming file transfer request (small file or general request)."""
        try:
            data = json.loads(msg.data.decode())
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "small_file":
            await self._receive_small_file(data)

    async def _handle_offer(self, msg):
        """Handle incoming transfer offer (for chunked transfers)."""
        try:
            data = json.loads(msg.data.decode())
        except json.JSONDecodeError:
            return

        if data.get("type") != "offer":
            return

        transfer_id = data["transfer_id"]
        info = TransferInfo(
            transfer_id=transfer_id,
            file_name=data["file_name"],
            file_size=data["file_size"],
            file_hash=data["file_hash"],
            source_node=data["source_node"],
            target_node=self.node_id,
            direction=TransferDirection.RECEIVE,
            status=TransferStatus.OFFERED,
            chunks_total=data["chunks_total"],
            started_at=time.time(),
        )
        self._active_transfers[transfer_id] = info
        self._chunk_buffers[transfer_id] = {}

        logger.info(
            f"Transfer offer received: {info.file_name} "
            f"({info.file_size}B, {info.chunks_total} chunks) from {info.source_node}"
        )

        if self.auto_accept:
            await self._accept_transfer(info)
        else:
            # Offer stays pending until manually accepted via API
            await self._broadcast_progress(info)

    async def _accept_transfer(self, info: TransferInfo):
        """Accept a transfer offer and start receiving chunks."""
        info.status = TransferStatus.ACCEPTED

        # Notify sender we accept
        accept_topic = f"apn.files.accept.{info.transfer_id}"
        await self.nats.publish(accept_topic, b'{"accepted": true}')

        # Subscribe to chunks for this transfer
        chunk_topic = f"apn.files.chunk.{info.transfer_id}"
        await self.nats.subscribe(chunk_topic, cb=lambda msg: self._handle_chunk(msg, info))

        info.status = TransferStatus.TRANSFERRING
        logger.info(f"Accepted transfer {info.transfer_id}, waiting for chunks...")

    async def accept_transfer(self, transfer_id: str) -> bool:
        """Manually accept a pending transfer offer (called from API)."""
        info = self._active_transfers.get(transfer_id)
        if not info or info.status != TransferStatus.OFFERED:
            return False
        await self._accept_transfer(info)
        return True

    async def cancel_transfer(self, transfer_id: str) -> bool:
        """Cancel an active transfer."""
        info = self._active_transfers.get(transfer_id)
        if not info:
            return False
        info.status = TransferStatus.CANCELLED
        info.error = "Cancelled by user"
        self._archive_transfer(info)
        return True

    async def _receive_small_file(self, data: dict):
        """Receive a small file sent as a single message."""
        transfer_id = data["transfer_id"]
        file_name = data["file_name"]
        file_hash = data["file_hash"]
        source_node = data["source_node"]

        try:
            file_data = base64.b64decode(data["data"])
        except Exception as e:
            logger.error(f"Failed to decode small file: {e}")
            return

        # Verify hash
        actual_hash = hashlib.sha256(file_data).hexdigest()
        if actual_hash != file_hash:
            logger.error(f"Hash mismatch for {file_name}: expected {file_hash}, got {actual_hash}")
            return

        # Save file
        dest = self._safe_dest_path(file_name)
        dest.write_bytes(file_data)

        info = TransferInfo(
            transfer_id=transfer_id,
            file_name=file_name,
            file_size=len(file_data),
            file_hash=file_hash,
            source_node=source_node,
            target_node=self.node_id,
            direction=TransferDirection.RECEIVE,
            status=TransferStatus.COMPLETED,
            chunks_total=1,
            chunks_transferred=1,
            bytes_transferred=len(file_data),
            started_at=time.time(),
            completed_at=time.time(),
            local_path=str(dest),
        )
        self._transfer_history.append(info)
        await self._broadcast_progress(info)

        logger.info(f"Small file received: {file_name} ({len(file_data)}B) from {source_node} -> {dest}")

    async def _handle_chunk(self, msg, info: TransferInfo):
        """Handle an incoming file chunk."""
        try:
            data = json.loads(msg.data.decode())
        except json.JSONDecodeError:
            return

        # Check if this is the completion signal
        if data.get("type") == "transfer_complete":
            await self._finalize_chunked_transfer(info, data)
            return

        seq = data["seq"]
        chunk_data = base64.b64decode(data["data"])

        self._chunk_buffers[info.transfer_id][seq] = chunk_data
        info.chunks_transferred = len(self._chunk_buffers[info.transfer_id])
        info.bytes_transferred += len(chunk_data)

        # Send ack
        ack_topic = f"apn.files.ack.{info.transfer_id}"
        await self.nats.publish(ack_topic, json.dumps({"seq": seq}).encode())

        # Report progress periodically
        if seq % 10 == 0 or info.chunks_transferred == info.chunks_total:
            await self._broadcast_progress(info)

    def _handle_chunk_ack(self, msg, info: TransferInfo):
        """Handle chunk acknowledgement from receiver (for sender-side tracking)."""
        # Currently just logging; could implement retry logic here
        pass

    async def _finalize_chunked_transfer(self, info: TransferInfo, complete_data: dict):
        """Reassemble chunks and verify the complete file."""
        buffer = self._chunk_buffers.get(info.transfer_id, {})

        # Check we have all chunks
        expected = complete_data.get("chunks_total", info.chunks_total)
        if len(buffer) < expected:
            missing = set(range(expected)) - set(buffer.keys())
            info.status = TransferStatus.FAILED
            info.error = f"Missing {len(missing)} chunks: {sorted(missing)[:10]}"
            self._archive_transfer(info)
            logger.error(f"Transfer incomplete: {info.transfer_id} - {info.error}")
            return

        # Reassemble file
        file_data = b""
        for seq in range(expected):
            file_data += buffer[seq]

        # Verify hash
        actual_hash = hashlib.sha256(file_data).hexdigest()
        expected_hash = complete_data.get("file_hash", info.file_hash)
        if actual_hash != expected_hash:
            info.status = TransferStatus.FAILED
            info.error = f"Hash mismatch: expected {expected_hash}, got {actual_hash}"
            self._archive_transfer(info)
            logger.error(f"Transfer hash mismatch: {info.transfer_id}")
            return

        # Save file
        dest = self._safe_dest_path(info.file_name)
        dest.write_bytes(file_data)
        info.local_path = str(dest)
        info.status = TransferStatus.COMPLETED
        info.completed_at = time.time()

        # Clean up buffer
        del self._chunk_buffers[info.transfer_id]

        await self._broadcast_progress(info)
        self._archive_transfer(info)

        logger.info(
            f"Chunked transfer complete: {info.file_name} "
            f"({len(file_data)}B, {expected} chunks) from {info.source_node} -> {dest}"
        )

    # ── Progress & Status ────────────────────────────────────────

    async def _broadcast_progress(self, info: TransferInfo):
        """Publish transfer progress to NATS for UI consumption."""
        if not self.nats or not self.nats.is_connected:
            return

        progress = {
            "transfer_id": info.transfer_id,
            "file_name": info.file_name,
            "file_size": info.file_size,
            "direction": info.direction,
            "status": info.status,
            "progress_pct": round(info.progress_pct, 1),
            "bytes_transferred": info.bytes_transferred,
            "chunks_transferred": info.chunks_transferred,
            "chunks_total": info.chunks_total,
            "speed_bps": round(info.speed_bps, 0),
            "source_node": info.source_node,
            "target_node": info.target_node,
            "error": info.error,
        }

        await self.nats.publish("apn.files.progress", json.dumps(progress).encode())

    def get_active_transfers(self) -> List[dict]:
        """Get all active (non-archived) transfers."""
        return [t.to_dict() for t in self._active_transfers.values()]

    def get_transfer_history(self, limit: int = 50) -> List[dict]:
        """Get recent completed/failed transfers."""
        return [t.to_dict() for t in self._transfer_history[-limit:]]

    def get_transfer(self, transfer_id: str) -> Optional[dict]:
        """Get a specific transfer by ID."""
        info = self._active_transfers.get(transfer_id)
        if not info:
            # Check history
            for t in reversed(self._transfer_history):
                if t.transfer_id == transfer_id:
                    return t.to_dict()
            return None
        return info.to_dict()

    # ── Helpers ──────────────────────────────────────────────────

    def _safe_dest_path(self, file_name: str) -> Path:
        """Generate a safe destination path, avoiding overwrites."""
        # Sanitize filename
        safe_name = Path(file_name).name  # Strip any directory components
        safe_name = safe_name.replace("..", "_")

        dest = self.receive_dir / safe_name
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = self.receive_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        return dest

    async def _hash_file(self, path: Path) -> str:
        """Calculate SHA-256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()

    def _archive_transfer(self, info: TransferInfo):
        """Move transfer from active to history."""
        self._active_transfers.pop(info.transfer_id, None)
        self._transfer_history.append(info)
        # Keep history bounded
        if len(self._transfer_history) > 200:
            self._transfer_history = self._transfer_history[-100:]


# ── Global instance management ───────────────────────────────────

_file_transfer_service: Optional[FileTransferService] = None


async def start_file_transfer(
    nats_url: str,
    node_id: str,
    receive_dir: Optional[str] = None,
) -> FileTransferService:
    """Start the global file transfer service."""
    global _file_transfer_service

    recv_dir = Path(receive_dir) if receive_dir else None
    _file_transfer_service = FileTransferService(nats_url, node_id, recv_dir)
    await _file_transfer_service.start()
    return _file_transfer_service


async def stop_file_transfer():
    """Stop the global file transfer service."""
    global _file_transfer_service
    if _file_transfer_service:
        await _file_transfer_service.stop()
        _file_transfer_service = None


def get_file_transfer() -> Optional[FileTransferService]:
    """Get the running file transfer service instance."""
    return _file_transfer_service
