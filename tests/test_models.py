"""Tests for status ordering and the alert decision logic."""

from __future__ import annotations

from datetime import timedelta

from homelab_storage_monitor.models import CheckResult, IssueState, RunResult, Status
from homelab_storage_monitor.timeutil import utcnow


class TestStatusSeverity:
    def test_crit_is_worst(self):
        assert Status.CRIT.severity > Status.WARN.severity
        assert Status.WARN.severity > Status.UNKNOWN.severity
        assert Status.UNKNOWN.severity > Status.OK.severity

    def test_unknown_does_not_mask_crit_in_overall_status(self):
        run = RunResult(
            hostname="test",
            ts_start=utcnow(),
            ts_end=utcnow(),
            check_results=[
                CheckResult(name="a", status=Status.UNKNOWN, summary="?"),
                CheckResult(name="b", status=Status.CRIT, summary="bad"),
            ],
        )
        assert run.overall_status == Status.CRIT

    def test_unknown_is_not_a_problem(self):
        assert not Status.UNKNOWN.is_problem()
        assert Status.WARN.is_problem()
        assert Status.CRIT.is_problem()


def make_state(status: Status = Status.OK, **kwargs) -> IssueState:
    return IssueState(key="test:disk", current_status=status, **kwargs)


class TestShouldAlert:
    def test_new_problem_alerts(self):
        state = make_state(Status.OK)
        should, reason = state.should_alert(Status.WARN)
        assert should and reason == "new_problem"

    def test_ongoing_problem_after_delivery_is_quiet(self):
        state = make_state(Status.OK)
        state.update(Status.WARN)
        state.record_alert()
        should, _ = state.should_alert(Status.WARN)
        assert not should

    def test_failed_delivery_is_retried(self):
        state = make_state(Status.OK)
        state.update(Status.WARN)  # no record_alert: delivery failed
        should, reason = state.should_alert(Status.WARN)
        assert should and reason == "unnotified"

    def test_escalation_alerts_even_after_warn_alert(self):
        state = make_state(Status.OK)
        state.update(Status.WARN)
        state.record_alert()
        should, reason = state.should_alert(Status.CRIT)
        assert should and reason == "escalation"

    def test_crit_repeats_after_cooldown(self):
        state = make_state(Status.OK)
        state.update(Status.CRIT)
        state.record_alert()
        # Within cooldown: quiet
        should, _ = state.should_alert(Status.CRIT, cooldown_seconds=3600)
        assert not should
        # After cooldown: repeats
        later = utcnow() + timedelta(seconds=3601)
        should, reason = state.should_alert(Status.CRIT, cooldown_seconds=3600, now=later)
        assert should and reason == "cooldown_repeat"

    def test_recovery_only_after_delivered_alert(self):
        # Alert delivered -> recovery fires
        state = make_state(Status.OK)
        state.update(Status.CRIT)
        state.record_alert()
        should, reason = state.should_alert(Status.OK)
        assert should and reason == "recovery"

        # Alert never delivered -> silent recovery
        state = make_state(Status.OK)
        state.update(Status.CRIT)
        should, _ = state.should_alert(Status.OK)
        assert not should


class TestUnknownAlerting:
    def test_first_unknown_is_tolerated(self):
        state = make_state(Status.OK)
        should, _ = state.should_alert(Status.UNKNOWN)
        assert not should

    def test_second_consecutive_unknown_alerts(self):
        state = make_state(Status.OK)
        state.update(Status.UNKNOWN)
        should, reason = state.should_alert(Status.UNKNOWN)
        assert should and reason == "check_unavailable"

    def test_unknown_alert_not_repeated_after_delivery(self):
        state = make_state(Status.OK)
        state.update(Status.UNKNOWN)
        state.record_alert()
        should, _ = state.should_alert(Status.UNKNOWN)
        assert not should

    def test_recovery_from_alerted_unknown(self):
        state = make_state(Status.OK)
        state.update(Status.UNKNOWN)
        state.record_alert()
        should, reason = state.should_alert(Status.OK)
        assert should and reason == "recovery"

    def test_transient_unknown_blip_is_silent(self):
        state = make_state(Status.OK)
        state.update(Status.UNKNOWN)
        should, _ = state.should_alert(Status.OK)
        assert not should

    def test_unknown_to_crit_alerts(self):
        state = make_state(Status.OK)
        state.update(Status.UNKNOWN)
        state.record_alert()
        should, reason = state.should_alert(Status.CRIT)
        assert should and reason == "escalation"

    def test_unknown_to_warn_alerts(self):
        state = make_state(Status.OK)
        state.update(Status.UNKNOWN)
        state.record_alert()
        should, reason = state.should_alert(Status.WARN)
        assert should and reason == "new_problem"
