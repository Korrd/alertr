"""Tests for configuration parsing."""

from __future__ import annotations

from homelab_storage_monitor.config import Config


class TestConfigParsing:
    def test_defaults(self):
        cfg = Config.from_dict({})
        assert cfg.smart.enabled
        assert cfg.smart.thresholds.delta_window_days == 7
        assert cfg.smart.thresholds.temp_warn_c == 55
        assert cfg.smart.thresholds.temp_crit_c == 65
        assert cfg.smart.selftest.enabled
        assert cfg.smart.selftest.short_interval_days == 7
        assert cfg.smart.selftest.long_interval_days == 30
        assert cfg.journal.latch_hours == 24
        assert cfg.alerts.dedupe_cooldown_seconds == 21600

    def test_new_options_parse(self):
        cfg = Config.from_dict({
            "smart": {
                "disks": ["/dev/sda"],
                "thresholds": {"delta_window_days": 14, "temp_crit_c": 70},
                "selftest": {"enabled": False, "long_interval_days": 60},
            },
            "journal": {"latch_hours": 48},
        })
        assert cfg.smart.thresholds.delta_window_days == 14
        assert cfg.smart.thresholds.temp_crit_c == 70
        assert not cfg.smart.selftest.enabled
        assert cfg.smart.selftest.long_interval_days == 60
        assert cfg.journal.latch_hours == 48

    def test_mountpoint_string_and_dict_forms(self):
        cfg = Config.from_dict({
            "filesystem": {
                "mountpoints": [
                    "/simple/path",
                    {"path": "/custom", "warn_pct": 70, "crit_pct": 90},
                ]
            }
        })
        assert cfg.filesystem.mountpoints[0].path == "/simple/path"
        assert cfg.filesystem.mountpoints[0].warn_pct == 85.0
        assert cfg.filesystem.mountpoints[1].warn_pct == 70
