"""Data models for homelab storage monitor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from homelab_storage_monitor import __version__
from homelab_storage_monitor.timeutil import utcnow


class Status(str, Enum):
    """Check status levels."""

    OK = "OK"
    WARN = "WARN"
    CRIT = "CRIT"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return self.value

    @property
    def severity(self) -> int:
        """Return numeric severity for comparison (higher = worse).

        UNKNOWN ranks below WARN/CRIT so an unreadable check never masks
        a confirmed problem in the overall status.
        """
        return {"OK": 0, "UNKNOWN": 1, "WARN": 2, "CRIT": 3}[self.value]

    def is_problem(self) -> bool:
        """Return True if status indicates a problem."""
        return self in (Status.WARN, Status.CRIT)


class EventType(str, Enum):
    """Event types for the events table."""

    STATE_CHANGE = "state_change"
    ALERT_SENT = "alert_sent"
    RECOVERY = "recovery"
    ERROR = "error"
    SELFTEST_STARTED = "selftest_started"

    def __str__(self) -> str:
        return self.value


@dataclass
class CheckResult:
    """Result from a single check execution."""

    name: str
    status: Status
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    identifier: str = ""  # e.g., disk path, mount path for dedup key

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "status": str(self.status),
            "summary": self.summary,
            "details": self.details,
            "identifier": self.identifier,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckResult:
        """Create from dictionary."""
        return cls(
            name=data["name"],
            status=Status(data["status"]),
            summary=data["summary"],
            details=data.get("details", {}),
            identifier=data.get("identifier", ""),
        )

    @property
    def dedup_key(self) -> str:
        """Generate deduplication key for alerting."""
        if self.identifier:
            return f"{self.name}:{self.identifier}"
        return self.name


@dataclass
class Metric:
    """A single metric sample."""

    name: str
    value_num: float | None = None
    value_text: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    ts: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "value_num": self.value_num,
            "value_text": self.value_text,
            "labels": self.labels,
            "ts": self.ts.isoformat(),
        }

    @property
    def labels_json(self) -> str:
        """Return labels as JSON string."""
        return json.dumps(self.labels, sort_keys=True)


@dataclass
class RunResult:
    """Result from a complete check run."""

    hostname: str
    ts_start: datetime
    ts_end: datetime
    check_results: list[CheckResult]
    version: str = field(default_factory=lambda: __version__)

    @property
    def overall_status(self) -> Status:
        """Compute overall status as worst of all check results."""
        if not self.check_results:
            return Status.UNKNOWN
        worst = max(self.check_results, key=lambda r: r.status.severity)
        return worst.status

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "hostname": self.hostname,
            "ts_start": self.ts_start.isoformat(),
            "ts_end": self.ts_end.isoformat(),
            "overall_status": str(self.overall_status),
            "check_results": [r.to_dict() for r in self.check_results],
            "version": self.version,
        }


@dataclass
class Event:
    """An event record (state change, alert, etc.)."""

    event_type: EventType
    severity: Status
    source: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=utcnow)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "event_type": str(self.event_type),
            "severity": str(self.severity),
            "source": self.source,
            "message": self.message,
            "payload": self.payload,
            "ts": self.ts.isoformat(),
        }

    @property
    def payload_json(self) -> str:
        """Return payload as JSON string."""
        return json.dumps(self.payload)


@dataclass
class IssueState:
    """Tracks the current state of an issue for deduplication."""

    key: str  # dedup_key from CheckResult
    current_status: Status
    last_alert_ts: datetime | None = None
    last_change_ts: datetime = field(default_factory=utcnow)
    alert_count: int = 0

    def alerted_this_episode(self) -> bool:
        """True if an alert was delivered since the last status change."""
        return self.last_alert_ts is not None and self.last_alert_ts >= self.last_change_ts

    def should_alert(
        self,
        new_status: Status,
        cooldown_seconds: int = 21600,  # 6 hours default
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """
        Determine if an alert should be sent.

        An "episode" is the period since the last status change. Alerts fire
        once per episode and are retried on later runs until a delivery
        succeeds (last_alert_ts is only stamped on successful delivery).

        Returns (should_alert, reason).
        """
        now = now or utcnow()
        alerted = self.alerted_this_episode()

        # Transition to OK: recovery, but only if we actually notified about
        # the problem (or its unavailability) — otherwise it's just noise.
        if new_status == Status.OK:
            if self.current_status != Status.OK and alerted:
                return True, "recovery"
            return False, ""

        # Escalation to CRIT always alerts immediately
        if new_status == Status.CRIT and self.current_status in (Status.WARN, Status.UNKNOWN):
            return True, "escalation"

        # Data came back from UNKNOWN showing a problem
        if new_status == Status.WARN and self.current_status == Status.UNKNOWN:
            return True, "new_problem"

        # Problem not yet successfully alerted in this episode:
        # covers new problems and retries after failed deliveries
        if new_status.is_problem() and not alerted:
            reason = "new_problem" if self.current_status == Status.OK else "unnotified"
            return True, reason

        # Repeated CRIT after cooldown
        if new_status == Status.CRIT and self.last_alert_ts:
            elapsed = (now - self.last_alert_ts).total_seconds()
            if elapsed >= cooldown_seconds:
                return True, "cooldown_repeat"

        # Check unavailable for 2+ consecutive runs: the data source itself is
        # broken (a dead disk often just vanishes), which deserves an alert.
        if (
            new_status == Status.UNKNOWN
            and self.current_status == Status.UNKNOWN
            and not alerted
        ):
            return True, "check_unavailable"

        return False, ""

    def update(self, new_status: Status) -> None:
        """Update issue state after check."""
        if new_status != self.current_status:
            self.last_change_ts = utcnow()
        self.current_status = new_status

    def record_alert(self, now: datetime | None = None) -> None:
        """Record a successfully delivered alert."""
        self.last_alert_ts = now or utcnow()
        self.alert_count += 1
