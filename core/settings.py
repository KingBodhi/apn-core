"""
APN CORE - Settings Module
Production-ready configuration using pydantic-settings with environment variable support.

Part of APN CORE v1.0.0 - Alpha Protocol Network
"""
import os
from pathlib import Path
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class APNSettings(BaseSettings):
    """
    APN Core server settings with environment variable support.

    All settings can be overridden via environment variables with APN_ prefix.
    Example: APN_PORT=9000 will set port to 9000
    """

    model_config = SettingsConfigDict(
        env_prefix="APN_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server settings
    host: str = Field(default="0.0.0.0", description="Server bind address")
    port: int = Field(default=8000, ge=1, le=65535, description="Server port")
    log_level: str = Field(default="INFO", description="Logging level")
    debug: bool = Field(default=False, description="Debug mode")

    # Security settings
    cors_origins: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:8000"],
        description="Allowed CORS origins"
    )
    api_key: Optional[str] = Field(default=None, description="API authentication key")
    rate_limit: int = Field(default=60, ge=1, description="Rate limit (requests/minute)")

    # Mesh networking
    nats_relay: str = Field(
        default="nats://nonlocal.info:4222",
        description="NATS relay server URL"
    )
    known_peers: List[str] = Field(
        default=[
            "https://dashboard.powerclubglobal.com",
            "https://pythia.nonlocal.info",
        ],
        description="Known peer node URLs"
    )

    # Backend services
    nora_url: str = Field(
        default="http://127.0.0.1:3003",
        description="Nora backend URL"
    )

    # Database
    database_path: str = Field(
        default="apn_core.db",
        description="SQLite database path (relative to config dir)"
    )

    # Identity
    identity_file: str = Field(
        default="node_identity.json",
        description="Node identity file (relative to config dir)"
    )

    # Contribution settings
    contribution_enabled: bool = Field(default=False)
    contribution_relay: bool = Field(default=False)
    contribution_compute: bool = Field(default=False)
    contribution_storage: bool = Field(default=False)
    storage_gb_allocated: int = Field(default=10, ge=0)
    compute_cores_allocated: int = Field(default=1, ge=0)
    bandwidth_limit_mbps: int = Field(default=100, ge=0)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse comma-separated string to list"""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("known_peers", mode="before")
    @classmethod
    def parse_known_peers(cls, v):
        """Parse comma-separated string to list"""
        if isinstance(v, str):
            return [peer.strip() for peer in v.split(",") if peer.strip()]
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v):
        """Validate log level"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v.upper()

    @property
    def config_dir(self) -> Path:
        """Get the APN configuration directory"""
        return Path.home() / ".apn"

    @property
    def full_database_path(self) -> Path:
        """Get full path to SQLite database"""
        return self.config_dir / self.database_path

    @property
    def full_identity_path(self) -> Path:
        """Get full path to identity file"""
        return self.config_dir / self.identity_file

    def ensure_config_dir(self) -> None:
        """Ensure configuration directory exists with proper permissions"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # Set secure permissions (owner read/write/execute only)
        try:
            self.config_dir.chmod(0o700)
        except Exception:
            # Ignore permission errors on some filesystems
            pass

    def is_origin_allowed(self, origin: str) -> bool:
        """Check if an origin is allowed for CORS"""
        if "*" in self.cors_origins:
            return True
        return origin in self.cors_origins

    def get_contribution_settings(self) -> dict:
        """Get contribution settings as dictionary"""
        return {
            "enabled": self.contribution_enabled,
            "relay": self.contribution_relay,
            "compute": self.contribution_compute,
            "storage": self.contribution_storage,
            "storage_gb_allocated": self.storage_gb_allocated,
            "compute_cores_allocated": self.compute_cores_allocated,
            "bandwidth_limit_mbps": self.bandwidth_limit_mbps,
        }


@lru_cache()
def get_settings() -> APNSettings:
    """
    Get cached settings instance.

    Settings are loaded once and cached for performance.
    To reload settings, call get_settings.cache_clear() first.
    """
    return APNSettings()


def reload_settings() -> APNSettings:
    """Force reload settings from environment"""
    get_settings.cache_clear()
    return get_settings()
