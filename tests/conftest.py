"""Shared fixtures: temp database, config, and captured smartctl JSON shapes."""

from __future__ import annotations

import pytest

from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database


@pytest.fixture
def db(tmp_path):
    """A fresh SQLite database in a temp directory."""
    return Database(tmp_path / "test.sqlite")


@pytest.fixture
def config(tmp_path):
    """Default config pointing at a temp database."""
    cfg = Config()
    cfg.history.db_path = str(tmp_path / "test.sqlite")
    return cfg


def make_ata_smart_data(
    *,
    passed: bool = True,
    attrs: dict[int, dict] | None = None,
    error_count: int = 0,
    selftests: list[dict] | None = None,
    power_on_hours: int = 10000,
) -> dict:
    """Build a realistic smartctl -j output for an ATA disk.

    attrs maps attribute id -> {"raw": int, "value": int (normalized)}.
    """
    attrs = attrs if attrs is not None else {}
    table = []
    for attr_id, spec in attrs.items():
        table.append({
            "id": attr_id,
            "name": f"Attr_{attr_id}",
            "value": spec.get("value", 100),
            "worst": spec.get("worst", 100),
            "thresh": spec.get("thresh", 0),
            "raw": {"value": spec["raw"], "string": str(spec["raw"])},
        })

    data = {
        "json_format_version": [1, 0],
        "smartctl": {"version": [7, 4], "exit_status": 0},
        "device": {"name": "/dev/sda", "type": "sat", "protocol": "ATA"},
        "model_name": "TESTDISK 4TB",
        "serial_number": "TEST123",
        "firmware_version": "1.0",
        "user_capacity": {"blocks": 7814037168, "bytes": 4000787030016},
        "rotation_rate": 5400,
        "smart_status": {"passed": passed},
        "power_on_time": {"hours": power_on_hours},
        "ata_smart_attributes": {"table": table},
        "ata_smart_self_test_log": {
            "standard": {"table": selftests or [], "count": len(selftests or [])}
        },
        "ata_smart_error_log": {
            "summary": {"count": error_count, "table": []}
        },
    }
    return data


def make_nvme_smart_data(
    *,
    passed: bool = True,
    media_errors: int = 0,
    percentage_used: int = 5,
    available_spare: int = 100,
    available_spare_threshold: int = 10,
    temperature: int = 40,
    critical_warning: int = 0,
    num_err_log_entries: int = 0,
) -> dict:
    """Build a realistic smartctl -j output for an NVMe disk."""
    return {
        "json_format_version": [1, 0],
        "smartctl": {"version": [7, 4], "exit_status": 0},
        "device": {"name": "/dev/nvme0", "type": "nvme", "protocol": "NVMe"},
        "model_name": "TESTNVME 1TB",
        "serial_number": "NVME123",
        "smart_status": {"passed": passed},
        "nvme_smart_health_information_log": {
            "critical_warning": critical_warning,
            "temperature": temperature,
            "available_spare": available_spare,
            "available_spare_threshold": available_spare_threshold,
            "percentage_used": percentage_used,
            "media_errors": media_errors,
            "num_err_log_entries": num_err_log_entries,
            "power_on_hours": 5000,
            "power_cycles": 100,
            "unsafe_shutdowns": 2,
            "data_units_written": 1000000,
            "data_units_read": 2000000,
        },
    }


def make_open_failure_data(disk: str = "/dev/sdb") -> dict:
    """smartctl -j output when the device cannot be opened (dead/missing disk)."""
    return {
        "json_format_version": [1, 0],
        "smartctl": {
            "version": [7, 4],
            "exit_status": 2,
            "messages": [
                {
                    "string": f"Smartctl open device: {disk} failed: No such device",
                    "severity": "error",
                }
            ],
        },
    }
