"""Injected UTC clock. Business logic takes a ``Clock`` instead of calling ``datetime.now()``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as an aware UTC datetime."""
        ...

    def today(self) -> date:
        """Current UTC calendar date."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().date()


@dataclass
class FixedClock:
    """Deterministic clock for tests. ``advance`` moves it forward explicitly."""

    current: datetime = field(default_factory=lambda: datetime(2026, 11, 3, 12, 0, tzinfo=UTC))

    def __post_init__(self) -> None:
        if self.current.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self.current = self.current.astimezone(UTC)

    def now(self) -> datetime:
        return self.current

    def today(self) -> date:
        return self.current.date()

    def advance(self, delta: timedelta) -> None:
        self.current = self.current + delta
