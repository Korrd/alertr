"""Kernel/journal log scanning for I/O and filesystem errors."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

from homelab_storage_monitor.checks.base import BaseCheck
from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import CheckResult, Metric, Status
from homelab_storage_monitor.timeutil import parse_ts, utcnow

logger = logging.getLogger(__name__)

# kv_store keys for scan position and latched errors
KV_CURSOR = "journal:cursor"
KV_RECENT_ERRORS = "journal:recent_errors"
KV_FILE_STATE_PREFIX = "journal:file:"

# Cap on latched error entries kept in the kv store
MAX_RETAINED_ERRORS = 500

# Patterns to search for (case-insensitive). Anchored to real kernel message
# formats to avoid matching benign lines (e.g. "errors=remount-ro" mount opts).
ERROR_PATTERNS = {
    # Critical patterns - filesystem/data corruption risk
    "ext4_error": (
        re.compile(r"EXT4-fs error", re.IGNORECASE),
        Status.CRIT,
        "ext4 filesystem error",
    ),
    "jbd2_error": (
        re.compile(r"JBD2.*(?:error|abort)", re.IGNORECASE),
        Status.CRIT,
        "Journal (JBD2) error",
    ),
    "io_error": (
        re.compile(r"\bI/O error\b", re.IGNORECASE),
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
        re.compile(r"\bata\d+(?:\.\d+)?: .*reset", re.IGNORECASE),
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
        re.compile(
            r"sense key\s*:\s*(?:medium error|hardware error|aborted command)",
            re.IGNORECASE,
        ),
        Status.WARN,
        "SCSI sense error",
    ),
}


class JournalCheck(BaseCheck):
    """Scan kernel logs for I/O and filesystem errors.

    Scan position persists in the database (a journald cursor, or per-file
    inode+offset for the fallback), so restarts neither rescan old lines nor
    miss errors logged while the collector was down. Matched errors are
    latched for latch_hours: the check stays non-OK until the log has been
    quiet for that long, so a single error burst can't flap OK -> CRIT -> OK
    within two runs.
    """

    name = "journal"

    def __init__(self, config: Config, db: Database):
        super().__init__(config, db)
        self._metrics: list[Metric] = []

    def run(self) -> list[CheckResult]:
        """Scan logs for errors since the last scan position."""
        self._metrics = []

        if not self.config.journal.enabled:
            return []

        try:
            if self.config.journal.use_journald:
                log_lines = self._get_journald_logs()
            else:
                log_lines = self._get_file_logs()
        except Exception as e:
            logger.warning(f"Failed to read logs: {e}")
            # Try fallback to file logs if journald failed
            if self.config.journal.use_journald:
                try:
                    log_lines = self._get_file_logs()
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

    def _get_journald_logs(self) -> list[str]:
        """Get new kernel log lines from journald, tracking a cursor."""
        cursor = self.db.kv_get(KV_CURSOR)
        lines, new_cursor = self._run_journalctl(cursor)

        if lines is None:
            # The stored cursor may have been invalidated by journal rotation
            # or vacuuming; drop it and retry from the time-based default
            if cursor:
                logger.warning("journalctl rejected stored cursor; rescanning last hour")
                self.db.kv_delete(KV_CURSOR)
                lines, new_cursor = self._run_journalctl(None)
            if lines is None:
                raise RuntimeError("journalctl failed")

        if new_cursor:
            self.db.kv_set(KV_CURSOR, new_cursor)

        return lines

    def _run_journalctl(self, cursor: str | None) -> tuple[list[str] | None, str | None]:
        """Run journalctl once. Returns (lines, new_cursor); lines=None on failure."""
        cmd = ["journalctl", "-k", "--no-pager", "-q", "--show-cursor"]
        if cursor:
            cmd += ["--after-cursor", cursor]
        else:
            since = (utcnow() - timedelta(hours=1)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            cmd += ["--since", since]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0 and result.returncode != 1:
            # returncode 1 can mean "no entries" which is fine
            logger.debug(f"journalctl failed (rc={result.returncode}): {result.stderr}")
            return None, None

        raw_lines = result.stdout.strip().split("\n") if result.stdout.strip() else []

        new_cursor = None
        lines: list[str] = []
        for line in raw_lines:
            if line.startswith("-- cursor:"):
                new_cursor = line[len("-- cursor:"):].strip()
            else:
                lines.append(line)

        return lines, new_cursor

    def _get_file_logs(self) -> list[str]:
        """Get new kernel log lines from files, tracking inode+offset per file."""
        lines: list[str] = []

        for log_path_str in self.config.journal.fallback_log_paths:
            log_path = Path(log_path_str)
            if not log_path.exists():
                continue

            state_key = f"{KV_FILE_STATE_PREFIX}{log_path_str}"

            try:
                st = log_path.stat()
                offset = 0
                stored = self.db.kv_get(state_key)
                if stored:
                    try:
                        state = json.loads(stored)
                        # Reset on rotation (new inode) or truncation
                        if state.get("inode") == st.st_ino and state.get("offset", 0) <= st.st_size:
                            offset = state["offset"]
                    except (json.JSONDecodeError, TypeError):
                        pass

                with open(log_path, "rb") as f:
                    f.seek(offset)
                    new_data = f.read()
                    new_offset = f.tell()

                lines.extend(
                    line.strip()
                    for line in new_data.decode("utf-8", errors="replace").splitlines()
                )
                self.db.kv_set(
                    state_key,
                    json.dumps({"inode": st.st_ino, "offset": new_offset}),
                )
            except (OSError, PermissionError) as e:
                logger.debug(f"Could not read {log_path}: {e}")
                continue

        return lines

    def _analyze_logs(self, log_lines: list[str]) -> CheckResult:
        """Match new lines against error patterns and merge with latched errors."""
        now = utcnow()
        latch_hours = self.config.journal.latch_hours
        cutoff = now - timedelta(hours=latch_hours)

        # Load errors latched from previous runs, dropping expired ones
        retained: list[dict[str, Any]] = []
        stored = self.db.kv_get(KV_RECENT_ERRORS)
        if stored:
            try:
                retained = [
                    e
                    for e in json.loads(stored)
                    if e.get("pattern") in ERROR_PATTERNS and parse_ts(e["ts"]) >= cutoff
                ]
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                retained = []

        # Match new lines
        new_counts: dict[str, int] = {}
        for line in log_lines:
            if not line:
                continue
            for pattern_name, (pattern, _severity, _desc) in ERROR_PATTERNS.items():
                if pattern.search(line):
                    new_counts[pattern_name] = new_counts.get(pattern_name, 0) + 1
                    retained.append(
                        {"ts": now.isoformat(), "pattern": pattern_name, "line": line}
                    )

        retained = retained[-MAX_RETAINED_ERRORS:]
        self.db.kv_set(KV_RECENT_ERRORS, json.dumps(retained))

        # Metrics count errors newly seen in this run
        total_io_errors = sum(
            count for name, count in new_counts.items()
            if name in ("io_error", "blk_update", "buffer_io_error")
        )
        total_ext4_errors = new_counts.get("ext4_error", 0) + new_counts.get("jbd2_error", 0)

        self._metrics.extend([
            Metric(name="kernel_io_error_count", value_num=float(total_io_errors)),
            Metric(name="ext4_error_count", value_num=float(total_ext4_errors)),
        ])

        # Status reflects everything still latched within the window
        error_counts: dict[str, int] = {}
        sample_matches: dict[str, list[str]] = {}
        worst_status = Status.OK
        for entry in retained:
            pattern_name = entry["pattern"]
            error_counts[pattern_name] = error_counts.get(pattern_name, 0) + 1
            sample_matches.setdefault(pattern_name, [])
            if len(sample_matches[pattern_name]) < 3:
                sample_matches[pattern_name].append(entry["line"])

            severity = ERROR_PATTERNS[pattern_name][1]
            if severity.severity > worst_status.severity:
                worst_status = severity

        details: dict[str, Any] = {
            "lines_scanned": len(log_lines),
            "new_matches": sum(new_counts.values()),
            "latch_hours": latch_hours,
            "error_counts": error_counts,
            "sample_matches": sample_matches,
        }

        if not retained:
            return CheckResult(
                name=self.name,
                status=Status.OK,
                summary=(
                    f"No storage errors in kernel logs for {latch_hours}h"
                    f" ({len(log_lines)} new lines scanned)"
                ),
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
        summary += f" in last {latch_hours}h"

        return CheckResult(
            name=self.name,
            status=worst_status,
            summary=summary,
            details=details,
        )
