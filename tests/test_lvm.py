"""Tests for LVM check with sample outputs."""

import json

import pytest

from homelab_storage_monitor.config import Config
from homelab_storage_monitor.models import Status


# Sample LVM output fixtures
LVM_OUTPUT_HEALTHY = {
    "report": [
        {
            "lv": [
                {
                    "vg_name": "RAID",
                    "lv_name": "RAID",
                    "segtype": "raid1",
                    "lv_attr": "-wi-ao----",
                    "copy_percent": "100.00",
                    "devices": "RAID_rimage_0(0),RAID_rimage_1(0)",
                    "lv_health": "",
                }
            ]
        }
    ]
}

LVM_OUTPUT_SYNCING = {
    "report": [
        {
            "lv": [
                {
                    "vg_name": "RAID",
                    "lv_name": "RAID",
                    "segtype": "raid1",
                    "lv_attr": "-wi-a-s---",
                    "copy_percent": "45.67",
                    "devices": "RAID_rimage_0(0),RAID_rimage_1(0)",
                    "lv_health": "",
                }
            ]
        }
    ]
}

LVM_OUTPUT_DEGRADED = {
    "report": [
        {
            "lv": [
                {
                    "vg_name": "RAID",
                    "lv_name": "RAID",
                    "segtype": "raid1",
                    "lv_attr": "-wi-a-----p",
                    "copy_percent": "100.00",
                    "devices": "RAID_rimage_0(0)",
                    "lv_health": "partial",
                }
            ]
        }
    ]
}

LVM_OUTPUT_NOT_RAID = {
    "report": [
        {
            "lv": [
                {
                    "vg_name": "RAID",
                    "lv_name": "RAID",
                    "segtype": "linear",
                    "lv_attr": "-wi-ao----",
                    "copy_percent": "",
                    "devices": "sda1(0)",
                    "lv_health": "",
                }
            ]
        }
    ]
}


class TestLvmOutputParsing:
    """Test parsing of LVM JSON output."""

    def test_parse_healthy_lv(self):
        """Test parsing healthy RAID1 LV."""
        lv_data = LVM_OUTPUT_HEALTHY["report"][0]["lv"][0]

        assert lv_data["vg_name"] == "RAID"
        assert lv_data["lv_name"] == "RAID"
        assert lv_data["segtype"] == "raid1"
        assert float(lv_data["copy_percent"]) == 100.0
        assert lv_data["lv_health"] == ""

    def test_parse_syncing_lv(self):
        """Test parsing syncing LV."""
        lv_data = LVM_OUTPUT_SYNCING["report"][0]["lv"][0]

        assert float(lv_data["copy_percent"]) == 45.67

    def test_parse_degraded_lv(self):
        """Test parsing degraded LV."""
        lv_data = LVM_OUTPUT_DEGRADED["report"][0]["lv"][0]

        assert lv_data["lv_health"] == "partial"
        assert "p" in lv_data["lv_attr"]

    def test_detect_non_raid(self):
        """Test detection of non-RAID LV."""
        lv_data = LVM_OUTPUT_NOT_RAID["report"][0]["lv"][0]

        assert lv_data["segtype"] == "linear"
        assert lv_data["segtype"] not in ("raid1", "mirror")
