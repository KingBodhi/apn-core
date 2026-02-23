"""
APN Core Resource Accounting - Tracks resource contributions

Mirrors the ResourceContribution and ResourceTracker from economics.rs
in the pcg-cc-mcp Rust codebase. Provides local resource tracking
for reward calculation.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from core.logging_config import get_logger

logger = get_logger("resource_accounting")


@dataclass
class ResourceContribution:
    """Snapshot of resource contributions. Matches economics.rs struct."""
    cpu_units: int = 0
    gpu_units: int = 0
    bandwidth_bytes: int = 0
    storage_bytes: int = 0
    relay_messages: int = 0
    uptime_seconds: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    heartbeat_count: int = 0

    def contribution_score(self) -> int:
        """Weighted scoring ported from Rust contribution_score()."""
        cpu_score = self.cpu_units // 1_000_000
        gpu_score = (self.gpu_units // 1_000_000) * 5
        bandwidth_score = self.bandwidth_bytes // (1024 * 1024 * 1024)
        storage_score = self.storage_bytes // (10 * 1024 * 1024 * 1024)
        relay_score = self.relay_messages // 100
        uptime_score = (self.uptime_seconds // 3600) * 10
        task_score = self.tasks_completed * 100
        return (cpu_score + gpu_score + bandwidth_score + storage_score +
                relay_score + uptime_score + task_score)

    def merge(self, other: "ResourceContribution"):
        """Merge another contribution into this one."""
        self.cpu_units += other.cpu_units
        self.gpu_units += other.gpu_units
        self.bandwidth_bytes += other.bandwidth_bytes
        self.storage_bytes = other.storage_bytes  # current, not cumulative
        self.relay_messages += other.relay_messages
        self.uptime_seconds += other.uptime_seconds
        self.tasks_completed += other.tasks_completed
        self.tasks_failed += other.tasks_failed
        self.heartbeat_count += other.heartbeat_count


class ResourceAccountant:
    """Real-time resource tracker matching Rust ResourceTracker."""

    def __init__(self):
        self._start_time = time.monotonic()
        self._last_snapshot = time.monotonic()
        self._current = ResourceContribution()
        self._total = ResourceContribution()

    def record_heartbeat(self):
        """Record that a heartbeat was sent."""
        self._current.heartbeat_count += 1

    def record_relay(self):
        """Record a relay message forwarded."""
        self._current.relay_messages += 1

    def record_task(self, success: bool):
        """Record a task completion."""
        if success:
            self._current.tasks_completed += 1
        else:
            self._current.tasks_failed += 1

    def record_bandwidth(self, bytes_count: int):
        """Record bandwidth used."""
        self._current.bandwidth_bytes += bytes_count

    def record_cpu(self, units: int):
        """Record CPU compute units."""
        self._current.cpu_units += units

    def record_gpu(self, units: int):
        """Record GPU compute units."""
        self._current.gpu_units += units

    def set_storage(self, bytes_count: int):
        """Set current storage contribution."""
        self._current.storage_bytes = bytes_count

    def snapshot(self) -> ResourceContribution:
        """Take a snapshot and reset the current period."""
        elapsed = int(time.monotonic() - self._last_snapshot)
        self._current.uptime_seconds = elapsed

        snap = ResourceContribution(
            cpu_units=self._current.cpu_units,
            gpu_units=self._current.gpu_units,
            bandwidth_bytes=self._current.bandwidth_bytes,
            storage_bytes=self._current.storage_bytes,
            relay_messages=self._current.relay_messages,
            uptime_seconds=self._current.uptime_seconds,
            tasks_completed=self._current.tasks_completed,
            tasks_failed=self._current.tasks_failed,
            heartbeat_count=self._current.heartbeat_count,
        )
        self._total.merge(snap)

        self._current = ResourceContribution()
        self._last_snapshot = time.monotonic()
        return snap

    def get_current_snapshot(self) -> ResourceContribution:
        """Get current contribution without resetting."""
        elapsed = int(time.monotonic() - self._last_snapshot)
        return ResourceContribution(
            cpu_units=self._current.cpu_units,
            gpu_units=self._current.gpu_units,
            bandwidth_bytes=self._current.bandwidth_bytes,
            storage_bytes=self._current.storage_bytes,
            relay_messages=self._current.relay_messages,
            uptime_seconds=elapsed,
            tasks_completed=self._current.tasks_completed,
            tasks_failed=self._current.tasks_failed,
            heartbeat_count=self._current.heartbeat_count,
        )

    @property
    def total(self) -> ResourceContribution:
        return self._total

    @property
    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self._start_time)


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------

_resource_accountant: Optional[ResourceAccountant] = None


def start_resource_accounting() -> ResourceAccountant:
    """Start the global resource accountant."""
    global _resource_accountant
    if _resource_accountant is None:
        _resource_accountant = ResourceAccountant()
        logger.info("Resource accounting started")
    return _resource_accountant


def get_resource_accounting() -> Optional[ResourceAccountant]:
    """Get the global resource accountant."""
    return _resource_accountant
