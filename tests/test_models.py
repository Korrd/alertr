"""Tests for data models."""

from datetime import datetime, timedelta

import pytest

from homelab_storage_monitor.models import (
    CheckResult,
    Event,
    EventType,
    IssueState,
    Metric,
    RunResult,
    Status,
)


class TestStatus:
    """Tests for Status enum."""

    def test_severity_order(self):
        """Test that severity increases correctly."""
        assert Status.OK.severity < Status.WARN.severity
        assert Status.WARN.severity < Status.CRIT.severity

    def test_is_problem(self):
        """Test is_problem detection."""
        assert not Status.OK.is_problem()
        assert Status.WARN.is_problem()
        assert Status.CRIT.is_problem()
        assert not Status.UNKNOWN.is_problem()


class TestCheckResult:
    """Tests for CheckResult dataclass."""

    def test_dedup_key_with_identifier(self):
        """Test dedup key generation with identifier."""
        result = CheckResult(
            name="smart",
            status=Status.OK,
            summary="All good",
            identifier="/dev/sda",
        )
        assert result.dedup_key == "smart:/dev/sda"

    def test_dedup_key_without_identifier(self):
        """Test dedup key generation without identifier."""
        result = CheckResult(
            name="journal",
            status=Status.OK,
            summary="No errors",
        )
        assert result.dedup_key == "journal"

    def test_to_dict(self):
        """Test serialization to dict."""
        result = CheckResult(
            name="test",
            status=Status.WARN,
            summary="Test warning",
            details={"key": "value"},
        )
        d = result.to_dict()

        assert d["name"] == "test"
        assert d["status"] == "WARN"
        assert d["summary"] == "Test warning"
        assert d["details"] == {"key": "value"}


class TestRunResult:
    """Tests for RunResult dataclass."""

    def test_overall_status_worst(self):
        """Test that overall status is the worst of all checks."""
        run = RunResult(
            hostname="test",
            ts_start=datetime.now(),
            ts_end=datetime.now(),
            check_results=[
                CheckResult(name="a", status=Status.OK, summary="ok"),
                CheckResult(name="b", status=Status.CRIT, summary="crit"),
                CheckResult(name="c", status=Status.WARN, summary="warn"),
            ],
        )
        assert run.overall_status == Status.CRIT

    def test_overall_status_empty(self):
        """Test overall status with no check results."""
        run = RunResult(
            hostname="test",
            ts_start=datetime.now(),
            ts_end=datetime.now(),
            check_results=[],
        )
        assert run.overall_status == Status.UNKNOWN


class TestIssueState:
    """Tests for IssueState and deduplication logic."""

    def test_should_alert_new_problem(self):
        """Test alert on new problem."""
        state = IssueState(
            key="test",
            current_status=Status.OK,
        )
        should, reason = state.should_alert(Status.CRIT)

        assert should is True
        assert reason == "new_problem"

    def test_should_alert_escalation(self):
        """Test alert on escalation from WARN to CRIT."""
        state = IssueState(
            key="test",
            current_status=Status.WARN,
        )
        should, reason = state.should_alert(Status.CRIT)

        assert should is True
        assert reason == "escalation"

    def test_should_alert_recovery(self):
        """Test alert on recovery."""
        state = IssueState(
            key="test",
            current_status=Status.CRIT,
        )
        should, reason = state.should_alert(Status.OK)

        assert should is True
        assert reason == "recovery"

    def test_should_not_alert_same_status(self):
        """Test no alert when status unchanged."""
        state = IssueState(
            key="test",
            current_status=Status.WARN,
            last_alert_ts=datetime.now(),
        )
        should, reason = state.should_alert(Status.WARN)

        assert should is False

    def test_should_alert_cooldown_repeat(self):
        """Test alert after cooldown for repeated CRIT."""
        old_alert = datetime.now() - timedelta(hours=7)
        state = IssueState(
            key="test",
            current_status=Status.CRIT,
            last_alert_ts=old_alert,
        )
        should, reason = state.should_alert(Status.CRIT, cooldown_seconds=21600)

        assert should is True
        assert reason == "cooldown_repeat"

    def test_should_not_alert_within_cooldown(self):
        """Test no alert within cooldown period."""
        recent_alert = datetime.now() - timedelta(hours=1)
        state = IssueState(
            key="test",
            current_status=Status.CRIT,
            last_alert_ts=recent_alert,
        )
        should, reason = state.should_alert(Status.CRIT, cooldown_seconds=21600)

        assert should is False
