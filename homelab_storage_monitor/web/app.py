"""FastAPI dashboard application."""

from __future__ import annotations

import base64
import contextlib
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from homelab_storage_monitor import __version__
from homelab_storage_monitor.config import Config, load_config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import EventType, Status
from homelab_storage_monitor.smart_attrs import Importance, get_attr_info
from homelab_storage_monitor.timeutil import parse_ts, utcnow
from homelab_storage_monitor.web.helpers import (
    RANGES,
    compute_staleness,
    human_bytes,
    parse_range,
    project_days_until_full,
    range_since,
    rel_ts_tag,
    render_prometheus,
    ts_tag,
)

# Paths for templates and static files
PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

# How far back "latest known value" lookups reach; pages keep showing the
# last known state (behind the staleness banner) if the collector stops
LATEST_WINDOW = timedelta(days=30)

# Health-critical SMART attributes shown prominently with trend charts;
# everything else is collapsed into the "all attributes" list
KEY_ATTR_IDS = {5, 177, 187, 188, 190, 194, 197, 198, 199, 231, 233,
                1001, 1002, 1003, 1004, 1010}
TEMP_ATTR_IDS = (194, 190, 1001)  # preference order

IMPORTANCE_RANK = {
    Importance.CRITICAL: 0,
    Importance.HIGH: 1,
    Importance.MEDIUM: 2,
    Importance.LOW: 3,
}


# Pydantic models for API requests
class AckRequest(BaseModel):
    """Request model for acknowledging SMART errors."""
    disk: str
    error_count: int
    note: str | None = None


def _parse_query_ts(value: str | None, param: str) -> datetime | None:
    """Parse an ISO timestamp query param, returning 422 on bad input."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid ISO timestamp for '{param}': {value}",
        ) from None


def _parse_enum(enum_cls: type, value: str | None, param: str) -> Any:
    """Parse an enum query param, returning 422 on bad input."""
    if not value:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(e.value for e in enum_cls)  # type: ignore[attr-defined]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid value for '{param}': {value} (expected one of: {valid})",
        ) from None


def _slug(value: str) -> str:
    """Anchor/element-id-safe form of a disk path or mountpoint."""
    return value.replace("/", "-")


def _series_payload(series: list[dict[str, Any]]) -> dict[str, list]:
    """Split a series into parallel ts/value arrays for chart JS."""
    return {
        "ts": [p["ts"] for p in series],
        "values": [p["value_num"] for p in series],
    }


def _adjusted_check_status(check: dict[str, Any], smart_acks: dict[str, Any]) -> str:
    """A check's status after applying SMART error acknowledgments."""
    check_status = check["status"]
    if check["name"] != "smart" or check_status != "WARN":
        return check_status

    details = check.get("details", {})
    error_count = details.get("selftest", {}).get("error_count", 0)
    issues = details.get("issues", [])

    # Warnings not covered by the ack system: the check provides them
    # structured; fall back to string matching for runs stored by older versions
    if "other_warnings" in details:
        non_ack_warnings = details["other_warnings"]
    else:
        non_ack_warnings = [
            w for w in details.get("warnings", [])
            if "error log" not in w.lower() and "error(s)" not in w.lower()
        ]

    ack = smart_acks.get(check.get("identifier", ""))
    if ack and ack["error_count_acked"] >= error_count and not issues and not non_ack_warnings:
        return "OK"
    return check_status


def _check_link(check: dict[str, Any]) -> str:
    """Dashboard page a check result row should link to."""
    name = check["name"]
    identifier = check.get("identifier", "")
    if name == "smart" and identifier:
        return f"/smart#disk-{_slug(identifier)}"
    if name == "filesystem" and identifier:
        return f"/filesystem#mount-{_slug(identifier)}"
    if name == "lvm_raid":
        return "/lvm"
    if name == "journal":
        return "/events"
    return "/"


