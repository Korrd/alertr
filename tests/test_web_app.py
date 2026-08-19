"""Tests for the dashboard app: auth, validation, ack flow, effective status."""

from __future__ import annotations

import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from homelab_storage_monitor.models import CheckResult, Metric, RunResult, Status
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


class TestStalenessBanner:
    def seed_run_at(self, db, age: timedelta):
        ts = utcnow() - age
        db.save_run(RunResult(
            hostname="test", ts_start=ts, ts_end=ts,
            check_results=[CheckResult(name="smart", status=Status.OK, summary="ok")],
        ))

    def test_fresh_data_shows_no_banner(self, config):
        app = create_app(config)
        self.seed_run_at(app.state.db, timedelta(minutes=5))
        text = TestClient(app).get("/").text
        assert "Data is stale" not in text

    def test_stale_data_shows_banner_on_every_page(self, config):
        app = create_app(config)
        self.seed_run_at(app.state.db, timedelta(hours=3))
        client = TestClient(app)
        for path in ["/", "/filesystem", "/lvm", "/smart", "/events"]:
            assert "Data is stale" in client.get(path).text, path


class TestPrometheusEndpoint:
    def test_metrics_endpoint(self, config):
        app = create_app(config)
        db = app.state.db
        seed_run(db, [CheckResult(name="smart", status=Status.OK, summary="ok",
                                  identifier="/dev/sda")])
        db.save_metrics([
            Metric(name="fs_usage_pct", value_num=42.0, labels={"mount": "/mnt/x"}),
        ])

        resp = TestClient(app).get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "hsm_info{" in resp.text
        assert 'hsm_check_status{check="smart",identifier="/dev/sda"} 0' in resp.text
        assert 'hsm_fs_usage_pct{mount="/mnt/x"} 42' in resp.text

    def test_metrics_respects_auth(self, config):
        config.dashboard.auth_enabled = True
        config.dashboard.auth_password = "secret"
        client = TestClient(create_app(config))
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers=basic_auth("admin", "secret")).status_code == 200


class TestDataPages:
    @pytest.fixture
    def seeded(self, config):
        """App with a run, filesystem history, and SMART attribute history."""
        app = create_app(config)
        db = app.state.db
        now = utcnow()

        seed_run(db, [
            CheckResult(name="smart", status=Status.OK, summary="/dev/sda: SMART healthy",
                        identifier="/dev/sda"),
            CheckResult(name="lvm_raid", status=Status.OK,
                        summary="LV RAID/RAID healthy (RAID1, 100% synced)",
                        identifier="RAID/RAID",
                        details={"vg": "RAID", "lv": "RAID", "segtype": "raid1",
                                 "lv_attr": "rwi-aor---", "copy_percent": "100.00",
                                 "lv_health": "", "devices": "ra(0),rb(0)"}),
        ])

        metrics = []
        for i in range(48):  # 12 hours of history, growing usage
            ts = now - timedelta(minutes=15 * i)
            metrics.extend([
                Metric(name="fs_usage_pct", value_num=80.0 - i * 0.01,
                       labels={"mount": "/hostfs/data"}, ts=ts),
                Metric(name="smart_attr_raw", value_num=41.0,
                       labels={"disk": "/dev/sda", "attr": "194"}, ts=ts),
                Metric(name="smart_attr_raw", value_num=0.0,
                       labels={"disk": "/dev/sda", "attr": "5"}, ts=ts),
                Metric(name="smart_attr_raw", value_num=123.0,
                       labels={"disk": "/dev/sda", "attr": "4"}, ts=ts),
                Metric(name="lvm_sync_pct", value_num=100.0,
                       labels={"vg": "RAID", "lv": "RAID"}, ts=ts),
            ])
        metrics.extend([
            Metric(name="fs_free_bytes", value_num=1.0e12, labels={"mount": "/hostfs/data"}),
            Metric(name="fs_total_bytes", value_num=5.0e12, labels={"mount": "/hostfs/data"}),
            Metric(name="smart_overall_pass", value_num=1.0, labels={"disk": "/dev/sda"}),
            Metric(name="lvm_degraded", value_num=0.0, labels={"vg": "RAID", "lv": "RAID"}),
        ])
        db.save_metrics(metrics)
        return app

    def test_overview_tiles(self, seeded):
        text = TestClient(seeded).get("/").text
        assert "Disks healthy" in text
        assert "Storage used" in text
        assert "Hottest disk" in text
        assert "41" in text  # hottest temp

    def test_filesystem_page_shows_capacity_and_projection(self, seeded):
        text = TestClient(seeded).get("/filesystem").text
        assert "free of" in text
        assert "931.3GB" in text and "4.5TB" in text
        # the fixture grows ~0.9%/day from 80%: a days-until-full estimate shows
        assert "days at current rate" in text

    def test_smart_page_key_and_other_attrs(self, seeded):
        text = TestClient(seeded).get("/smart").text
        assert 'id="disk--dev-sda"' in text  # anchor for overview links
        assert "Health Indicators" in text
        assert "All attributes" in text  # attr 4 is collapsed inventory
        assert "Temperature" in text

    def test_attr_split_follows_importance_taxonomy(self, config):
        from homelab_storage_monitor.web.app import build_smart_data

        app = create_app(config)
        db = app.state.db
        now = utcnow()
        db.save_metrics([
            Metric(name="smart_attr_raw", value_num=val,
                   labels={"disk": "/dev/sda", "attr": attr}, ts=now)
            for attr, val in [("5", 0.0), ("10", 2.0), ("4", 51.0),
                              ("194", 40.0), ("177", 1200.0)]
        ])

        disks = build_smart_data(db, config, "7d")
        key_ids = {a["id"] for a in disks["/dev/sda"]["key_attrs"]}
        other_ids = {a["id"] for a in disks["/dev/sda"]["other_attrs"]}

        # CRITICAL (5 realloc), HIGH (10 spin retry, 177 wear leveling), and
        # temperature (194) are prominent; LOW (4 start/stop) is collapsed
        assert {5, 10, 177, 194} <= key_ids
        assert other_ids == {4}

        # Wear gauges are not error counters: raw value stays uncolored
        wear = next(a for a in disks["/dev/sda"]["key_attrs"] if a["id"] == 177)
        assert wear["value_class"] == "value-normal"
        assert wear["name"] == "Wear Leveling Count"

    def test_collapsed_attrs_have_lazy_charts(self, seeded):
        text = TestClient(seeded).get("/smart").text
        # attr 4 lives in the collapsed section but still gets a canvas and a
        # series payload, chart-rendered on first expand
        assert 'id="attr--dev-sda-4"' in text
        assert "HSM_DEFERRED_CHARTS" in text
        assert "'attrs--dev-sda'" in text

    def test_lvm_page_current_state(self, seeded):
        text = TestClient(seeded).get("/lvm").text
        assert "RAID/RAID" in text
        assert "raid1" in text
        assert "State Changes" in text

    def test_invalid_range_falls_back(self, seeded):
        client = TestClient(seeded)
        assert client.get("/filesystem", params={"range": "bogus"}).status_code == 200
        assert client.get("/smart", params={"range": "1y"}).status_code == 200

    def test_range_selector_rendered(self, seeded):
        text = TestClient(seeded).get("/filesystem", params={"range": "30d"}).text
        assert 'class="range-link active">30d' in text
