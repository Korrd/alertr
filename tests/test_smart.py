"""Tests for SMART check with sample outputs."""

import json

import pytest

from homelab_storage_monitor.models import Status


# Sample SMART output fixtures
SMART_OUTPUT_HEALTHY = {
    "device": {"name": "/dev/sda"},
    "smart_status": {"passed": True},
    "ata_smart_attributes": {
        "table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "worst": 100, "thresh": 10, "raw": {"value": 0}},
            {"id": 187, "name": "Reported_Uncorrect", "value": 100, "worst": 100, "thresh": 0, "raw": {"value": 0}},
            {"id": 188, "name": "Command_Timeout", "value": 100, "worst": 100, "thresh": 0, "raw": {"value": 0}},
            {"id": 197, "name": "Current_Pending_Sector", "value": 100, "worst": 100, "thresh": 0, "raw": {"value": 0}},
            {"id": 198, "name": "Offline_Uncorrectable", "value": 100, "worst": 100, "thresh": 0, "raw": {"value": 0}},
            {"id": 199, "name": "UDMA_CRC_Error_Count", "value": 200, "worst": 200, "thresh": 0, "raw": {"value": 0}},
        ]
    },
}

SMART_OUTPUT_FAILING = {
    "device": {"name": "/dev/sdb"},
    "smart_status": {"passed": False},
    "ata_smart_attributes": {
        "table": [
            {"id": 5, "name": "Reallocated_Sector_Ct", "value": 50, "worst": 50, "thresh": 10, "raw": {"value": 150}},
            {"id": 187, "name": "Reported_Uncorrect", "value": 90, "worst": 90, "thresh": 0, "raw": {"value": 5}},
            {"id": 197, "name": "Current_Pending_Sector", "value": 95, "worst": 95, "thresh": 0, "raw": {"value": 8}},
            {"id": 198, "name": "Offline_Uncorrectable", "value": 95, "worst": 95, "thresh": 0, "raw": {"value": 3}},
        ]
    },
}

SMART_OUTPUT_NVME = {
    "device": {"name": "/dev/nvme0n1"},
    "smart_status": {"passed": True},
    "nvme_smart_health_information_log": {
        "critical_warning": 0,
        "temperature": 35,
        "available_spare": 100,
        "available_spare_threshold": 10,
        "percentage_used": 5,
        "data_units_read": 12345678,
        "data_units_written": 9876543,
        "host_reads": 123456,
        "host_writes": 98765,
        "controller_busy_time": 100,
        "power_cycles": 50,
        "power_on_hours": 5000,
        "unsafe_shutdowns": 2,
        "media_errors": 0,
        "num_err_log_entries": 0,
    },
}


class TestSmartOutputParsing:
    """Test parsing of SMART JSON output."""

    def test_parse_healthy_disk(self):
        """Test parsing healthy disk SMART data."""
        data = SMART_OUTPUT_HEALTHY

        assert data["smart_status"]["passed"] is True
        attrs = {a["id"]: a for a in data["ata_smart_attributes"]["table"]}

        assert attrs[5]["raw"]["value"] == 0  # No reallocated sectors
        assert attrs[197]["raw"]["value"] == 0  # No pending sectors

    def test_parse_failing_disk(self):
        """Test parsing failing disk SMART data."""
        data = SMART_OUTPUT_FAILING

        assert data["smart_status"]["passed"] is False
        attrs = {a["id"]: a for a in data["ata_smart_attributes"]["table"]}

        assert attrs[5]["raw"]["value"] == 150  # Reallocated sectors
        assert attrs[197]["raw"]["value"] == 8  # Pending sectors

    def test_parse_nvme_disk(self):
        """Test parsing NVMe SMART data."""
        data = SMART_OUTPUT_NVME

        assert data["smart_status"]["passed"] is True
        nvme = data["nvme_smart_health_information_log"]

        assert nvme["critical_warning"] == 0
        assert nvme["media_errors"] == 0
        assert nvme["percentage_used"] == 5

    def test_critical_attributes_detection(self):
        """Test detection of critical attribute values."""
        data = SMART_OUTPUT_FAILING
        attrs = {a["id"]: a for a in data["ata_smart_attributes"]["table"]}

        # These should trigger CRIT
        assert attrs[187]["raw"]["value"] > 0  # Uncorrectable errors
        assert attrs[197]["raw"]["value"] > 0  # Pending sectors
        assert attrs[198]["raw"]["value"] > 0  # Offline uncorrectable
