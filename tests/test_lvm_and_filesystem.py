"""Tests for LVM RAID analysis and filesystem capacity checks."""

from __future__ import annotations

import pytest

from homelab_storage_monitor.checks.filesystem import FilesystemCheck
from homelab_storage_monitor.checks.lvm import LvmCheck
from homelab_storage_monitor.config import MountpointConfig
from homelab_storage_monitor.models import Status


@pytest.fixture
def lvm_check(config, db):
    return LvmCheck(config, db)


def lv_info(**overrides):
    info = {
        "vg_name": "RAID",
        "lv_name": "RAID",
        "segtype": "raid1",
        "lv_attr": "rwi-aor---",
        "copy_percent": "100.00",
        "devices": "RAID_rimage_0(0),RAID_rimage_1(0)",
        "lv_health_status": "",
    }
    info.update(overrides)
    return info


class TestLvmAnalysis:
    def test_healthy_raid1_is_ok(self, lvm_check):
        result = lvm_check._analyze_lv(lv_info(), "RAID", "RAID")
        assert result.status == Status.OK

    def test_partial_attr_is_crit(self, lvm_check):
        result = lvm_check._analyze_lv(lv_info(lv_attr="rwi-aor-p-"), "RAID", "RAID")
        assert result.status == Status.CRIT
        assert "DEGRADED" in result.summary

    def test_health_status_is_crit(self, lvm_check):
        result = lvm_check._analyze_lv(lv_info(lv_health_status="partial"), "RAID", "RAID")
        assert result.status == Status.CRIT

    def test_syncing_is_warn(self, lvm_check):
        result = lvm_check._analyze_lv(lv_info(copy_percent="42.50"), "RAID", "RAID")
        assert result.status == Status.WARN
        assert "syncing" in result.summary

    def test_stalled_sync_is_crit(self, lvm_check, db, config):
        # Seed enough identical sync percentages to trip stall detection
        for _ in range(config.lvm.sync_stall_runs):
            db.save_sync_pct("RAID", "RAID", 42.5)
        result = lvm_check._analyze_lv(lv_info(copy_percent="42.50"), "RAID", "RAID")
        assert result.status == Status.CRIT
        assert "STALLED" in result.summary

    def test_wrong_segtype_is_crit(self, lvm_check):
        result = lvm_check._analyze_lv(lv_info(segtype="linear"), "RAID", "RAID")
        assert result.status == Status.CRIT


@pytest.fixture
def fs_check(config, db):
    return FilesystemCheck(config, db)


class TestFilesystemCheck:
    def test_missing_mount_is_unknown(self, fs_check):
        result = fs_check._check_mountpoint("/does/not/exist", 85.0, 95.0)
        assert result.status == Status.UNKNOWN

    def test_real_path_is_ok_with_high_thresholds(self, fs_check, tmp_path):
        result = fs_check._check_mountpoint(str(tmp_path), 99.9, 100.0)
        assert result.status == Status.OK
        assert result.details["total_bytes"] > 0

    def test_warn_threshold(self, fs_check, tmp_path):
        result = fs_check._check_mountpoint(str(tmp_path), 0.0, 100.0)
        assert result.status == Status.WARN

    def test_crit_threshold(self, fs_check, tmp_path):
        result = fs_check._check_mountpoint(str(tmp_path), 0.0, 0.0)
        assert result.status == Status.CRIT

    def test_metrics_recorded(self, fs_check, config, tmp_path):
        config.filesystem.mountpoints = [MountpointConfig(path=str(tmp_path))]
        fs_check.run()
        names = {m.name for m in fs_check.get_metrics()}
        assert names == {"fs_usage_pct", "fs_free_bytes", "fs_total_bytes"}