def _latest_temps_by_disk(attr_latest: list[dict[str, Any]]) -> dict[str, float]:
    """Latest temperature per disk from smart_attr_raw latest values."""
    by_disk: dict[str, dict[int, float]] = {}
    for v in attr_latest:
        if v["value_num"] is None:
            continue
        disk = v["labels"].get("disk", "")
        try:
            attr_id = int(v["labels"].get("attr", ""))
        except ValueError:
            continue
        if attr_id in TEMP_ATTR_IDS:
            by_disk.setdefault(disk, {})[attr_id] = v["value_num"]

    temps: dict[str, float] = {}
    for disk, attrs in by_disk.items():
        for attr_id in TEMP_ATTR_IDS:
            if attr_id in attrs and attrs[attr_id] > 0:
                temps[disk] = attrs[attr_id]
                break
    return temps


def _attr_unit(attr_id: int) -> str:
    if attr_id in (190, 194, 1001):
        return "°C"
    if attr_id == 3:
        return "ms"
    if attr_id in (1002, 1003):
        return "%"
    if attr_id in (1008, 1009):
        return "GB"
    return ""


def _attr_value_class(attr_id: int, value: float, config: Config) -> str:
    """Status coloring for a SMART attribute value (config-driven)."""
    thresholds = config.smart.thresholds
    info = get_attr_info(attr_id)

    if attr_id in (190, 194, 1001):
        if value >= thresholds.temp_crit_c:
            return "value-critical"
        if value >= thresholds.temp_warn_c:
            return "value-warning"
        return "value-normal"

    if attr_id == 1002:  # NVMe percentage used
        return "value-warning" if value >= 90 else "value-normal"
    if attr_id == 1003:  # NVMe available spare
        return "value-warning" if value < 20 else "value-normal"

    if info.higher_is_worse and value > 0:
        if info.importance == Importance.CRITICAL:
            return "value-critical"
        if info.importance == Importance.HIGH:
            return "value-warning"
    return "value-normal"


def build_overview_data(db: Database, config: Config) -> dict[str, Any]:
    """Data for the overview page: effective status, tiles, events, issues."""
    latest_run = db.get_latest_run()
    open_issues = db.get_open_issues()
    recent_events = db.get_events(limit=10)
    smart_acks = db.get_all_smart_acks()

    disk_counts = {"ok": 0, "problem": 0, "unknown": 0}

    if latest_run and latest_run.get("check_results"):
        effective_status = "OK"
        for check in latest_run["check_results"]:
            adjusted = _adjusted_check_status(check, smart_acks)
            check["display_status"] = adjusted
            check["link"] = _check_link(check)

            if check["name"] == "smart":
                if adjusted == "OK":
                    disk_counts["ok"] += 1
                elif adjusted == "UNKNOWN":
                    disk_counts["unknown"] += 1
                else:
                    disk_counts["problem"] += 1

            # Update effective status (worst wins)
            if adjusted == "CRIT":
                effective_status = "CRIT"
            elif adjusted == "WARN" and effective_status != "CRIT":
                effective_status = "WARN"
            elif adjusted == "UNKNOWN" and effective_status == "OK":
                effective_status = "UNKNOWN"

        latest_run["effective_status"] = effective_status

    # Storage tile: totals across all monitored mounts
    since = utcnow() - LATEST_WINDOW
    total_bytes = sum(
        v["value_num"] or 0 for v in db.get_latest_metric_values("fs_total_bytes", since)
    )
    free_bytes = sum(
        v["value_num"] or 0 for v in db.get_latest_metric_values("fs_free_bytes", since)
    )
    storage = None
    if total_bytes > 0:
        storage = {
            "total": total_bytes,
            "free": free_bytes,
            "used_pct": (total_bytes - free_bytes) / total_bytes * 100,
        }

    # Hottest disk tile
    temps = _latest_temps_by_disk(db.get_latest_metric_values("smart_attr_raw", since))
    hottest = None
    if temps:
        disk = max(temps, key=lambda d: temps[d])
        hottest = {"disk": disk, "temp": temps[disk]}

    return {
        "latest_run": latest_run,
        "open_issues": open_issues,
        "recent_events": recent_events,
        "disk_counts": disk_counts if (disk_counts["ok"] or disk_counts["problem"] or disk_counts["unknown"]) else None,
        "storage": storage,
        "hottest": hottest,
        "temp_crit_c": config.smart.thresholds.temp_crit_c,
        "temp_warn_c": config.smart.thresholds.temp_warn_c,
    }


