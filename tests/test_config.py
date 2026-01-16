"""Tests for configuration loading."""

import tempfile
from pathlib import Path

import pytest
import yaml

from homelab_storage_monitor.config import Config, load_config


def test_config_defaults():
    """Test default configuration values."""
    config = Config()

    assert config.lvm.enabled is True
    assert config.lvm.vg == "RAID"
    assert config.lvm.lv == "RAID"
    assert config.scheduler.interval_seconds == 900
    assert config.history.retention_days_metrics == 90
    assert config.alerts.dedupe_cooldown_seconds == 21600


def test_config_from_dict():
    """Test loading configuration from dictionary."""
    data = {
        "lvm": {
            "vg": "MyVG",
            "lv": "MyLV",
        },
        "smart": {
            "disks": ["/dev/sda", "/dev/sdb"],
        },
        "filesystem": {
            "mountpoints": [
                {"path": "/data", "warn_pct": 80, "crit_pct": 90},
            ],
        },
    }

    config = Config.from_dict(data)

    assert config.lvm.vg == "MyVG"
    assert config.lvm.lv == "MyLV"
    assert config.smart.disks == ["/dev/sda", "/dev/sdb"]
    assert len(config.filesystem.mountpoints) == 1
    assert config.filesystem.mountpoints[0].path == "/data"
    assert config.filesystem.mountpoints[0].warn_pct == 80


def test_config_from_yaml():
    """Test loading configuration from YAML file."""
    yaml_content = """
lvm:
  vg: TestVG
  lv: TestLV
  sync_stall_runs: 10

alerts:
  slack:
    enabled: true
    webhook_url: "https://hooks.slack.com/test"
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        config = Config.from_yaml(f.name)

    assert config.lvm.vg == "TestVG"
    assert config.lvm.sync_stall_runs == 10
    assert config.alerts.slack.enabled is True
    assert "hooks.slack.com" in config.alerts.slack.webhook_url


def test_config_file_not_found():
    """Test error when config file doesn't exist."""
    with pytest.raises(FileNotFoundError):
        Config.from_yaml("/nonexistent/config.yaml")


def test_mountpoint_simple_format():
    """Test simple string format for mountpoints."""
    data = {
        "filesystem": {
            "mountpoints": ["/data", "/backup"],
        },
    }

    config = Config.from_dict(data)

    assert len(config.filesystem.mountpoints) == 2
    assert config.filesystem.mountpoints[0].path == "/data"
    assert config.filesystem.mountpoints[0].warn_pct == 85.0  # default
    assert config.filesystem.mountpoints[0].crit_pct == 95.0  # default
