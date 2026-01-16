"""Filesystem capacity check."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from homelab_storage_monitor.checks.base import BaseCheck
from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import CheckResult, Metric, Status

logger = logging.getLogger(__name__)


class FilesystemCheck(BaseCheck):
    """Check filesystem capacity for configured mountpoints."""

    name = "filesystem"

    def __init__(self, config: Config, db: Database):
        super().__init__(config, db)
        self._metrics: list[Metric] = []

    def run(self) -> list[CheckResult]:
        """Check all configured mountpoints."""
        self._metrics = []

        if not self.config.filesystem.enabled:
            return []

        results: list[CheckResult] = []

        for mp_config in self.config.filesystem.mountpoints:
            result = self._check_mountpoint(
                path=mp_config.path,
                warn_pct=mp_config.warn_pct,
                crit_pct=mp_config.crit_pct,
            )
            results.append(result)

        return results

    def get_metrics(self) -> list[Metric]:
        """Return metrics from last check."""
        return self._metrics

    def _check_mountpoint(
        self,
        path: str,
        warn_pct: float,
        crit_pct: float,
    ) -> CheckResult:
        """Check a single mountpoint."""
        mount_path = Path(path)

        if not mount_path.exists():
            return CheckResult(
                name=self.name,
                status=Status.UNKNOWN,
                summary=f"Mount path not found: {path}",
                details={"path": path, "error": "not found"},
                identifier=path,
            )

        if not mount_path.is_dir():
            return CheckResult(
                name=self.name,
                status=Status.UNKNOWN,
                summary=f"Path is not a directory: {path}",
                details={"path": path, "error": "not a directory"},
                identifier=path,
            )

        try:
            stat = os.statvfs(path)
        except OSError as e:
            logger.warning(f"Failed to statvfs {path}: {e}")
            return CheckResult(
                name=self.name,
                status=Status.UNKNOWN,
                summary=f"Failed to check {path}: {e}",
                details={"path": path, "error": str(e)},
                identifier=path,
            )

        # Calculate usage
        total_bytes = stat.f_blocks * stat.f_frsize
        free_bytes = stat.f_bavail * stat.f_frsize  # Available to non-root
        used_bytes = total_bytes - (stat.f_bfree * stat.f_frsize)

        if total_bytes == 0:
            usage_pct = 0.0
        else:
            usage_pct = (used_bytes / total_bytes) * 100

        # Record metrics
        labels = {"mount": path}
        self._metrics.extend([
            Metric(name="fs_usage_pct", value_num=usage_pct, labels=labels),
            Metric(name="fs_free_bytes", value_num=float(free_bytes), labels=labels),
            Metric(name="fs_total_bytes", value_num=float(total_bytes), labels=labels),
        ])

        details = {
            "path": path,
            "total_bytes": total_bytes,
            "free_bytes": free_bytes,
            "used_bytes": used_bytes,
            "usage_pct": round(usage_pct, 2),
            "warn_pct": warn_pct,
            "crit_pct": crit_pct,
        }

        # Format sizes for human-readable summary
        def format_size(b: int) -> str:
            for unit in ["B", "KB", "MB", "GB", "TB"]:
                if abs(b) < 1024:
                    return f"{b:.1f}{unit}"
                b /= 1024  # type: ignore
            return f"{b:.1f}PB"

        free_human = format_size(free_bytes)
        total_human = format_size(total_bytes)

        # Determine status
        if usage_pct >= crit_pct:
            return CheckResult(
                name=self.name,
                status=Status.CRIT,
                summary=f"{path}: {usage_pct:.1f}% used ({free_human} free of {total_human})",
                details=details,
                identifier=path,
            )

        if usage_pct >= warn_pct:
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                summary=f"{path}: {usage_pct:.1f}% used ({free_human} free of {total_human})",
                details=details,
                identifier=path,
            )

        return CheckResult(
            name=self.name,
            status=Status.OK,
            summary=f"{path}: {usage_pct:.1f}% used ({free_human} free of {total_human})",
            details=details,
            identifier=path,
        )
