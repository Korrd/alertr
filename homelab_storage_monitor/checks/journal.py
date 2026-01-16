"""Kernel/journal log scanning for I/O and filesystem errors."""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homelab_storage_monitor.checks.base import BaseCheck
from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import CheckResult, Metric, Status

logger = logging.getLogger(__name__)

# Patterns to search for (case-insensitive)
ERROR_PATTERNS = {
    # Critical patterns - filesystem/data corruption risk
    "ext4_error": (
        re.compile(r"EXT4-fs.*error", re.IGNORECASE),
        Status.CRIT,
        "ext4 filesystem error",
    ),
    "jbd2_error": (
        re.compile(r"JBD2.*error", re.IGNORECASE),
        Status.CRIT,
        "Journal (JBD2) error",
    ),
    "io_error": (
        re.compile(r"I/O error", re.IGNORECASE),
        Status.CRIT,
        "I/O error",
    ),
    "blk_update": (
        re.compile(r"blk_update_request.*error", re.IGNORECASE),
        Status.CRIT,
        "Block device error",
    ),
    "buffer_io_error": (
        re.compile(r"Buffer I/O error", re.IGNORECASE),
        Status.CRIT,
        "Buffer I/O error",
    ),
    "xfs_error": (
        re.compile(r"XFS.*error", re.IGNORECASE),
        Status.CRIT,
        "XFS filesystem error",
    ),
    "btrfs_error": (
        re.compile(r"BTRFS.*error", re.IGNORECASE),
        Status.CRIT,
        "BTRFS filesystem error",
    ),
    # Warning patterns - potential issues
    "ata_reset": (
        re.compile(r"ata.*reset", re.IGNORECASE),
        Status.WARN,
        "ATA bus reset",
    ),
    "link_slow": (
        re.compile(r"link is slow to respond", re.IGNORECASE),
        Status.WARN,
        "Slow SATA link",
    ),
    "sata_down": (
        re.compile(r"SATA link down", re.IGNORECASE),
        Status.WARN,
        "SATA link down",
    ),
    "medium_error": (
        re.compile(r"medium error", re.IGNORECASE),
        Status.WARN,
        "Medium error",
    ),
    "sense_error": (
        re.compile(r"sense.*error", re.IGNORECASE),
        Status.WARN,
        "SCSI sense error",
    ),
}


class JournalCheck(BaseCheck):
    """Scan kernel logs for I/O and filesystem errors."""

    name = "journal"

    def __init__(self, config: Config, db: Database):
        super().__init__(config, db)
        self._metrics: list[Metric] = []
        self._last_check_ts: datetime | None = None

    def run(self) -> list[CheckResult]:
        """Scan logs for errors since last check."""
        self._metrics = []

        if not self.config.journal.enabled:
            return []

        # Get logs since last check (default: last hour for first run)
        since_ts = self._last_check_ts or (datetime.now() - timedelta(hours=1))
        self._last_check_ts = datetime.now()

        try:
            if self.config.journal.use_journald:
                log_lines = self._get_journald_logs(since_ts)
            else:
                log_lines = self._get_file_logs(since_ts)
        except Exception as e:
            logger.warning(f"Failed to read logs: {e}")
            # Try fallback to file logs if journald failed
            if self.config.journal.use_journald:
                try:
                    log_lines = self._get_file_logs(since_ts)
                except Exception as e2:
                    return [
                        CheckResult(
                            name=self.name,
                            status=Status.UNKNOWN,
                            summary=f"Failed to read logs: {e2}",
                            details={"error": str(e2), "journald_error": str(e)},
                        )
                    ]
            else:
                return [
                    CheckResult(
                        name=self.name,
                        status=Status.UNKNOWN,
                        summary=f"Failed to read logs: {e}",
                        details={"error": str(e)},
                    )
                ]

        return [self._analyze_logs(log_lines)]

    def get_metrics(self) -> list[Metric]:
        """Return metrics from last check."""
        return self._metrics

    def _get_journald_logs(self, since: datetime) -> list[str]:
        """Get kernel logs from journald."""
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")

        cmd = [
            "journalctl",
            "-k",  # kernel messages only
            "--since", since_str,
            "--no-pager",
            "-q",  # quiet (no metadata)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0 and result.returncode != 1:
            # returncode 1 can mean "no entries" which is fine
            raise RuntimeError(f"journalctl failed: {result.stderr}")

        return result.stdout.strip().split("\n") if result.stdout.strip() else []

    def _get_file_logs(self, since: datetime) -> list[str]:
        """Get kernel logs from log files (fallback)."""
        lines: list[str] = []

        for log_path_str in self.config.journal.fallback_log_paths:
            log_path = Path(log_path_str)
            if not log_path.exists():
                continue

            try:
                with open(log_path) as f:
                    for line in f:
                        # Try to parse timestamp and filter
                        # This is best-effort; log formats vary
                        lines.append(line.strip())
            except (OSError, PermissionError) as e:
                logger.debug(f"Could not read {log_path}: {e}")
                continue

        return lines

    def _analyze_logs(self, log_lines: list[str]) -> CheckResult:
        """Analyze log lines for error patterns."""
        matches: dict[str, list[str]] = {}
        error_counts: dict[str, int] = {}
        worst_status = Status.OK

        for line in log_lines:
            if not line:
                continue

            for pattern_name, (pattern, severity, _desc) in ERROR_PATTERNS.items():
                if pattern.search(line):
                    if pattern_name not in matches:
                        matches[pattern_name] = []
                    matches[pattern_name].append(line)
                    error_counts[pattern_name] = error_counts.get(pattern_name, 0) + 1

                    if severity.severity > worst_status.severity:
                        worst_status = severity

        # Record metrics
        total_io_errors = sum(
            count for name, count in error_counts.items()
            if name in ("io_error", "blk_update", "buffer_io_error")
        )
        total_ext4_errors = error_counts.get("ext4_error", 0) + error_counts.get("jbd2_error", 0)

        self._metrics.extend([
            Metric(name="kernel_io_error_count", value_num=float(total_io_errors)),
            Metric(name="ext4_error_count", value_num=float(total_ext4_errors)),
        ])

        details: dict[str, Any] = {
            "lines_scanned": len(log_lines),
            "error_counts": error_counts,
            "sample_matches": {
                name: lines[:3]  # Keep first 3 samples
                for name, lines in matches.items()
            },
        }

        if not matches:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                summary=f"No errors in kernel logs ({len(log_lines)} lines scanned)",
                details=details,
            )

        # Build summary
        issue_parts = []
        for pattern_name, count in sorted(
            error_counts.items(),
            key=lambda x: ERROR_PATTERNS[x[0]][1].severity,
            reverse=True,
        ):
            _, severity, desc = ERROR_PATTERNS[pattern_name]
            issue_parts.append(f"{desc}: {count}")

        summary = "; ".join(issue_parts[:3])
        if len(issue_parts) > 3:
            summary += f" (+{len(issue_parts) - 3} more types)"

        return CheckResult(
            name=self.name,
            status=worst_status,
            summary=summary,
            details=details,
        )
