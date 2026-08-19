"""Tests for the SMART check analyzers using captured smartctl JSON shapes."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homelab_storage_monitor.checks.smart import SmartCheck
from homelab_storage_monitor.models import Status
from homelab_storage_monitor.timeutil import utcnow
from tests.conftest import (
    make_ata_smart_data,
    make_nvme_smart_data,
    make_open_failure_data,
)

DISK = "/dev/sda"


@pytest.fixture
def check(config, db):
    config.smart.disks = [DISK]
    config.smart.selftest.enabled = False  # scheduling tested separately
    return SmartCheck(config, db)


def run_disk(check, smart_data, disk=DISK):
    """Run _check_disk with the smartctl call stubbed out."""
    check._get_smart_data = lambda d: smart_data
    return check._check_disk(disk)


def seed_history(db, disk, attrs: dict[int, int], age: timedelta):
    """Insert a smart_history row with a back-dated timestamp."""
    ts = (utcnow() - age).isoformat()
    with db.connection() as conn:
        conn.executemany(
            "INSERT INTO smart_history (ts, disk, attr_id, raw_value) VALUES (?, ?, ?, ?)",
            [(ts, disk, attr_id, value) for attr_id, value in attrs.items()],
        )


class TestSmartctlFailures:
    def test_dead_disk_is_crit_not_healthy(self, check):
        """A disk smartctl can't open must alert, not report healthy."""
        result = run_disk(check, make_open_failure_data(DISK))
        assert result.status == Status.CRIT
        assert "disconnected" in result.summary or "failed" in result.summary

    def test_cmdline_error_is_unknown(self, check):
        data = {
            "smartctl": {
                "exit_status": 1,
                "messages": [{"string": "Unknown option", "severity": "error"}],
            }
        }
        result = run_disk(check, data)
        assert result.status == Status.UNKNOWN

    def test_empty_output_is_unknown(self, check):
        result = run_disk(check, {"smartctl": {"exit_status": 0}})
        assert result.status == Status.UNKNOWN

    def test_smartctl_exception_is_unknown(self, check):
        def boom(disk):
            raise RuntimeError("smartctl not found")

        check._get_smart_data = boom
        result = check._check_disk(DISK)
        assert result.status == Status.UNKNOWN

    def test_unparsable_text_fallback_is_unknown(self, check):
        with pytest.raises(ValueError):
            check._parse_smartctl_text("garbage output with no smart data", DISK)


class TestAtaAnalysis:
    def test_healthy_disk_is_ok(self, check):
        data = make_ata_smart_data(attrs={5: {"raw": 0}, 197: {"raw": 0}})
        result = run_disk(check, data)
        assert result.status == Status.OK

    def test_overall_failed_is_crit(self, check):
        result = run_disk(check, make_ata_smart_data(passed=False))
        assert result.status == Status.CRIT
        assert "FAILED" in result.summary

    def test_pending_sectors_are_crit(self, check):
        data = make_ata_smart_data(attrs={197: {"raw": 3}})
        result = run_disk(check, data)
        assert result.status == Status.CRIT
        assert "Pending sectors" in result.summary

    def test_realloc_growth_within_window_is_crit_and_latches(self, check, db):
        seed_history(db, DISK, {5: 2}, age=timedelta(days=2))
        data = make_ata_smart_data(attrs={5: {"raw": 7}})
        result = run_disk(check, data)
        assert result.status == Status.CRIT
        assert "increased by 5" in result.summary

        # A later run with the same value still sees growth vs the old
        # baseline: no false recovery on the very next run
        result = run_disk(check, data)
        assert result.status == Status.CRIT

    def test_realloc_growth_outside_window_is_quiet(self, check, db, config):
        window = config.smart.thresholds.delta_window_days
        seed_history(db, DISK, {5: 2}, age=timedelta(days=window + 1))
        data = make_ata_smart_data(attrs={5: {"raw": 7}})
        result = run_disk(check, data)
        assert result.status == Status.OK

    def test_crc_growth_is_warn(self, check, db):
        seed_history(db, DISK, {199: 10}, age=timedelta(days=1))
        data = make_ata_smart_data(attrs={199: {"raw": 12}})
        result = run_disk(check, data)
        assert result.status == Status.WARN
        assert "CRC" in result.summary

    def test_uncorrectable_growth_is_crit_but_stable_history_is_quiet(self, check, db):
        # Stable historical value: no alert (was perpetually CRIT before)
        seed_history(db, DISK, {187: 4}, age=timedelta(days=2))
        data = make_ata_smart_data(attrs={187: {"raw": 4}})
        assert run_disk(check, data).status == Status.OK

        # Growth: CRIT
        data = make_ata_smart_data(attrs={187: {"raw": 6}})
        result = run_disk(check, data)
        assert result.status == Status.CRIT
        assert "Uncorrectable" in result.summary

    def test_temperature_thresholds(self, check):
        assert run_disk(check, make_ata_smart_data(attrs={194: {"raw": 40}})).status == Status.OK

        result = run_disk(check, make_ata_smart_data(attrs={194: {"raw": 58}}))
        assert result.status == Status.WARN
        assert "Temperature" in result.summary

        result = run_disk(check, make_ata_smart_data(attrs={194: {"raw": 70}}))
        assert result.status == Status.CRIT

    def test_ssd_wear_low_is_warn(self, check):
        data = make_ata_smart_data(attrs={233: {"raw": 500, "value": 5}})
        result = run_disk(check, data)
        assert result.status == Status.WARN
        assert "wear" in result.summary.lower()

    def test_error_log_ack_suppresses_warning(self, check, db):
        data = make_ata_smart_data(error_count=3)
        result = run_disk(check, data)
        assert result.status == Status.WARN
        assert result.details["other_warnings"] == []

        db.save_smart_ack(DISK, error_count=3, note="known issue")
        result = run_disk(check, data)
        assert result.status == Status.OK

        # New errors beyond the ack re-raise the warning
        result = run_disk(check, make_ata_smart_data(error_count=5))
        assert result.status == Status.WARN
        assert "2 new error(s)" in result.summary


