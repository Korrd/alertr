"""FastAPI dashboard application."""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from homelab_storage_monitor.config import Config, load_config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import EventType, Status

# Paths for templates and static files
PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def create_app(config: Config | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = load_config()

    app = FastAPI(
        title="Homelab Storage Monitor",
        description="Dashboard for storage health monitoring",
        version="1.0.0",
    )

    # Store config in app state
    app.state.config = config
    app.state.db = Database(config.history.db_path)

    # Setup templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Add template globals
    templates.env.globals["now"] = datetime.now

    # Setup static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Security
    security = HTTPBasic(auto_error=False)

    def get_db() -> Database:
        return app.state.db

    async def get_optional_credentials(
        request: Request,
    ) -> HTTPBasicCredentials | None:
        """Get credentials if provided, None otherwise."""
        auth = request.headers.get("Authorization")
        if not auth:
            return None
        try:
            return await security(request)
        except Exception:
            return None

    def verify_auth(
        credentials: HTTPBasicCredentials | None,
    ) -> bool:
        """Verify authentication if enabled."""
        cfg: Config = app.state.config

        if not cfg.dashboard.auth_enabled:
            return True

        if credentials is None:
            return False

        # Check username/password
        if cfg.dashboard.auth_password:
            correct_username = secrets.compare_digest(
                credentials.username.encode("utf-8"),
                cfg.dashboard.auth_username.encode("utf-8"),
            )
            correct_password = secrets.compare_digest(
                credentials.password.encode("utf-8"),
                cfg.dashboard.auth_password.encode("utf-8"),
            )
            if correct_username and correct_password:
                return True

        # Check bearer token (passed as password with any username)
        if cfg.dashboard.auth_token:
            if secrets.compare_digest(
                credentials.password.encode("utf-8"),
                cfg.dashboard.auth_token.encode("utf-8"),
            ):
                return True

        return False

    async def require_auth(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(get_optional_credentials)],
    ) -> None:
        """Require authentication if enabled."""
        if not verify_auth(credentials):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    # -------------------------------------------------------------------------
    # HTML Pages
    # -------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def overview(
        request: Request,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """Dashboard overview page."""
        latest_run = db.get_latest_run()
        open_issues = db.get_open_issues()
        recent_events = db.get_events(limit=10)

        return templates.TemplateResponse(
            "overview.html",
            {
                "request": request,
                "latest_run": latest_run,
                "open_issues": open_issues,
                "recent_events": recent_events,
            },
        )

    @app.get("/filesystem", response_class=HTMLResponse)
    async def filesystem_page(
        request: Request,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """Filesystem status page."""
        # Get recent filesystem metrics
        metrics = db.get_metrics("fs_usage_pct", limit=500)

        # Group by mount
        mounts: dict[str, list[dict]] = {}
        for m in metrics:
            mount = m["labels"].get("mount", "unknown")
            if mount not in mounts:
                mounts[mount] = []
            mounts[mount].append(m)

        return templates.TemplateResponse(
            "filesystem.html",
            {
                "request": request,
                "mounts": mounts,
            },
        )

    @app.get("/lvm", response_class=HTMLResponse)
    async def lvm_page(
        request: Request,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """LVM RAID status page."""
        sync_metrics = db.get_metrics("lvm_sync_pct", limit=500)
        degraded_metrics = db.get_metrics("lvm_degraded", limit=100)

        return templates.TemplateResponse(
            "lvm.html",
            {
                "request": request,
                "sync_metrics": sync_metrics,
                "degraded_metrics": degraded_metrics,
            },
        )

    @app.get("/smart", response_class=HTMLResponse)
    async def smart_page(
        request: Request,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """SMART disk health page."""
        # Get overall health
        health_metrics = db.get_metrics("smart_overall_pass", limit=100)

        # Get attribute metrics
        attr_metrics = db.get_metrics("smart_attr_raw", limit=1000)

        # Group by disk
        disks: dict[str, dict] = {}
        for m in health_metrics:
            disk = m["labels"].get("disk", "unknown")
            if disk not in disks:
                disks[disk] = {"health": [], "attrs": {}}
            disks[disk]["health"].append(m)

        for m in attr_metrics:
            disk = m["labels"].get("disk", "unknown")
            attr = m["labels"].get("attr", "unknown")
            if disk not in disks:
                disks[disk] = {"health": [], "attrs": {}}
            if attr not in disks[disk]["attrs"]:
                disks[disk]["attrs"][attr] = []
            disks[disk]["attrs"][attr].append(m)

        return templates.TemplateResponse(
            "smart.html",
            {
                "request": request,
                "disks": disks,
            },
        )

    @app.get("/events", response_class=HTMLResponse)
    async def events_page(
        request: Request,
        severity: str | None = None,
        event_type: str | None = None,
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> HTMLResponse:
        """Events timeline page."""
        severity_filter = Status(severity) if severity else None
        type_filter = EventType(event_type) if event_type else None

        events = db.get_events(
            severity=severity_filter,
            event_type=type_filter,
            limit=200,
        )

        return templates.TemplateResponse(
            "events.html",
            {
                "request": request,
                "events": events,
                "severity_filter": severity,
                "type_filter": event_type,
            },
        )

    # -------------------------------------------------------------------------
    # API Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/status/current")
    async def api_current_status(
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> dict[str, Any]:
        """Get current system status."""
        latest = db.get_latest_run()
        open_issues = db.get_open_issues()

        return {
            "latest_run": latest,
            "open_issues": open_issues,
            "timestamp": datetime.now().isoformat(),
        }

    @app.get("/api/runs")
    async def api_runs(
        limit: int = Query(default=50, le=500),
        offset: int = Query(default=0, ge=0),
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Get recent check runs."""
        return db.get_runs(limit=limit, offset=offset)

    @app.get("/api/metrics")
    async def api_metrics(
        name: str,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = Query(default=1000, le=10000),
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Query metrics by name."""
        from_dt = datetime.fromisoformat(from_ts) if from_ts else None
        to_dt = datetime.fromisoformat(to_ts) if to_ts else None

        return db.get_metrics(
            name=name,
            from_ts=from_dt,
            to_ts=to_dt,
            limit=limit,
        )

    @app.get("/api/events")
    async def api_events(
        from_ts: str | None = None,
        to_ts: str | None = None,
        severity: str | None = None,
        event_type: str | None = None,
        limit: int = Query(default=100, le=1000),
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Query events."""
        from_dt = datetime.fromisoformat(from_ts) if from_ts else None
        to_dt = datetime.fromisoformat(to_ts) if to_ts else None
        sev = Status(severity) if severity else None
        et = EventType(event_type) if event_type else None

        return db.get_events(
            from_ts=from_dt,
            to_ts=to_dt,
            severity=sev,
            event_type=et,
            limit=limit,
        )

    @app.get("/api/issues/open")
    async def api_open_issues(
        db: Database = Depends(get_db),
        _auth: None = Depends(require_auth),
    ) -> list[dict[str, Any]]:
        """Get all open issues."""
        return db.get_open_issues()

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint (no auth required)."""
        return {"status": "ok"}

    return app


# For running with uvicorn directly
def get_app() -> FastAPI:
    """Factory function for uvicorn."""
    return create_app()
