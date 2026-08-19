"""Tests for the database layer: schema, kv store, baselines, retention."""

from __future__ import annotations

import sqlite3
from datetime import UTC, timedelta

from homelab_storage_monitor.db import SCHEMA_VERSION, Database
from homelab_storage_monitor.models import CheckResult, Metric, RunResult, Status
from homelab_storage_monitor.timeutil import utcnow


class TestSchema:
    def test_fresh_db_gets_current_version(self, db):
        with db.connection() as conn:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION

    def test_v1_to_v2_migration_swaps_metrics_index(self, tmp_path):
        path = tmp_path / "old.sqlite"
        Database(path)

        # Rewind to v1 with the old index in place
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_ts_name ON metrics(ts, metric_name)")
        conn.execute("UPDATE schema_version SET version = 1")
        conn.commit()
        conn.close()

        # Reopening migrates
        db = Database(path)
        with db.connection() as conn2:
            version = conn2.execute("SELECT version FROM schema_version").fetchone()["version"]
            indexes = {
                row["name"]
                for row in conn2.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
        assert version == SCHEMA_VERSION
        assert "idx_metrics_name_ts" in indexes
        assert "idx_metrics_ts_name" not in indexes


class TestV4TimestampMigration:
    def rewind_to_v3(self, path, naive_metric_ts: str, aware_metric_ts: str):
        conn = sqlite3.connect(str(path))
        conn.execute(
            "INSERT INTO metrics (ts, metric_name, labels_json, value_num) VALUES (?, 'poh', '{}', 1.0)",
            (naive_metric_ts,),
        )
        conn.execute(
            "INSERT INTO metrics (ts, metric_name, labels_json, value_num) VALUES (?, 'poh', '{}', 2.0)",
            (aware_metric_ts,),
        )
        conn.execute("UPDATE schema_version SET version = 3")
        conn.commit()
        conn.close()

    def test_naive_rows_become_utc_and_ordering_heals(self, tmp_path):
        from datetime import datetime

        path = tmp_path / "old.sqlite"
        Database(path)

        # A legacy naive local-time row that in real time precedes the aware
        # UTC row written one hour later
        naive_local = datetime.now().replace(microsecond=0)
        real_utc = naive_local.astimezone(UTC)
        later_aware = (real_utc + timedelta(hours=1)).isoformat()
        self.rewind_to_v3(path, naive_local.isoformat(), later_aware)

        db = Database(path)  # reopening runs the migration
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT ts, value_num FROM metrics ORDER BY ts ASC"
            ).fetchall()

        # The legacy row is now aware UTC, equal to its real instant
        assert all("+" in r["ts"] for r in rows)
        assert datetime.fromisoformat(rows[0]["ts"]) == real_utc
        # TEXT ordering now matches real time: legacy row first
        assert [r["value_num"] for r in rows] == [1.0, 2.0]

    def test_aware_rows_are_untouched(self, tmp_path):
        path = tmp_path / "old.sqlite"
        Database(path)
        aware = utcnow().isoformat()
        self.rewind_to_v3(path, aware, aware)

        Database(path)
        conn = sqlite3.connect(str(path))
        rows = conn.execute("SELECT ts FROM metrics").fetchall()
        conn.close()
        assert all(ts == aware for (ts,) in rows)


class TestKvStore:
    def test_roundtrip(self, db):
        assert db.kv_get("missing") is None
        db.kv_set("k", "v1")
        assert db.kv_get("k") == "v1"
        db.kv_set("k", "v2")
        assert db.kv_get("k") == "v2"
        db.kv_delete("k")
        assert db.kv_get("k") is None


class TestSmartBaseline:
    def test_oldest_value_in_window(self, db):
        with db.connection() as conn:
            for age_days, value in [(10, 1), (5, 3), (1, 8)]:
                ts = (utcnow() - timedelta(days=age_days)).isoformat()
                conn.execute(
                    "INSERT INTO smart_history (ts, disk, attr_id, raw_value) VALUES (?, ?, ?, ?)",
                    (ts, "/dev/sda", 5, value),
                )
        # 7-day window excludes the 10-day-old row; the 5-day-old one is baseline
        assert db.get_smart_attr_baseline("/dev/sda", 5, 7) == 3
        # 15-day window reaches the oldest row
        assert db.get_smart_attr_baseline("/dev/sda", 5, 15) == 1
        # No history for this attribute
        assert db.get_smart_attr_baseline("/dev/sda", 199, 7) is None


