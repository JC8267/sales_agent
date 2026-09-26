from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class AdjustableClock:
    """System time by default; pin it with set() for tests, demos, and scheduler replays."""

    def __init__(self, at: datetime | None = None):
        self._at = at.astimezone(timezone.utc) if at else None

    def now(self) -> datetime:
        return self._at or datetime.now(timezone.utc)

    def set(self, at: datetime | None) -> None:
        self._at = at.astimezone(timezone.utc) if at else None


def utc_iso(dt: datetime) -> str:
    """Canonical UTC timestamp string; lexicographic order == chronological order."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
