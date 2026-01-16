"""SQLite database layer for homelab storage monitor."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from homelab_storage_monitor.config import Config
from homelab_storage_monitor.models import (
    CheckResult,
    Event,
    EventType,
    IssueState,
    Metric,
    RunResult,
    Status,
)

logger = logging.getLogger(__name__)

# Schema version for migrations
SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Check runs
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_start TEXT NOT NULL,
    ts_end TEXT NOT NULL,
    hostname TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    version TEXT NOT NULL
);

-- Individual check results
CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL,
    identifier TEXT DEFAULT '',
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

-- Time-series metrics
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    value_num REAL,
    value_text TEXT
);

-- Events (state changes, alerts)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

-- Issue state for deduplication
CREATE TABLE IF NOT EXISTS issue_states (
    key TEXT PRIMARY KEY,
    current_status TEXT NOT NULL,
    last_alert_ts TEXT,
    last_change_ts TEXT NOT NULL,
    alert_count INTEGER DEFAULT 0
);

-- Sync state for stall detection
CREATE TABLE IF NOT EXISTS sync_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    vg TEXT NOT NULL,
    lv TEXT NOT NULL,
    sync_pct REAL NOT NULL
);

-- SMART attribute history for delta detection
CREATE TABLE IF NOT EXISTS smart_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    disk TEXT NOT NULL,
    attr_id INTEGER NOT NULL,
    raw_value INTEGER NOT NULL
);

-- SMART error acknowledgments
CREATE TABLE IF NOT EXISTS smart_acknowledgments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disk TEXT NOT NULL UNIQUE,
    error_count_acked INTEGER NOT NULL,
    acked_by TEXT NOT NULL,
    acked_at TEXT NOT NULL,
    note TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_metrics_ts_name ON metrics(ts, metric_name);
CREATE INDEX IF NOT EXISTS idx_events_ts_severity ON events(ts, severity);
CREATE INDEX IF NOT EXISTS idx_check_results_run_id ON check_results(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts_start);
CREATE INDEX IF NOT EXISTS idx_sync_history_vg_lv ON sync_history(vg, lv, ts);
CREATE INDEX IF NOT EXISTS idx_smart_history_disk ON smart_history(disk, attr_id, ts);
CREATE INDEX IF NOT EXISTS idx_smart_acks_disk ON smart_acknowledgments(disk);
"""


