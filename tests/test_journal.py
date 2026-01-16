"""Tests for journal/log scanning."""

import pytest

from homelab_storage_monitor.models import Status


# Sample log lines for testing
LOG_LINES_HEALTHY = [
    "Jan 15 10:00:00 server kernel: usb 2-1: New USB device found",
    "Jan 15 10:00:01 server kernel: usb 2-1: Product: USB Mouse",
]

LOG_LINES_IO_ERROR = [
    "Jan 15 10:00:00 server kernel: blk_update_request: I/O error, dev sda, sector 12345",
    "Jan 15 10:00:01 server kernel: Buffer I/O error on dev sda1, logical block 100",
]

LOG_LINES_EXT4_ERROR = [
    "Jan 15 10:00:00 server kernel: EXT4-fs error (device sda1): ext4_lookup:1234",
    "Jan 15 10:00:01 server kernel: JBD2: I/O error detected when updating journal",
]

LOG_LINES_SATA_ISSUES = [
    "Jan 15 10:00:00 server kernel: ata1.00: exception Emask 0x0 SAct 0x0 SErr 0x0",
    "Jan 15 10:00:01 server kernel: ata1: link is slow to respond",
    "Jan 15 10:00:02 server kernel: ata1: SATA link down",
]

LOG_LINES_MIXED = [
    "Jan 15 10:00:00 server kernel: usb 2-1: New USB device found",
    "Jan 15 10:00:01 server kernel: I/O error, dev sdb, sector 999",
    "Jan 15 10:00:02 server kernel: ata2: link is slow to respond",
    "Jan 15 10:00:03 server kernel: Normal kernel message",
]


class TestLogPatternMatching:
    """Test log pattern matching."""

    def test_healthy_logs(self):
        """Test that healthy logs don't trigger alerts."""
        import re
        from homelab_storage_monitor.checks.journal import ERROR_PATTERNS

        matches = []
        for line in LOG_LINES_HEALTHY:
            for name, (pattern, severity, desc) in ERROR_PATTERNS.items():
                if pattern.search(line):
                    matches.append(name)

        assert len(matches) == 0

    def test_io_error_detection(self):
        """Test I/O error pattern detection."""
        import re
        from homelab_storage_monitor.checks.journal import ERROR_PATTERNS

        matches = []
        for line in LOG_LINES_IO_ERROR:
            for name, (pattern, severity, desc) in ERROR_PATTERNS.items():
                if pattern.search(line):
                    matches.append((name, severity))

        # Should detect multiple I/O errors
        assert len(matches) >= 2
        # All should be CRIT severity
        assert all(s == Status.CRIT for _, s in matches)

    def test_ext4_error_detection(self):
        """Test ext4 error pattern detection."""
        import re
        from homelab_storage_monitor.checks.journal import ERROR_PATTERNS

        matches = []
        for line in LOG_LINES_EXT4_ERROR:
            for name, (pattern, severity, desc) in ERROR_PATTERNS.items():
                if pattern.search(line):
                    matches.append(name)

        assert "ext4_error" in matches or "jbd2_error" in matches

    def test_sata_issues_detection(self):
        """Test SATA issue pattern detection."""
        import re
        from homelab_storage_monitor.checks.journal import ERROR_PATTERNS

        matches = []
        for line in LOG_LINES_SATA_ISSUES:
            for name, (pattern, severity, desc) in ERROR_PATTERNS.items():
                if pattern.search(line):
                    matches.append(name)

        # Should detect link issues
        assert "link_slow" in matches or "sata_down" in matches

    def test_mixed_log_severity(self):
        """Test that worst severity is used for mixed logs."""
        import re
        from homelab_storage_monitor.checks.journal import ERROR_PATTERNS

        severities = []
        for line in LOG_LINES_MIXED:
            for name, (pattern, severity, desc) in ERROR_PATTERNS.items():
                if pattern.search(line):
                    severities.append(severity)

        if severities:
            worst = max(severities, key=lambda s: s.severity)
            assert worst == Status.CRIT  # I/O error is CRIT
