"""Tests for the dashboard app: auth, validation, ack flow, effective status."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from homelab_storage_monitor.models import CheckResult, RunResult, Status
from homelab_storage_monitor.timeutil import utcnow
from homelab_storage_monitor.web.app import create_app


def basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client(config):
    app = create_app(config)
    return TestClient(app)


class TestAuth:
    def test_health_needs_no_auth(self, config):
        config.dashboard.auth_enabled = True
        config.dashboard.auth_password = "secret"
        client = TestClient(create_app(config))
        assert client.get("/health").status_code == 200

    def test_pages_require_auth_when_enabled(self, config):
        config.dashboard.auth_enabled = True
        config.dashboard.auth_password = "secret"
        client = TestClient(create_app(config))

        assert client.get("/").status_code == 401
        assert client.get("/", headers=basic_auth("admin", "wrong")).status_code == 401
        assert client.get("/", headers=basic_auth("admin", "secret")).status_code == 200

    def test_token_as_password_with_any_username(self, config):
        config.dashboard.auth_enabled = True
        config.dashboard.auth_token = "tok123"
        client = TestClient(create_app(config))
        assert client.get("/", headers=basic_auth("whoever", "tok123")).status_code == 200

    def test_auth_enabled_without_credentials_fails_at_startup(self, config):
        config.dashboard.auth_enabled = True
        with pytest.raises(ValueError, match="auth_enabled"):
            create_app(config)

    def test_no_auth_by_default(self, client):
        assert client.get("/").status_code == 200


class TestParamValidation:
    def test_bad_severity_is_422(self, client):
        assert client.get("/api/events", params={"severity": "BOGUS"}).status_code == 422

    def test_bad_event_type_is_422(self, client):
        assert client.get("/api/events", params={"event_type": "nope"}).status_code == 422

    def test_bad_timestamp_is_422(self, client):
        resp = client.get("/api/metrics", params={"name": "fs_usage_pct", "from_ts": "yesterday"})
        assert resp.status_code == 422

    def test_events_page_bad_filter_is_422_not_500(self, client):
        assert client.get("/events", params={"severity": "BOGUS"}).status_code == 422

    def test_valid_filters_work(self, client):
        assert client.get("/api/events", params={"severity": "CRIT"}).status_code == 200
        assert client.get("/events", params={"severity": "CRIT"}).status_code == 200


class TestAckFlow:
    def test_ack_roundtrip(self, client):
        resp = client.post(
            "/api/smart/acknowledge",
            json={"disk": "/dev/sda", "error_count": 3, "note": "replacing next week"},
        )
        assert resp.status_code == 200

        acks = client.get("/api/smart/acknowledgments").json()
        assert acks["/dev/sda"]["error_count_acked"] == 3
        assert acks["/dev/sda"]["note"] == "replacing next week"

        resp = client.delete("/api/smart/acknowledge//dev/sda")
        assert resp.json()["status"] == "ok"
        assert client.get("/api/smart/acknowledgments").json() == {}


def seed_run(db, check_results):
    db.save_run(
        RunResult(
            hostname="test",
            ts_start=utcnow(),
            ts_end=utcnow(),
            check_results=check_results,
        )
    )


class TestOverviewEffectiveStatus:
    def test_acked_warn_shows_as_ok(self, config):
        app = create_app(config)
        db = app.state.db
        client = TestClient(app)

        seed_run(db, [
            CheckResult(
                name="smart",
                status=Status.WARN,
                summary="/dev/sda: Error log has 3 new error(s)",
                identifier="/dev/sda",
                details={
                    "selftest": {"error_count": 3},
                    "issues": [],
                    "warnings": ["Error log has 3 new error(s) (total: 3)"],
                    "other_warnings": [],
                },
            )
        ])

        # Without an ack, the run shows WARN
        assert "status-card status-warn" in client.get("/").text

        # After acking, the overview recomputes to OK
        db.save_smart_ack("/dev/sda", error_count=3)
        assert "status-card status-ok" in client.get("/").text

    def test_non_ackable_warning_stays_warn(self, config):
        app = create_app(config)
        db = app.state.db
        client = TestClient(app)

        seed_run(db, [
            CheckResult(
                name="smart",
                status=Status.WARN,
                summary="/dev/sda: Temperature high: 58°C",
                identifier="/dev/sda",
                details={
                    "selftest": {"error_count": 0},
                    "issues": [],
                    "warnings": ["Temperature high: 58°C"],
                    "other_warnings": ["Temperature high: 58°C"],
                },
            )
        ])
        db.save_smart_ack("/dev/sda", error_count=99)
        assert "status-card status-warn" in client.get("/").text