def build_filesystem_data(db: Database, config: Config, range_key: str) -> list[dict[str, Any]]:
    """Per-mount usage series, free/total, thresholds, and full-in projection."""
    since, bucket = range_since(range_key)
    latest_since = utcnow() - LATEST_WINDOW

    thresholds = {
        mp.path: (mp.warn_pct, mp.crit_pct) for mp in config.filesystem.mountpoints
    }
    frees = {
        v["labels"].get("mount"): v["value_num"]
        for v in db.get_latest_metric_values("fs_free_bytes", latest_since)
    }
    totals = {
        v["labels"].get("mount"): v["value_num"]
        for v in db.get_latest_metric_values("fs_total_bytes", latest_since)
    }
    usages = {
        v["labels"].get("mount"): v["value_num"]
        for v in db.get_latest_metric_values("fs_usage_pct", latest_since)
    }

    # Projection uses a fixed 7-day window so it doesn't change with the view
    proj_since = utcnow() - timedelta(days=7)

    mounts = []
    for label_set in db.get_metric_label_sets("fs_usage_pct", latest_since):
        mount = label_set.get("mount", "unknown")
        series = db.get_metric_series("fs_usage_pct", label_set, since, bucket)
        proj_series = db.get_metric_series("fs_usage_pct", label_set, proj_since, 3600)
        warn, crit = thresholds.get(mount, (85.0, 95.0))

        mounts.append({
            "mount": mount,
            "slug": _slug(mount),
            "usage": usages.get(mount),
            "free": frees.get(mount),
            "total": totals.get(mount),
            "warn": warn,
            "crit": crit,
            "days_until_full": project_days_until_full(proj_series),
            "series": _series_payload(series),
        })

    mounts.sort(key=lambda m: m["mount"])
    return mounts


def build_lvm_data(db: Database, config: Config, range_key: str) -> dict[str, Any]:
    """Current LVM state, sync series, and degraded-state transitions."""
    since, bucket = range_since(range_key)
    latest_since = utcnow() - LATEST_WINDOW

    # Current state from the latest check result
    current = None
    latest_run = db.get_latest_run()
    if latest_run:
        for check in latest_run.get("check_results", []):
            if check["name"] == "lvm_raid":
                current = check
                break

    # Sync percentage series per VG/LV
    volumes = []
    for label_set in db.get_metric_label_sets("lvm_sync_pct", latest_since):
        series = db.get_metric_series("lvm_sync_pct", label_set, since, bucket)
        volumes.append({
            "vg": label_set.get("vg", "?"),
            "lv": label_set.get("lv", "?"),
            "series": _series_payload(series),
        })
    volumes.sort(key=lambda v: (v["vg"], v["lv"]))

    # Degraded-state history: only transitions, not one row per run
    raw = db.get_metrics("lvm_degraded", from_ts=utcnow() - timedelta(days=90), limit=10000)
    transitions: list[dict[str, Any]] = []
    previous: dict[tuple[str, str], float] = {}
    for m in reversed(raw):  # ascending time
        key = (m["labels"].get("vg", "?"), m["labels"].get("lv", "?"))
        value = m["value_num"]
        if key in previous and previous[key] == value:
            continue
        transitions.append({
            "ts": m["ts"],
            "vg": key[0],
            "lv": key[1],
            "degraded": bool(value),
            "initial": key not in previous,
        })
        previous[key] = value
    transitions.reverse()  # newest first

    return {"current": current, "volumes": volumes, "transitions": transitions[:20]}


