"""View helpers for the dashboard: time ranges, staleness, projections, exports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from markupsafe import Markup, escape

from homelab_storage_monitor import __version__
from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import Status
from homelab_storage_monitor.timeutil import parse_ts, utcnow

# Chart time ranges: key -> (window, bucket size for downsampling)
RANGES: dict[str, tuple[timedelta, int]] = {
    "24h": (timedelta(hours=24), 900),
    "7d": (timedelta(days=7), 3600),
    "30d": (timedelta(days=30), 4 * 3600),
    "90d": (timedelta(days=90), 12 * 3600),
}
DEFAULT_RANGE = "7d"


def parse_range(value: str | None) -> str:
    """Map a ?range= query param to a known range key (default on bad input)."""
    return value if value in RANGES else DEFAULT_RANGE


def range_since(range_key: str) -> tuple[datetime, int]:
    """Return (since, bucket_seconds) for a range key."""
    window, bucket = RANGES[range_key]
    return utcnow() - window, bucket


@dataclass
class Staleness:
    """Freshness of collector data, shown as a banner when stale."""

    last_run_id: int | None
    last_run_ts: str | None
    age_seconds: float | None
    interval_seconds: int
    is_stale: bool


def compute_staleness(db: Database, config: Config) -> Staleness:
    """Check whether the collector has reported recently.

    Stale means no completed run within roughly two check intervals — the
    dashboard must not present old data as current health.
    """
    interval = config.scheduler.interval_seconds
    runs = db.get_runs(limit=1)
    if not runs:
        return Staleness(None, None, None, interval, False)

    run = runs[0]
    age = (utcnow() - parse_ts(run["ts_end"])).total_seconds()
    return Staleness(
        last_run_id=run["id"],
        last_run_ts=run["ts_end"],
        age_seconds=age,
        interval_seconds=interval,
        is_stale=age > interval * 2 + 60,
    )


def project_days_until_full(series: list[dict[str, Any]]) -> float | None:
    """Estimate days until a filesystem hits 100% from its usage-pct series.

    Least-squares slope over the series; returns None when there is no
    meaningful growth trend or not enough data to call it one.
    """
    points = [
        (parse_ts(p["ts"]).timestamp(), p["value_num"])
        for p in series
        if p.get("value_num") is not None
    ]
    if len(points) < 5:
        return None

    span_seconds = points[-1][0] - points[0][0]
    if span_seconds < 6 * 3600:
        return None

    n = len(points)
    mean_t = sum(t for t, _ in points) / n
    mean_v = sum(v for _, v in points) / n
    denom = sum((t - mean_t) ** 2 for t, _ in points)
    if denom == 0:
        return None
    slope_per_second = sum((t - mean_t) * (v - mean_v) for t, v in points) / denom
    slope_per_day = slope_per_second * 86400

    # Under ~0.01%/day is noise, not growth
    if slope_per_day < 0.01:
        return None

    latest = points[-1][1]
    days = (100.0 - latest) / slope_per_day
    if days < 0:
        return 0.0
    return min(days, 3650.0)


def human_bytes(value: float | int | None) -> str:
    """Format a byte count for display."""
    if value is None:
        return "—"
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def ts_tag(value: str | None) -> Markup:
    """Render a timestamp as a span the frontend converts to local time."""
    if not value:
        return Markup("—")
    try:
        dt = parse_ts(value)
    except ValueError:
        return Markup(escape(value))
    iso = escape(dt.isoformat())
    fallback = escape(dt.strftime("%Y-%m-%d %H:%M UTC"))
    return Markup(f'<span class="ts" data-ts="{iso}">{fallback}</span>')


def rel_ts_tag(value: str | None) -> Markup:
    """Render a timestamp as a live-updating relative time ("5 min ago")."""
    if not value:
        return Markup("—")
    try:
        dt = parse_ts(value)
    except ValueError:
        return Markup(escape(value))
    iso = escape(dt.isoformat())
    fallback = escape(dt.strftime("%Y-%m-%d %H:%M UTC"))
    return Markup(f'<span class="ts ts-rel" data-ts="{iso}">{fallback}</span>')


# -------------------------------------------------------------------------
# Prometheus exposition
# -------------------------------------------------------------------------

# Numeric metrics exported with their labels as-is (hsm_ prefix added)
PROM_METRICS: dict[str, str] = {
    "fs_usage_pct": "Filesystem usage percentage",
    "fs_free_bytes": "Filesystem free bytes (available to non-root)",
    "fs_total_bytes": "Filesystem total bytes",
    "smart_overall_pass": "SMART overall health (1=passed, 0=failed)",
    "smart_attr_raw": "SMART attribute value (NVMe health uses attr ids 1000+)",
    "lvm_sync_pct": "LVM RAID sync percentage",
    "lvm_degraded": "LVM RAID degraded (1=degraded, 0=healthy)",
    "kernel_io_error_count": "Kernel I/O errors found in logs this run",
    "ext4_error_count": "ext4/JBD2 errors found in logs this run",
}


def _prom_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _prom_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{_prom_escape(str(v))}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


def render_prometheus(db: Database, config: Config) -> str:
    """Render current state in Prometheus text exposition format."""
    lines: list[str] = []
    since = utcnow() - timedelta(days=2)

    lines.append("# HELP hsm_info Build information")
    lines.append("# TYPE hsm_info gauge")
    lines.append(f'hsm_info{{version="{_prom_escape(__version__)}"}} 1')

    staleness = compute_staleness(db, config)
    if staleness.last_run_ts is not None:
        lines.append("# HELP hsm_last_run_timestamp_seconds Unix time of the last completed check run")
        lines.append("# TYPE hsm_last_run_timestamp_seconds gauge")
        lines.append(
            f"hsm_last_run_timestamp_seconds {parse_ts(staleness.last_run_ts).timestamp():.0f}"
        )
    lines.append("# HELP hsm_collector_stale 1 when no run completed within two check intervals")
    lines.append("# TYPE hsm_collector_stale gauge")
    lines.append(f"hsm_collector_stale {1 if staleness.is_stale else 0}")

    latest_run = db.get_latest_run()
    if latest_run:
        lines.append("# HELP hsm_check_status Check status (0=OK 1=UNKNOWN 2=WARN 3=CRIT)")
        lines.append("# TYPE hsm_check_status gauge")
        for check in latest_run.get("check_results", []):
            labels = {"check": check["name"]}
            if check.get("identifier"):
                labels["identifier"] = check["identifier"]
            value = Status(check["status"]).severity
            lines.append(f"hsm_check_status{_prom_labels(labels)} {value}")

    for name, help_text in PROM_METRICS.items():
        values = db.get_latest_metric_values(name, since=since)
        numeric = [v for v in values if v["value_num"] is not None]
        if not numeric:
            continue
        lines.append(f"# HELP hsm_{name} {help_text}")
        lines.append(f"# TYPE hsm_{name} gauge")
        for v in numeric:
            lines.append(f"hsm_{name}{_prom_labels(v['labels'])} {v['value_num']:g}")

    return "\n".join(lines) + "\n"
