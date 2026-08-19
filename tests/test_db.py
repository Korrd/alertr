"""Tests for the database layer: schema, kv store, baselines, retention."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

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