def build_smart_data(db: Database, config: Config, range_key: str) -> dict[str, dict[str, Any]]:
    """Per-disk SMART view: key attributes with trends, the rest collapsed."""
    since, bucket = range_since(range_key)
    latest_since = utcnow() - LATEST_WINDOW

    def parse_text_values(name: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for v in db.get_latest_metric_values(name, latest_since):
            disk = v["labels"].get("disk", "unknown")
            if v.get("value_text"):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    out[disk] = json.loads(v["value_text"])
        return out

    disk_infos = parse_text_values("disk_info")
    disk_selftests = parse_text_values("disk_selftest")
    disk_acks = db.get_all_smart_acks()
    healths = {
        v["labels"].get("disk", "unknown"): v["value_num"]
        for v in db.get_latest_metric_values("smart_overall_pass", latest_since)
    }

    selftest_cfg = config.smart.selftest

    disks: dict[str, dict[str, Any]] = {}

    def disk_entry(disk: str) -> dict[str, Any]:
        if disk not in disks:
            # Next scheduled self-tests from the collector's kv store
            schedule = None
            if selftest_cfg.enabled:
                last_short = db.kv_get(f"smart:selftest:short:{disk}")
                last_long = db.kv_get(f"smart:selftest:long:{disk}")
                schedule = {
                    "next_short": (
                        (parse_ts(last_short) + timedelta(days=selftest_cfg.short_interval_days)).isoformat()
                        if last_short else None
                    ),
                    "next_long": (
                        (parse_ts(last_long) + timedelta(days=selftest_cfg.long_interval_days)).isoformat()
                        if last_long else None
                    ),
                }
            disks[disk] = {
                "slug": _slug(disk),
                "info": disk_infos.get(disk, {}),
                "selftest": disk_selftests.get(disk, {}),
                "ack": disk_acks.get(disk),
                "health": healths.get(disk),
                "key_attrs": [],
                "other_attrs": [],
                "temp_series": None,
                "schedule": schedule,
            }
        return disks[disk]

    for v in db.get_latest_metric_values("smart_attr_raw", latest_since):
        if v["value_num"] is None:
            continue
        disk = v["labels"].get("disk", "unknown")
        try:
            attr_id = int(v["labels"].get("attr", ""))
        except ValueError:
            continue

        entry = disk_entry(disk)
        info = get_attr_info(attr_id)
        value = round(v["value_num"], 1)
        if value == int(value):
            value = int(value)

        series = db.get_metric_series(
            "smart_attr_raw",
            {"disk": disk, "attr": str(attr_id)},
            since,
            bucket,
        )

        attr_data = {
            "id": attr_id,
            "is_nvme": attr_id >= 1000,
            "name": info.name,
            "description": info.description,
            "importance": info.importance.value,
            "value": value,
            "unit": _attr_unit(attr_id),
            "value_class": _attr_value_class(attr_id, v["value_num"], config),
            "series": _series_payload(series),
        }

        if attr_id in KEY_ATTR_IDS:
            entry["key_attrs"].append(attr_data)
        else:
            entry["other_attrs"].append(attr_data)

    for entry in disks.values():
        entry["key_attrs"].sort(
            key=lambda a: (IMPORTANCE_RANK.get(Importance(a["importance"]), 9), a["id"])
        )
        entry["other_attrs"].sort(key=lambda a: a["id"])

        # Temperature history chart from the preferred temp attribute
        by_id = {a["id"]: a for a in entry["key_attrs"]}
        for temp_id in TEMP_ATTR_IDS:
            if temp_id in by_id and by_id[temp_id]["value"] > 0:
                entry["temp_series"] = by_id[temp_id]["series"]
                break

    return dict(sorted(disks.items()))


def create_app(config: Config | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = load_config()

    # Fail loudly on misconfiguration instead of locking everyone out
    if config.dashboard.auth_enabled and not (
        config.dashboard.auth_password or config.dashboard.auth_token
    ):
        raise ValueError(
            "dashboard.auth_enabled is true but neither auth_password nor "
            "auth_token is set; set one or disable auth"
        )

    app = FastAPI(
        title="Homelab Storage Monitor",
        description="Dashboard for storage health monitoring",
        version=__version__,
    )

    # Store config in app state
    app.state.config = config
    app.state.db = Database(config.history.db_path)

    # Setup templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Custom filter for natural time display
    def hours_to_natural(hours: int | float) -> str:
        """Convert hours to natural time format (years, months, days, hours)."""
        hours = int(hours)
        if hours <= 0:
            return "0 hours"

        years, remainder = divmod(hours, 8760)  # 365 * 24
        months, remainder = divmod(remainder, 730)  # ~30.4 * 24
        days, hrs = divmod(remainder, 24)

        parts = []
        if years > 0:
            parts.append(f"{years}y")
        if months > 0:
            parts.append(f"{months}mo")
        if days > 0:
            parts.append(f"{days}d")
        if hrs > 0 and not years:  # Only show hours if less than a year
            parts.append(f"{hrs}h")

        return " ".join(parts) if parts else "0 hours"

    templates.env.filters["natural_time"] = hours_to_natural
    templates.env.filters["local_ts"] = ts_tag
    templates.env.filters["rel_ts"] = rel_ts_tag
    templates.env.filters["human_bytes"] = human_bytes

    # Add template globals
    templates.env.globals["now"] = utcnow
    templates.env.globals["version"] = __version__
    templates.env.globals["get_attr_info"] = get_attr_info
    templates.env.globals["Importance"] = Importance
    templates.env.globals["ranges"] = list(RANGES.keys())

    # Setup static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def get_db() -> Database:
        return app.state.db

    def page_context(db: Database, **extra: Any) -> dict[str, Any]:
        """Base context every page gets: staleness banner + auto-refresh."""
        return {"staleness": compute_staleness(db, app.state.config), **extra}

    def require_auth(request: Request) -> None:
        """Require authentication if enabled."""
        cfg: Config = app.state.config

        # If auth is disabled, allow all requests
        if not cfg.dashboard.auth_enabled:
            return

        # Get authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )

        # Parse basic auth
        try:
            scheme, credentials = auth_header.split(" ", 1)
            if scheme.lower() != "basic":
                raise ValueError("Invalid scheme")
            decoded = base64.b64decode(credentials).decode("utf-8")
            username, password = decoded.split(":", 1)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials format",
                headers={"WWW-Authenticate": "Basic"},
            ) from None

        # Check username/password
        if cfg.dashboard.auth_password:
            correct_username = secrets.compare_digest(
                username.encode("utf-8"),
                cfg.dashboard.auth_username.encode("utf-8"),
            )
            correct_password = secrets.compare_digest(
                password.encode("utf-8"),
                cfg.dashboard.auth_password.encode("utf-8"),
            )
            if correct_username and correct_password:
                return

        # Check bearer token (passed as password with any username)
        if cfg.dashboard.auth_token and secrets.compare_digest(
            password.encode("utf-8"),
            cfg.dashboard.auth_token.encode("utf-8"),
        ):
            return

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    # -------------------------------------------------------------------------
    # HTML Pages
    # -------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def overview(
        request: Request,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """Dashboard overview page."""
        data = build_overview_data(db, app.state.config)
        return templates.TemplateResponse(request, "overview.html", page_context(db, **data))

    @app.get("/filesystem", response_class=HTMLResponse)
    def filesystem_page(
        request: Request,
        range: str | None = None,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """Filesystem status page."""
        range_key = parse_range(range)
        mounts = build_filesystem_data(db, app.state.config, range_key)
        return templates.TemplateResponse(
            request,
            "filesystem.html",
            page_context(db, mounts=mounts, range_key=range_key),
        )

    @app.get("/lvm", response_class=HTMLResponse)
    def lvm_page(
        request: Request,
        range: str | None = None,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """LVM RAID status page."""
        range_key = parse_range(range)
        data = build_lvm_data(db, app.state.config, range_key)
        return templates.TemplateResponse(
            request,
            "lvm.html",
            page_context(db, range_key=range_key, **data),
        )

    @app.get("/smart", response_class=HTMLResponse)
    def smart_page(
        request: Request,
        range: str | None = None,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """SMART disk health page."""
        range_key = parse_range(range)
        cfg: Config = app.state.config
        disks = build_smart_data(db, cfg, range_key)
        return templates.TemplateResponse(
            request,
            "smart.html",
            page_context(
                db,
                disks=disks,
                range_key=range_key,
                temp_warn_c=cfg.smart.thresholds.temp_warn_c,
                temp_crit_c=cfg.smart.thresholds.temp_crit_c,
            ),
        )

    @app.get("/events", response_class=HTMLResponse)
    def events_page(
        request: Request,
        severity: str | None = None,
        event_type: str | None = None,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """Events timeline page."""
        severity_filter = _parse_enum(Status, severity, "severity")
        type_filter = _parse_enum(EventType, event_type, "event_type")

        events = db.get_events(
            severity=severity_filter,
            event_type=type_filter,
            limit=200,
        )

        return templates.TemplateResponse(
            request,
            "events.html",
            page_context(
                db,
                events=events,
                severity_filter=severity,
                type_filter=event_type,
            ),
        )

    # -------------------------------------------------------------------------
    # API Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/status/current")
    def api_current_status(
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> dict[str, Any]:
        """Get current system status."""
        latest = db.get_latest_run()
        open_issues = db.get_open_issues()

        return {
            "latest_run": latest,
            "open_issues": open_issues,
            "timestamp": utcnow().isoformat(),
        }

    @app.get("/api/runs")
    def api_runs(
        limit: int = Query(default=50, le=500),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Get recent check runs."""
        return db.get_runs(limit=limit, offset=offset)

    @app.get("/api/metrics")
    def api_metrics(
        name: str,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = Query(default=1000, le=10000),
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Query metrics by name."""
        from_dt = _parse_query_ts(from_ts, "from_ts")
        to_dt = _parse_query_ts(to_ts, "to_ts")

        return db.get_metrics(
            name=name,
            from_ts=from_dt,
            to_ts=to_dt,
            limit=limit,
        )

    @app.get("/api/events")
    def api_events(
        from_ts: str | None = None,
        to_ts: str | None = None,
        severity: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        limit: int = Query(default=100, le=1000),
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Query events."""
        from_dt = _parse_query_ts(from_ts, "from_ts")
        to_dt = _parse_query_ts(to_ts, "to_ts")
        sev = _parse_enum(Status, severity, "severity")
        et = _parse_enum(EventType, event_type, "event_type")

        return db.get_events(
            from_ts=from_dt,
            to_ts=to_dt,
            severity=sev,
            event_type=et,
            source=source,
            limit=limit,
        )

    @app.get("/api/issues/open")
    def api_open_issues(
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Get all open issues."""
        return db.get_open_issues()

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus_metrics(
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> PlainTextResponse:
        """Prometheus text exposition of current state."""
        body = render_prometheus(db, app.state.config)
        return PlainTextResponse(
            content=body,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # -------------------------------------------------------------------------
    # SMART Acknowledgment API
    # -------------------------------------------------------------------------

    @app.post("/api/smart/acknowledge")
    def api_ack_smart_errors(
        req: AckRequest,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> dict[str, str]:
        """Acknowledge SMART errors for a disk."""
        cfg: Config = app.state.config

        db.save_smart_ack(
            disk=req.disk,
            error_count=req.error_count,
            acked_by="user",
            note=req.note,
        )

        # Send ACK notification to Slack if enabled
        if cfg.alerts.slack.enabled and cfg.alerts.slack.webhook_url:
            from homelab_storage_monitor.alerts.slack import send_ack_alert

            dashboard_url = None
            if cfg.dashboard.base_url:
                dashboard_url = f"{cfg.dashboard.base_url}/smart"

            send_ack_alert(
                config=cfg.alerts.slack,
                hostname=cfg.target.get_hostname(),
                disk=req.disk,
                error_count=req.error_count,
                note=req.note,
                dashboard_url=dashboard_url,
            )

        return {"status": "ok", "disk": req.disk}

    @app.delete("/api/smart/acknowledge/{disk:path}")
    def api_delete_smart_ack(
        disk: str,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> dict[str, Any]:
        """Remove acknowledgment for a disk."""
        deleted = db.delete_smart_ack(disk)
        return {"status": "ok" if deleted else "not_found", "disk": disk}

    @app.get("/api/smart/acknowledgments")
    def api_get_smart_acks(
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> dict[str, dict[str, Any]]:
        """Get all SMART acknowledgments."""
        return db.get_all_smart_acks()

    @app.get("/health")
    def health() -> dict[str, str]:
        """Health check endpoint (no auth required)."""
        return {"status": "ok"}

    return app


# For running with uvicorn directly
def get_app() -> FastAPI:
    """Factory function for uvicorn."""
    return create_app()