class Database:
    """SQLite database manager."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

            # Check/set schema version
            cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            else:
                current_version = row[0]
                if current_version < SCHEMA_VERSION:
                    self._migrate(conn, current_version)

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        """Run migrations from from_version to SCHEMA_VERSION."""
        # Future migrations would go here
        # Example:
        # if from_version < 2:
        #     conn.execute("ALTER TABLE ... ADD COLUMN ...")
        #     from_version = 2

        conn.execute(
            "UPDATE schema_version SET version = ?",
            (SCHEMA_VERSION,),
        )
        logger.info(f"Migrated database from version {from_version} to {SCHEMA_VERSION}")

    # -------------------------------------------------------------------------
    # Runs and Check Results
    # -------------------------------------------------------------------------

    def save_run(self, run: RunResult) -> int:
        """Save a run and its check results. Returns run ID."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO runs (ts_start, ts_end, hostname, overall_status, version)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run.ts_start.isoformat(),
                    run.ts_end.isoformat(),
                    run.hostname,
                    str(run.overall_status),
                    run.version,
                ),
            )
            run_id = cur.lastrowid
            assert run_id is not None

            for result in run.check_results:
                conn.execute(
                    """
                    INSERT INTO check_results
                        (run_id, check_name, status, summary, details_json, identifier)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        result.name,
                        str(result.status),
                        result.summary,
                        json.dumps(result.details),
                        result.identifier,
                    ),
                )

            return run_id

    def get_latest_run(self) -> dict[str, Any] | None:
        """Get the most recent run with its check results."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT id, ts_start, ts_end, hostname, overall_status, version
                FROM runs ORDER BY ts_start DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            if row is None:
                return None

            run = dict(row)
            run_id = run["id"]

            results_cur = conn.execute(
                """
                SELECT check_name, status, summary, details_json, identifier
                FROM check_results WHERE run_id = ?
                """,
                (run_id,),
            )
            run["check_results"] = [
                {
                    "name": r["check_name"],
                    "status": r["status"],
                    "summary": r["summary"],
                    "details": json.loads(r["details_json"]),
                    "identifier": r["identifier"],
                }
                for r in results_cur
            ]

            return run

    def get_runs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Get recent runs (without check results for efficiency)."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT id, ts_start, ts_end, hostname, overall_status, version
                FROM runs ORDER BY ts_start DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            return [dict(row) for row in cur]

    # -------------------------------------------------------------------------
    # Metrics
    # -------------------------------------------------------------------------

    def save_metrics(self, metrics: list[Metric]) -> None:
        """Save multiple metrics."""
        if not metrics:
            return

        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO metrics (ts, metric_name, labels_json, value_num, value_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        m.ts.isoformat(),
                        m.name,
                        m.labels_json,
                        m.value_num,
                        m.value_text,
                    )
                    for m in metrics
                ],
            )

    def get_metrics(
        self,
        name: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        labels: dict[str, str] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query metrics with optional filters."""
        query = "SELECT ts, metric_name, labels_json, value_num, value_text FROM metrics WHERE metric_name = ?"
        params: list[Any] = [name]

        if from_ts:
            query += " AND ts >= ?"
            params.append(from_ts.isoformat())

        if to_ts:
            query += " AND ts <= ?"
            params.append(to_ts.isoformat())

        if labels:
            # Filter by labels (exact match on JSON)
            labels_json = json.dumps(labels, sort_keys=True)
            query += " AND labels_json = ?"
            params.append(labels_json)

        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            return [
                {
                    "ts": row["ts"],
                    "name": row["metric_name"],
                    "labels": json.loads(row["labels_json"]),
                    "value_num": row["value_num"],
                    "value_text": row["value_text"],
                }
                for row in cur
            ]

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def save_event(self, event: Event) -> int:
        """Save an event. Returns event ID."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (ts, event_type, severity, source, message, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.ts.isoformat(),
                    str(event.event_type),
                    str(event.severity),
                    event.source,
                    event.message,
                    event.payload_json,
                ),
            )
            return cur.lastrowid or 0

    def get_events(
        self,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        severity: Status | None = None,
        event_type: EventType | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query events with optional filters."""
        query = "SELECT id, ts, event_type, severity, source, message, payload_json FROM events WHERE 1=1"
        params: list[Any] = []

        if from_ts:
            query += " AND ts >= ?"
            params.append(from_ts.isoformat())

        if to_ts:
            query += " AND ts <= ?"
            params.append(to_ts.isoformat())

        if severity:
            query += " AND severity = ?"
            params.append(str(severity))

        if event_type:
            query += " AND event_type = ?"
            params.append(str(event_type))

        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self.connection() as conn:
            cur = conn.execute(query, params)
            return [
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "event_type": row["event_type"],
                    "severity": row["severity"],
                    "source": row["source"],
                    "message": row["message"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in cur
            ]

    # -------------------------------------------------------------------------
    # Issue State (for deduplication)
    # -------------------------------------------------------------------------

    def get_issue_state(self, key: str) -> IssueState | None:
        """Get issue state by key."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT key, current_status, last_alert_ts, last_change_ts, alert_count
                FROM issue_states WHERE key = ?
                """,
                (key,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            return IssueState(
                key=row["key"],
                current_status=Status(row["current_status"]),
                last_alert_ts=(
                    datetime.fromisoformat(row["last_alert_ts"])
                    if row["last_alert_ts"]
                    else None
                ),
                last_change_ts=datetime.fromisoformat(row["last_change_ts"]),
                alert_count=row["alert_count"],
            )

    def save_issue_state(self, state: IssueState) -> None:
        """Save or update issue state."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO issue_states
                    (key, current_status, last_alert_ts, last_change_ts, alert_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    state.key,
                    str(state.current_status),
                    state.last_alert_ts.isoformat() if state.last_alert_ts else None,
                    state.last_change_ts.isoformat(),
                    state.alert_count,
                ),
            )

    def get_open_issues(self) -> list[dict[str, Any]]:
        """Get all issues with non-OK status."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT key, current_status, last_alert_ts, last_change_ts, alert_count
                FROM issue_states WHERE current_status != 'OK'
                ORDER BY last_change_ts DESC
                """
            )
            return [
                {
                    "key": row["key"],
                    "status": row["current_status"],
                    "last_alert_ts": row["last_alert_ts"],
                    "last_change_ts": row["last_change_ts"],
                    "alert_count": row["alert_count"],
                }
                for row in cur
            ]

    # -------------------------------------------------------------------------
    # Sync History (for LVM stall detection)
    # -------------------------------------------------------------------------

    def save_sync_pct(self, vg: str, lv: str, sync_pct: float) -> None:
        """Save LVM sync percentage for stall detection."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_history (ts, vg, lv, sync_pct)
                VALUES (?, ?, ?, ?)
                """,
                (datetime.now().isoformat(), vg, lv, sync_pct),
            )

    def get_recent_sync_pcts(self, vg: str, lv: str, limit: int = 10) -> list[float]:
        """Get recent sync percentages for stall detection."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT sync_pct FROM sync_history
                WHERE vg = ? AND lv = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (vg, lv, limit),
            )
            return [row["sync_pct"] for row in cur]

    # -------------------------------------------------------------------------
    # SMART History (for delta detection)
    # -------------------------------------------------------------------------

    def save_smart_attrs(self, disk: str, attrs: dict[int, int]) -> None:
        """Save SMART attributes for delta detection."""
        ts = datetime.now().isoformat()
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT INTO smart_history (ts, disk, attr_id, raw_value)
                VALUES (?, ?, ?, ?)
                """,
                [(ts, disk, attr_id, value) for attr_id, value in attrs.items()],
            )

    def get_last_smart_attrs(self, disk: str) -> dict[int, int]:
        """Get the most recent SMART attributes for a disk."""
        with self.connection() as conn:
            # Get the latest timestamp for this disk
            cur = conn.execute(
                """
                SELECT DISTINCT ts FROM smart_history
                WHERE disk = ? ORDER BY ts DESC LIMIT 1
                """,
                (disk,),
            )
            row = cur.fetchone()
            if row is None:
                return {}

            last_ts = row["ts"]

            cur = conn.execute(
                """
                SELECT attr_id, raw_value FROM smart_history
                WHERE disk = ? AND ts = ?
                """,
                (disk, last_ts),
            )
            return {row["attr_id"]: row["raw_value"] for row in cur}

    # -------------------------------------------------------------------------
    # SMART Acknowledgments
    # -------------------------------------------------------------------------

    def save_smart_ack(
        self,
        disk: str,
        error_count: int,
        acked_by: str = "user",
        note: str | None = None,
    ) -> None:
        """Save or update a SMART error acknowledgment for a disk."""
        ts = datetime.now().isoformat()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO smart_acknowledgments (disk, error_count_acked, acked_by, acked_at, note)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(disk) DO UPDATE SET
                    error_count_acked = excluded.error_count_acked,
                    acked_by = excluded.acked_by,
                    acked_at = excluded.acked_at,
                    note = excluded.note
                """,
                (disk, error_count, acked_by, ts, note),
            )

    def get_smart_ack(self, disk: str) -> dict[str, Any] | None:
        """Get acknowledgment for a specific disk."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT disk, error_count_acked, acked_by, acked_at, note
                FROM smart_acknowledgments WHERE disk = ?
                """,
                (disk,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "disk": row["disk"],
                "error_count_acked": row["error_count_acked"],
                "acked_by": row["acked_by"],
                "acked_at": row["acked_at"],
                "note": row["note"],
            }

    def get_all_smart_acks(self) -> dict[str, dict[str, Any]]:
        """Get all SMART acknowledgments, keyed by disk."""
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT disk, error_count_acked, acked_by, acked_at, note
                FROM smart_acknowledgments
                """
            )
            return {
                row["disk"]: {
                    "disk": row["disk"],
                    "error_count_acked": row["error_count_acked"],
                    "acked_by": row["acked_by"],
                    "acked_at": row["acked_at"],
                    "note": row["note"],
                }
                for row in cur
            }

    def delete_smart_ack(self, disk: str) -> bool:
        """Delete acknowledgment for a disk. Returns True if deleted."""
        with self.connection() as conn:
            cur = conn.execute(
                "DELETE FROM smart_acknowledgments WHERE disk = ?",
                (disk,),
            )
            return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Retention / Cleanup
    # -------------------------------------------------------------------------

    def run_retention(self, config: Config) -> dict[str, int]:
        """
        Clean up old data based on retention settings.
        Returns counts of deleted rows.
        """
        now = datetime.now()
        metrics_cutoff = now - timedelta(days=config.history.retention_days_metrics)
        events_cutoff = now - timedelta(days=config.history.retention_days_events)

        deleted = {"metrics": 0, "events": 0, "runs": 0, "sync_history": 0, "smart_history": 0}

        with self.connection() as conn:
            # Delete old metrics
            cur = conn.execute(
                "DELETE FROM metrics WHERE ts < ?",
                (metrics_cutoff.isoformat(),),
            )
            deleted["metrics"] = cur.rowcount

            # Delete old events
            cur = conn.execute(
                "DELETE FROM events WHERE ts < ?",
                (events_cutoff.isoformat(),),
            )
            deleted["events"] = cur.rowcount

            # Delete old runs (use metrics retention)
            cur = conn.execute(
                "DELETE FROM runs WHERE ts_start < ?",
                (metrics_cutoff.isoformat(),),
            )
            deleted["runs"] = cur.rowcount

            # Delete old sync history
            cur = conn.execute(
                "DELETE FROM sync_history WHERE ts < ?",
                (metrics_cutoff.isoformat(),),
            )
            deleted["sync_history"] = cur.rowcount

            # Delete old SMART history
            cur = conn.execute(
                "DELETE FROM smart_history WHERE ts < ?",
                (metrics_cutoff.isoformat(),),
            )
            deleted["smart_history"] = cur.rowcount

        logger.info(f"Retention cleanup: deleted {deleted}")
        return deleted

    def vacuum(self) -> None:
        """Run VACUUM to reclaim space after deletions."""
        with self.connection() as conn:
            conn.execute("VACUUM")
