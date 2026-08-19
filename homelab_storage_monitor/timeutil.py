"""Timezone-aware time helpers.

All timestamps are stored in UTC. Databases created before this change
hold naive local-time strings; parse_ts() converts those on read so
elapsed-time math never mixes naive and aware datetimes.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def parse_ts(value: str) -> datetime:
    """Parse a stored ISO timestamp, converting legacy naive values to aware."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # Legacy rows were written in local time without an offset
        dt = dt.astimezone()
    return dt
