"""Tests for kernel log scanning: patterns, latching, and file offset tracking."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from homelab_storage_monitor.checks.journal import (
    ERROR_PATTERNS,
    KV_RECENT_ERRORS,
    JournalCheck,
)
from homelab_storage_monitor.models import Status
from homelab_storage_monitor.timeutil import utcnow


@pytest.fixture
def check(config, db):
    return JournalCheck(config, db)


class TestPatterns:
    def matches(self, line: str) -> list[str]:
        return [name for name, (pattern, _, _) in ERROR_PATTERNS.items() if pattern.search(line)]

    def test_real_ext4_error_matches(self):
        line = "EXT4-fs error (device dm-0): ext4_find_entry:1455: inode #1234: comm find: reading directory lblock 0"
        assert "ext4_error" in self.matches(line)

    def test_benign_mount_options_do_not_match(self):
        line = "EXT4-fs (sda1): mounted filesystem with ordered data mode. Opts: errors=remount-ro"
        assert self.matches(line) == []

    def test_io_error_matches(self):
        line = "blk_update_request: I/O error, dev sda, sector 12345"
        found = self.matches(line)
        assert "io_error" in found and "blk_update" in found

    def test_ata_reset_matches(self):
        assert "ata_reset" in self.matches("ata3.00: hard resetting link")

    def test_unrelated_reset_does_not_match(self):
        assert "ata_reset" not in self.matches("Corrupted data buffer; resetting controller")

    def test_sense_key_medium_error_matches(self):
        line = "sd 0:0:0:0: [sda] tag#0 Sense Key : Medium Error [current]"
        found = self.matches(line)
        assert "sense_error" in found

    def test_benign_sense_key_does_not_match(self):
        line = "sd 0:0:0:0: [sda] tag#0 Sense Key : Illegal Request [current]"
        assert "sense_error" not in self.matches(line)


class TestLatching:
    def test_new_crit_error_sets_crit(self, check):
        result = check._analyze_logs(["blk_update_request: I/O error, dev sda, sector 1"])
        assert result.status == Status.CRIT

    def test_error_stays_latched_with_quiet_logs(self, check):
        check._analyze_logs(["blk_update_request: I/O error, dev sda, sector 1"])
        # Next run sees no new lines but the error is still within the window
        result = check._analyze_logs([])
        assert result.status == Status.CRIT
        assert "in last 24h" in result.summary

    def test_latch_expires_after_window(self, check, db, config):
        check._analyze_logs(["blk_update_request: I/O error, dev sda, sector 1"])

        # Age the latched entry beyond the window
        entries = json.loads(db.kv_get(KV_RECENT_ERRORS))
        old_ts = (utcnow() - timedelta(hours=config.journal.latch_hours + 1)).isoformat()
        for e in entries:
            e["ts"] = old_ts
        db.kv_set(KV_RECENT_ERRORS, json.dumps(entries))

        result = check._analyze_logs([])
        assert result.status == Status.OK

    def test_warn_pattern_sets_warn(self, check):
        result = check._analyze_logs(["ata3.00: hard resetting link"])
        assert result.status == Status.WARN

    def test_clean_logs_are_ok(self, check):
        result = check._analyze_logs(["usb 1-1: new high-speed USB device"])
        assert result.status == Status.OK


class TestFileOffsetTracking:
    def test_only_new_lines_scanned_on_second_run(self, check, config, tmp_path):
        log = tmp_path / "kern.log"
        log.write_text("blk_update_request: I/O error, dev sda, sector 1\n")
        config.journal.fallback_log_paths = [str(log)]

        lines = check._get_file_logs()
        assert len(lines) == 1

        # Nothing new appended: nothing scanned
        assert check._get_file_logs() == []

        # Appended line: only the new one is returned
        with open(log, "a") as f:
            f.write("second line\n")
        lines = check._get_file_logs()
        assert lines == ["second line"]

    def test_rotation_resets_offset(self, check, config, tmp_path):
        log = tmp_path / "kern.log"
        log.write_text("a long first line before rotation happens\n")
        config.journal.fallback_log_paths = [str(log)]
        check._get_file_logs()

        # Simulate rotation: new (shorter) file at the same path
        log.unlink()
        log.write_text("rotated\n")
        lines = check._get_file_logs()
        assert lines == ["rotated"]