class TestMetricSeries:
    def seed(self, db, hours: int, mount: str = "/mnt/a"):
        """One fs_usage_pct point per 15 minutes for the given duration."""
        now = utcnow()
        metrics = []
        for i in range(hours * 4):
            metrics.append(Metric(
                name="fs_usage_pct",
                value_num=50.0 + i * 0.1,
                labels={"mount": mount},
                ts=now - timedelta(minutes=15 * i),
            ))
        db.save_metrics(metrics)

    def test_series_is_ascending_and_filtered_by_labels(self, db):
        self.seed(db, hours=4, mount="/mnt/a")
        self.seed(db, hours=4, mount="/mnt/b")

        series = db.get_metric_series(
            "fs_usage_pct", {"mount": "/mnt/a"},
            since=utcnow() - timedelta(hours=10),
            bucket_seconds=900,
        )
        assert len(series) == 16
        timestamps = [p["ts"] for p in series]
        assert timestamps == sorted(timestamps)

    def test_bucketing_downsamples(self, db):
        self.seed(db, hours=8)
        raw = db.get_metric_series(
            "fs_usage_pct", {"mount": "/mnt/a"},
            since=utcnow() - timedelta(hours=10), bucket_seconds=900,
        )
        hourly = db.get_metric_series(
            "fs_usage_pct", {"mount": "/mnt/a"},
            since=utcnow() - timedelta(hours=10), bucket_seconds=3600,
        )
        assert len(raw) == 32
        assert len(hourly) <= 10  # ~8 hourly buckets (+/- boundary alignment)

    def test_since_filters_out_old_points(self, db):
        self.seed(db, hours=8)
        series = db.get_metric_series(
            "fs_usage_pct", {"mount": "/mnt/a"},
            since=utcnow() - timedelta(hours=2), bucket_seconds=900,
        )
        assert len(series) == 8

    def test_label_sets_enumeration(self, db):
        self.seed(db, hours=1, mount="/mnt/a")
        self.seed(db, hours=1, mount="/mnt/b")
        sets = db.get_metric_label_sets("fs_usage_pct", since=utcnow() - timedelta(hours=2))
        mounts = sorted(s["mount"] for s in sets)
        assert mounts == ["/mnt/a", "/mnt/b"]

    def test_latest_values_per_series(self, db):
        now = utcnow()
        db.save_metrics([
            Metric(name="fs_free_bytes", value_num=100.0, labels={"mount": "/mnt/a"},
                   ts=now - timedelta(hours=1)),
            Metric(name="fs_free_bytes", value_num=90.0, labels={"mount": "/mnt/a"}, ts=now),
            Metric(name="fs_free_bytes", value_num=500.0, labels={"mount": "/mnt/b"}, ts=now),
        ])
        latest = db.get_latest_metric_values("fs_free_bytes", since=now - timedelta(days=1))
        by_mount = {v["labels"]["mount"]: v["value_num"] for v in latest}
        assert by_mount == {"/mnt/a": 90.0, "/mnt/b": 500.0}


class TestEventSourceFilter:
    def test_filter_by_source(self, db):
        from homelab_storage_monitor.models import Event, EventType

        db.save_event(Event(event_type=EventType.STATE_CHANGE, severity=Status.CRIT,
                            source="lvm_raid", message="degraded"))
        db.save_event(Event(event_type=EventType.STATE_CHANGE, severity=Status.WARN,
                            source="smart", message="warning"))

        events = db.get_events(source="lvm_raid")
        assert len(events) == 1
        assert events[0]["source"] == "lvm_raid"


class TestRetention:
    def test_old_rows_are_deleted(self, db, config):
        old_ts = utcnow() - timedelta(days=config.history.retention_days_metrics + 10)

        db.save_metrics([Metric(name="fs_usage_pct", value_num=50.0, ts=old_ts)])
        db.save_metrics([Metric(name="fs_usage_pct", value_num=60.0)])

        run = RunResult(
            hostname="test",
            ts_start=old_ts,
            ts_end=old_ts,
            check_results=[CheckResult(name="smart", status=Status.OK, summary="ok")],
        )
        db.save_run(run)

        deleted = db.run_retention(config)
        assert deleted["metrics"] == 1
        assert deleted["runs"] == 1

        remaining = db.get_metrics("fs_usage_pct")
        assert len(remaining) == 1
        assert remaining[0]["value_num"] == 60.0