class TestNvmeAnalysis:
    def test_healthy_nvme_is_ok(self, check):
        result = run_disk(check, make_nvme_smart_data())
        assert result.status == Status.OK

    def test_media_error_growth_is_crit(self, check, db):
        seed_history(db, DISK, {1004: 1}, age=timedelta(days=1))
        result = run_disk(check, make_nvme_smart_data(media_errors=4))
        assert result.status == Status.CRIT
        assert "media errors increased" in result.summary

    def test_stable_historical_media_errors_are_quiet(self, check, db):
        seed_history(db, DISK, {1004: 4}, age=timedelta(days=1))
        result = run_disk(check, make_nvme_smart_data(media_errors=4))
        assert result.status == Status.OK
        assert result.details["historical_media_errors"] == 4

    def test_spare_below_threshold_is_crit(self, check):
        result = run_disk(
            check, make_nvme_smart_data(available_spare=5, available_spare_threshold=10)
        )
        assert result.status == Status.CRIT

    def test_nvme_temperature_warn(self, check):
        result = run_disk(check, make_nvme_smart_data(temperature=60))
        assert result.status == Status.WARN

    def test_nvme_wear_warn(self, check):
        result = run_disk(check, make_nvme_smart_data(percentage_used=92))
        assert result.status == Status.WARN

    def test_nvme_error_log_is_ackable(self, check, db):
        data = make_nvme_smart_data(num_err_log_entries=2)
        # The error log warning comes from the shared ATA/NVMe path
        result = run_disk(check, data)
        assert result.status == Status.WARN

        db.save_smart_ack(DISK, error_count=2)
        result = run_disk(check, data)
        assert result.status == Status.OK


class TestSelftestScheduling:
    @pytest.fixture
    def sched_check(self, config, db):
        config.smart.disks = [DISK, "/dev/sdb"]
        config.smart.selftest.enabled = True
        check = SmartCheck(config, db)
        check._launched: list[tuple[str, str]] = []

        def fake_start(disk, kind):
            check._launched.append((disk, kind))
            check._selftest_launched = True
            return True

        check._start_selftest = fake_start
        return check

    def test_first_sight_launches_short_test(self, sched_check, db):
        sched_check._maybe_schedule_selftest(DISK, make_ata_smart_data())
        assert sched_check._launched == [(DISK, "short")]
        assert db.kv_get(f"smart:selftest:short:{DISK}") is not None
        assert db.kv_get(f"smart:selftest:long:{DISK}") is not None

    def test_not_due_means_no_launch(self, sched_check, db):
        now = utcnow().isoformat()
        db.kv_set(f"smart:selftest:short:{DISK}", now)
        db.kv_set(f"smart:selftest:long:{DISK}", now)
        sched_check._maybe_schedule_selftest(DISK, make_ata_smart_data())
        assert sched_check._launched == []

    def test_long_test_due_after_interval(self, sched_check, db, config):
        old = (utcnow() - timedelta(days=config.smart.selftest.long_interval_days + 1)).isoformat()
        db.kv_set(f"smart:selftest:short:{DISK}", old)
        db.kv_set(f"smart:selftest:long:{DISK}", old)
        sched_check._maybe_schedule_selftest(DISK, make_ata_smart_data())
        assert sched_check._launched == [(DISK, "long")]

    def test_short_test_due_after_interval(self, sched_check, db, config):
        old = (utcnow() - timedelta(days=config.smart.selftest.short_interval_days + 1)).isoformat()
        db.kv_set(f"smart:selftest:short:{DISK}", old)
        db.kv_set(f"smart:selftest:long:{DISK}", utcnow().isoformat())
        sched_check._maybe_schedule_selftest(DISK, make_ata_smart_data())
        assert sched_check._launched == [(DISK, "short")]

    def test_at_most_one_launch_per_run(self, sched_check):
        sched_check._maybe_schedule_selftest(DISK, make_ata_smart_data())
        sched_check._maybe_schedule_selftest("/dev/sdb", make_ata_smart_data())
        assert len(sched_check._launched) == 1

    def test_skips_when_test_in_progress(self, sched_check):
        data = make_ata_smart_data()
        data["ata_smart_data"] = {
            "self_test": {"status": {"value": 249, "remaining_percent": 90}}
        }
        sched_check._maybe_schedule_selftest(DISK, data)
        assert sched_check._launched == []
