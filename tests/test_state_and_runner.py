"""Integration tests: state manager + runner alert delivery semantics."""

from __future__ import annotations

import pytest

from homelab_storage_monitor.models import CheckResult, RunResult, Status
from homelab_storage_monitor.runner import Runner
from homelab_storage_monitor.state import StateManager
from homelab_storage_monitor.timeutil import utcnow


def result_with(status: Status, name: str = "smart", identifier: str = "/dev/sda") -> CheckResult:
    return CheckResult(name=name, status=status, summary=f"{identifier}: test", identifier=identifier)


class TestStateManager:
    def test_new_problem_then_dedup(self, config, db):
        sm = StateManager(config, db)

        should, reason = sm.process_result(result_with(Status.CRIT))
        assert should and reason == "new_problem"
        sm.mark_alerted([result_with(Status.CRIT)])

        # Same problem next run: deduplicated
        should, _ = sm.process_result(result_with(Status.CRIT))
        assert not should

    def test_failed_delivery_retries_next_run(self, config, db):
        sm = StateManager(config, db)

        should, _ = sm.process_result(result_with(Status.WARN))
        assert should
        # mark_alerted NOT called: delivery failed

        should, reason = sm.process_result(result_with(Status.WARN))
        assert should and reason == "unnotified"

    def test_recovery_flow_records_events(self, config, db):
        sm = StateManager(config, db)
        sm.process_result(result_with(Status.CRIT))
        sm.mark_alerted([result_with(Status.CRIT)])

        should, reason = sm.process_result(result_with(Status.OK))
        assert should and reason == "recovery"

        events = db.get_events()
        types = [e["event_type"] for e in events]
        assert "state_change" in types and "recovery" in types

    def test_mark_alerted_increments_count(self, config, db):
        sm = StateManager(config, db)
        result = result_with(Status.CRIT)
        sm.process_result(result)
        sm.mark_alerted([result])

        state = db.get_issue_state(result.dedup_key)
        assert state.alert_count == 1
        assert state.last_alert_ts is not None


class FakeAlerter:
    def __init__(self, succeed: bool):
        self.succeed = succeed
        self.calls = 0

    def send(self, run, dashboard_url=None, is_test=False):
        self.calls += 1
        return self.succeed


@pytest.fixture
def runner(config, db):
    r = Runner(config, db)
    return r


def make_run(status: Status) -> RunResult:
    return RunResult(
        hostname="test",
        ts_start=utcnow(),
        ts_end=utcnow(),
        check_results=[result_with(status)],
    )


class TestRunnerDelivery:
    def test_successful_delivery_stamps_state(self, runner, db):
        runner.slack_alerter = FakeAlerter(succeed=True)
        runner._process_alerts(make_run(Status.CRIT))

        state = db.get_issue_state("smart:/dev/sda")
        assert state.alert_count == 1

        # Next run: deduplicated, no second send
        runner._process_alerts(make_run(Status.CRIT))
        assert runner.slack_alerter.calls == 1

    def test_failed_delivery_is_retried(self, runner, db):
        runner.slack_alerter = FakeAlerter(succeed=False)
        runner._process_alerts(make_run(Status.CRIT))

        state = db.get_issue_state("smart:/dev/sda")
        assert state.alert_count == 0

        # Next run retries the send
        runner._process_alerts(make_run(Status.CRIT))
        assert runner.slack_alerter.calls == 2

        # Delivery comes back: stamped, then quiet
        runner.slack_alerter.succeed = True
        runner._process_alerts(make_run(Status.CRIT))
        assert db.get_issue_state("smart:/dev/sda").alert_count == 1
        runner._process_alerts(make_run(Status.CRIT))
        assert runner.slack_alerter.calls == 3

    def test_one_backend_success_is_enough(self, runner, db):
        runner.slack_alerter = FakeAlerter(succeed=False)
        runner.email_alerter = FakeAlerter(succeed=True)
        runner._process_alerts(make_run(Status.CRIT))
        assert db.get_issue_state("smart:/dev/sda").alert_count == 1

    def test_no_backends_marks_handled(self, runner, db):
        assert runner.slack_alerter is None and runner.email_alerter is None
        runner._process_alerts(make_run(Status.CRIT))
        # Without backends the episode is closed rather than retried forever
        state = db.get_issue_state("smart:/dev/sda")
        assert state.alert_count == 1
