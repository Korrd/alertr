"""Tests for dashboard view helpers: ranges, staleness, projection, Prometheus."""

from __future__ import annotations

from datetime import timedelta

from homelab_storage_monitor.models import CheckResult, Metric, RunResult, Status
from homelab_storage_monitor.timeutil import utcnow
from homelab_storage_monitor.web.helpers import (
    compute_staleness,
    human_bytes,
    parse_range,
    project_days_until_full,
    render_prometheus,
)


class TestParseRange:
    def test_known_ranges(self):
        assert parse_range("24h") == "24h"
        assert parse_range("90d") == "90d"

    def test_bad_input_falls_back(self):
        assert parse_range(None) == "7d"
        assert parse_range("nonsense") == "7d"


class TestHumanBytes:
    def test_units(self):
        assert human_bytes(None) == "—"
        assert human_bytes(512) == "512B"
        assert human_bytes(1536) == "1.5KB"
        assert human_bytes(4000787030016) == "3.6TB"


def usage_series(days: int, start_pct: float, pct_per_day: float, points_per_day: int = 4):
    """Build a synthetic usage series growing linearly."""
    now = utcnow()
    series = []
    total = days * points_per_day
    for i in range(total + 1):
        offset_days = days - i / points_per_day
        series.append({
            "ts": (now - timedelta(days=offset_days)).isoformat(),
            "value_num": start_pct + (days - offset_days) * pct_per_day,
        })
    return series


class TestProjection:
    def test_linear_growth_projects_days_until_full(self):
        # 70% now, growing 1%/day -> ~30 days until full
        series = usage_series(days=7, start_pct=63.0, pct_per_day=1.0)
        days = project_days_until_full(series)
        assert days is not None
        assert 27 <= days <= 33

    def test_flat_usage_has_no_projection(self):
        series = usage_series(days=7, start_pct=50.0, pct_per_day=0.0)
        assert project_days_until_full(series) is None

    def test_shrinking_usage_has_no_projection(self):
        series = usage_series(days=7, start_pct=50.0, pct_per_day=-0.5)
        assert project_days_until_full(series) is None

    def test_too_few_points_is_none(self):
        series = usage_series(days=7, start_pct=50.0, pct_per_day=1.0)[:3]
        assert project_days_until_full(series) is None

    def test_short_time_span_is_none(self):
        now = utcnow()
        series = [
            {"ts": (now - timedelta(minutes=i * 15)).isoformat(), "value_num": 50 + i}
            for i in range(6)
        ]
        assert project_days_until_full(series) is None


class TestStaleness:
    def seed_run(self, db, age: timedelta):
        ts = utcnow() - age
        db.save_run(RunResult(
            hostname="test",
            ts_start=ts,
            ts_end=ts,
            check_results=[CheckResult(name="smart", status=Status.OK, summary="ok")],
        ))

    def test_no_runs_is_not_stale(self, db, config):
        s = compute_staleness(db, config)
        assert not s.is_stale
        assert s.last_run_id is None

    def test_fresh_run_is_not_stale(self, db, config):
        self.seed_run(db, timedelta(minutes=5))
        s = compute_staleness(db, config)
        assert not s.is_stale
        assert s.last_run_id is not None

    def test_old_run_is_stale(self, db, config):
        # Default interval 900s; 2 hours is well past 2x
        self.seed_run(db, timedelta(hours=2))
        s = compute_staleness(db, config)
        assert s.is_stale


class TestPrometheus:
    def test_render_includes_checks_metrics_and_staleness(self, db, config):
        ts = utcnow()
        db.save_run(RunResult(
            hostname="test",
            ts_start=ts,
            ts_end=ts,
            check_results=[
                CheckResult(name="smart", status=Status.CRIT, summary="bad",
                            identifier="/dev/sda"),
                CheckResult(name="journal", status=Status.OK, summary="ok"),
            ],
        ))
        db.save_metrics([
            Metric(name="fs_usage_pct", value_num=76.3, labels={"mount": "/hostfs/x"}),
            Metric(name="smart_attr_raw", value_num=42.0,
                   labels={"disk": "/dev/sda", "attr": "194"}),
        ])

        body = render_prometheus(db, config)

        assert 'hsm_info{version=' in body
        assert 'hsm_check_status{check="smart",identifier="/dev/sda"} 3' in body
        assert 'hsm_check_status{check="journal"} 0' in body
        assert 'hsm_fs_usage_pct{mount="/hostfs/x"} 76.3' in body
        assert 'hsm_smart_attr_raw{attr="194",disk="/dev/sda"} 42' in body
        assert "hsm_collector_stale 0" in body
        assert "hsm_last_run_timestamp_seconds" in body

    def test_label_escaping(self, db, config):
        db.save_metrics([
            Metric(name="fs_usage_pct", value_num=10.0, labels={"mount": 'a"b\\c'}),
        ])
        body = render_prometheus(db, config)
        assert 'mount="a\\"b\\\\c"' in body
