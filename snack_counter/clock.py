"""Time source. Tests use FakeClock so timeouts can be tested instantly."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware start time")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware")
        self._now = at
