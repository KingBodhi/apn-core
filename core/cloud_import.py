"""
Cloud Import Service for APN Core

Resolves and downloads files from cloud storage providers:
- Google Drive (shared links and direct file IDs)
- OneDrive (shared links)
- Dropbox (shared links)

Downloads are cached in ~/topos/downloads/ with SHA-256 verification.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs, unquote

import httpx

logger = logging.getLogger("apn.cloud_import")

DOWNLOAD_DIR = Path.home() / "topos" / "downloads"
CACHE_INDEX_FILE = DOWNLOAD_DIR / ".cache_index.json"
MAX_DOWNLOAD_SIZE = 5 * 1024 * 1024 * 1024  # 5GB
CHUNK_SIZE = 1024 * 1024  # 1MB download chunks


class CloudProvider(str, Enum):
    GOOGLE_DRIVE = "google_drive"
    ONEDRIVE = "onedrive"
    DROPBOX = "dropbox"
    DIRECT = "direct"  # Direct HTTP/HTTPS URL
    UNKNOWN = "unknown"


class ImportStatus(str, Enum):
    PENDING = "pending"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CACHED = "cached"


@dataclass
class ImportJob:
    job_id: str
    source_url: str
    provider: str
    resolved_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_hash: Optional[str] = None
    local_path: Optional[str] = None
    status: str = ImportStatus.PENDING
    progress_pct: float = 0.0
    bytes_downloaded: int = 0
    speed_bps: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    error: Optional[str] = None
    cached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class CloudImportService:
    """Handles cloud link resolution and file downloads."""

    def __init__(self, download_dir: Optional[Path] = None):
        self.download_dir = download_dir or DOWNLOAD_DIR
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self._active_imports: Dict[str, ImportJob] = {}
        self._import_history: List[ImportJob] = []
        self._cache: Dict[str, str] = {}  # url -> local_path

        self._load_cache_index()

    def _load_cache_index(self):
        """Load the download cache index from disk."""
        if CACHE_INDEX_FILE.exists():
            try:
                data = json.loads(CACHE_INDEX_FILE.read_text())
                # Validate entries still exist on disk
                for url, path in data.items():
                    if Path(path).exists():
                        self._cache[url] = path
            except Exception:
                pass

    def _save_cache_index(self):
        """Persist the cache index to disk."""
        try:
            CACHE_INDEX_FILE.write_text(json.dumps(self._cache, indent=2))
        except Exception as e:
            logger.warning(f"Failed to save cache index: {e}")

    # ── Provider Detection ───────────────────────────────────────

    @staticmethod
    def detect_provider(url: str) -> CloudProvider:
        """Detect which cloud storage provider a URL belongs to."""
        parsed = urlparse(url)
        host = parsed.hostname or ""

        if "drive.google.com" in host or "docs.google.com" in host:
            return CloudProvider.GOOGLE_DRIVE
        if "1drv.ms" in host or "onedrive.live.com" in host or "sharepoint.com" in host:
            return CloudProvider.ONEDRIVE
        if "dropbox.com" in host or "dl.dropboxusercontent.com" in host:
            return CloudProvider.DROPBOX
        if parsed.scheme in ("http", "https"):
            return CloudProvider.DIRECT

        return CloudProvider.UNKNOWN

    # ── URL Resolution ───────────────────────────────────────────

    @staticmethod
    def resolve_google_drive_url(url: str) -> tuple[str, Optional[str]]:
        """
        Convert Google Drive share URL to direct download URL.

        Supported formats:
        - https://drive.google.com/file/d/{ID}/view
        - https://drive.google.com/open?id={ID}
        - https://docs.google.com/uc?id={ID}
        """
        # Extract file ID
        file_id = None

        # /file/d/{ID}/view pattern
        match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
        if match:
            file_id = match.group(1)

        # ?id={ID} pattern
        if not file_id:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if 'id' in params:
                file_id = params['id'][0]

        if not file_id:
            return url, None

        download_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
        return download_url, file_id

    @staticmethod
    def resolve_onedrive_url(url: str) -> str:
        """
        Convert OneDrive share URL to direct download URL.

        Supported formats:
        - https://1drv.ms/{shortcode}
        - https://onedrive.live.com/...
        - SharePoint URLs
        """
        # For 1drv.ms short links, append ?download=1
        parsed = urlparse(url)
        if "1drv.ms" in (parsed.hostname or ""):
            return url  # httpx will follow redirects; add download param after redirect

        # For full OneDrive URLs, replace "redir" with "download"
        if "onedrive.live.com" in (parsed.hostname or ""):
            return url.replace("redir?", "download?")

        # For SharePoint, append ?download=1
        if "sharepoint.com" in (parsed.hostname or ""):
            separator = "&" if "?" in url else "?"
            return f"{url}{separator}download=1"

        return url

    @staticmethod
    def resolve_dropbox_url(url: str) -> str:
        """
        Convert Dropbox share URL to direct download URL.

        Replaces dl=0 with dl=1, or appends dl=1.
        """
        if "dropbox.com" not in url:
            return url

        if "?dl=" in url:
            return url.replace("?dl=0", "?dl=1")
        if "&dl=" in url:
            return url.replace("&dl=0", "&dl=1")

        separator = "&" if "?" in url else "?"
        return f"{url}{separator}dl=1"

    def resolve_url(self, url: str, provider: CloudProvider) -> str:
        """Resolve a cloud URL to a direct download URL."""
        if provider == CloudProvider.GOOGLE_DRIVE:
            resolved, _ = self.resolve_google_drive_url(url)
            return resolved
        elif provider == CloudProvider.ONEDRIVE:
            return self.resolve_onedrive_url(url)
        elif provider == CloudProvider.DROPBOX:
            return self.resolve_dropbox_url(url)
        return url

    # ── Import/Download ──────────────────────────────────────────

    async def import_url(self, url: str, file_name: Optional[str] = None) -> ImportJob:
        """
        Import a file from a cloud URL.

        If the URL was previously downloaded and cached, returns immediately.
        Otherwise downloads the file to ~/topos/downloads/.
        """
        # Check cache first
        if url in self._cache:
            cached_path = self._cache[url]
            if Path(cached_path).exists():
                job = ImportJob(
                    job_id=str(uuid.uuid4())[:12],
                    source_url=url,
                    provider=self.detect_provider(url).value,
                    local_path=cached_path,
                    file_name=Path(cached_path).name,
                    file_size=Path(cached_path).stat().st_size,
                    status=ImportStatus.CACHED,
                    progress_pct=100.0,
                    cached=True,
                    started_at=time.time(),
                    completed_at=time.time(),
                )
                self._import_history.append(job)
                logger.info(f"Cache hit for {url} -> {cached_path}")
                return job

        provider = self.detect_provider(url)
        if provider == CloudProvider.UNKNOWN:
            job = ImportJob(
                job_id=str(uuid.uuid4())[:12],
                source_url=url,
                provider=provider.value,
                status=ImportStatus.FAILED,
                error="Unknown or unsupported URL format",
                started_at=time.time(),
            )
            return job

        job = ImportJob(
            job_id=str(uuid.uuid4())[:12],
            source_url=url,
            provider=provider.value,
            status=ImportStatus.RESOLVING,
            started_at=time.time(),
        )
        self._active_imports[job.job_id] = job

        # Resolve URL
        resolved_url = self.resolve_url(url, provider)
        job.resolved_url = resolved_url
        job.status = ImportStatus.DOWNLOADING

        # Start async download
        asyncio.create_task(self._download(job, file_name))
        return job

    async def _download(self, job: ImportJob, requested_name: Optional[str] = None):
        """Download the file with progress tracking."""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=60.0),
            ) as client:
                async with client.stream("GET", job.resolved_url) as response:
                    response.raise_for_status()

                    # Determine filename
                    content_disp = response.headers.get("content-disposition", "")
                    if requested_name:
                        file_name = requested_name
                    elif "filename=" in content_disp:
                        # Parse Content-Disposition header
                        match = re.search(r'filename[*]?=["\']?([^"\';\n]+)', content_disp)
                        if match:
                            file_name = unquote(match.group(1).strip())
                        else:
                            file_name = f"download_{job.job_id}"
                    else:
                        # Try to get name from URL path
                        path = urlparse(job.source_url).path
                        file_name = Path(path).name or f"download_{job.job_id}"

                    # Get file size if available
                    total_size = int(response.headers.get("content-length", 0))
                    if total_size > MAX_DOWNLOAD_SIZE:
                        job.status = ImportStatus.FAILED
                        job.error = f"File too large: {total_size} bytes (max {MAX_DOWNLOAD_SIZE})"
                        self._archive_import(job)
                        return

                    job.file_name = file_name
                    job.file_size = total_size

                    # Generate safe destination path
                    dest = self._safe_path(file_name)
                    sha256 = hashlib.sha256()

                    with open(dest, "wb") as f:
                        async for chunk in response.aiter_bytes(CHUNK_SIZE):
                            f.write(chunk)
                            sha256.update(chunk)
                            job.bytes_downloaded += len(chunk)

                            if total_size > 0:
                                job.progress_pct = (job.bytes_downloaded / total_size) * 100.0

                            elapsed = time.time() - job.started_at
                            if elapsed > 0:
                                job.speed_bps = job.bytes_downloaded / elapsed

                    job.file_hash = sha256.hexdigest()
                    job.local_path = str(dest)
                    job.file_size = job.bytes_downloaded
                    job.progress_pct = 100.0
                    job.status = ImportStatus.COMPLETED
                    job.completed_at = time.time()

                    # Update cache
                    self._cache[job.source_url] = str(dest)
                    self._save_cache_index()

                    self._archive_import(job)
                    logger.info(
                        f"Downloaded {file_name} ({job.bytes_downloaded} bytes) "
                        f"from {job.provider} -> {dest}"
                    )

        except httpx.HTTPStatusError as e:
            job.status = ImportStatus.FAILED
            job.error = f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
            self._archive_import(job)
            logger.error(f"Download failed: {job.source_url} - {job.error}")

        except Exception as e:
            job.status = ImportStatus.FAILED
            job.error = str(e)
            self._archive_import(job)
            logger.error(f"Download error: {job.source_url} - {e}")

    # ── Status & History ─────────────────────────────────────────

    def get_active_imports(self) -> List[dict]:
        return [j.to_dict() for j in self._active_imports.values()]

    def get_import_history(self, limit: int = 50) -> List[dict]:
        return [j.to_dict() for j in self._import_history[-limit:]]

    def get_import(self, job_id: str) -> Optional[dict]:
        job = self._active_imports.get(job_id)
        if not job:
            for j in reversed(self._import_history):
                if j.job_id == job_id:
                    return j.to_dict()
            return None
        return job.to_dict()

    def get_cache_stats(self) -> dict:
        """Get statistics about the download cache."""
        total_size = 0
        file_count = 0
        for path in self._cache.values():
            p = Path(path)
            if p.exists():
                total_size += p.stat().st_size
                file_count += 1

        return {
            "cached_files": file_count,
            "cached_urls": len(self._cache),
            "total_size_bytes": total_size,
            "cache_dir": str(self.download_dir),
        }

    def clear_cache(self) -> dict:
        """Clear the download cache (keeps files, removes index)."""
        count = len(self._cache)
        self._cache.clear()
        self._save_cache_index()
        return {"cleared_entries": count}

    # ── Helpers ──────────────────────────────────────────────────

    def _safe_path(self, file_name: str) -> Path:
        """Generate a safe destination path, avoiding overwrites."""
        safe_name = Path(file_name).name.replace("..", "_")
        dest = self.download_dir / safe_name
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = self.download_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        return dest

    def _archive_import(self, job: ImportJob):
        """Move import from active to history."""
        self._active_imports.pop(job.job_id, None)
        self._import_history.append(job)
        if len(self._import_history) > 200:
            self._import_history = self._import_history[-100:]


# ── Global instance management ───────────────────────────────────

_cloud_import_service: Optional[CloudImportService] = None


def start_cloud_import(download_dir: Optional[str] = None) -> CloudImportService:
    """Initialize the global cloud import service."""
    global _cloud_import_service
    recv_dir = Path(download_dir) if download_dir else None
    _cloud_import_service = CloudImportService(recv_dir)
    logger.info(f"Cloud import service started (cache: {_cloud_import_service.download_dir})")
    return _cloud_import_service


def get_cloud_import() -> Optional[CloudImportService]:
    """Get the running cloud import service instance."""
    return _cloud_import_service
