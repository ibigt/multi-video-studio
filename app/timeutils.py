"""Small, dependency-free time helpers.

Database timestamps are stored as naive UTC (SQLite has no timezone type).
They are serialised as explicit RFC 3339 UTC strings at the API boundary so
browsers never have to guess the timezone.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return naive UTC, suitable for the database columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def local_timestamp() -> str:
    """Timestamp used in auto-generated recording filenames (server local time)."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")
