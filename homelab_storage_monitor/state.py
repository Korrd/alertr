"""Alert deduplication and state management."""

from __future__ import annotations

import logging

from homelab_storage_monitor.config import Config
from homelab_storage_monitor.db import Database
from homelab_storage_monitor.models import (
    CheckResult,
    Event,
    EventType,
    IssueState,
    Status,
)
from homelab_storage_monitor.timeutil import utcnow

logger = logging.getLogger(__name__)


class StateManager:
    """Manages issue state for alert deduplication."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.cooldown_seconds = config.alerts.dedupe_cooldown_seconds

    def process_result(self, result: CheckResult) -> tuple[bool, str]:
        """
        Process a check result and determine if alert should be sent.

        Returns (should_alert, reason).
        """
        key = result.dedup_key

        # Get current state from DB
        state = self.db.get_issue_state(key)

        if state is None:
            # First time seeing this check
            state = IssueState(
                key=key,
                current_status=Status.OK,
                last_change_ts=utcnow(),
            )

        # Determine if we should alert
        should_alert, reason = state.should_alert(
            result.status,
            cooldown_seconds=self.cooldown_seconds,
        )

        # Log state change events
        if result.status != state.current_status:
            self._record_state_change(result, state.current_status)

        # Update status only; last_alert_ts is stamped by mark_alerted()
        # once a delivery actually succeeds, so failed sends get retried.
        state.update(result.status)
        self.db.save_issue_state(state)

        return should_alert, reason

    def mark_alerted(self, results: list[CheckResult]) -> None:
        """Record successful alert delivery for the given results."""
        now = utcnow()
        for result in results:
            state = self.db.get_issue_state(result.dedup_key)
            if state is None:
                continue
            state.record_alert(now)
            self.db.save_issue_state(state)

    def _record_state_change(
        self,
        result: CheckResult,
        old_status: Status,
    ) -> None:
        """Record a state change event."""
        if result.status == Status.OK and old_status.is_problem():
            event_type = EventType.RECOVERY
            message = f"Recovered: {result.name}"
        elif result.status.is_problem() and old_status == Status.OK:
            event_type = EventType.STATE_CHANGE
            message = f"New issue: {result.name} - {result.summary}"
        else:
            event_type = EventType.STATE_CHANGE
            message = f"Status change: {result.name} {old_status} -> {result.status}"

        event = Event(
            event_type=event_type,
            severity=result.status,
            source=result.name,
            message=message,
            payload={
                "check_name": result.name,
                "identifier": result.identifier,
                "old_status": str(old_status),
                "new_status": str(result.status),
                "summary": result.summary,
            },
        )

        self.db.save_event(event)

    def record_alert_sent(
        self,
        results: list[CheckResult],
        backend: str,
        success: bool,
    ) -> None:
        """Record that an alert was sent."""
        summaries = [f"[{r.status}] {r.name}: {r.summary}" for r in results if r.status.is_problem()]

        event = Event(
            event_type=EventType.ALERT_SENT,
            severity=max((r.status for r in results), key=lambda s: s.severity, default=Status.OK),
            source=backend,
            message=f"Alert sent via {backend}" if success else f"Alert failed via {backend}",
            payload={
                "backend": backend,
                "success": success,
                "summaries": summaries,
            },
        )

        self.db.save_event(event)
