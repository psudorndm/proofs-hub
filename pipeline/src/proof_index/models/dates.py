"""Partial publication dates and their window/filter semantics (spec 5.1 and 12)."""

from __future__ import annotations

import calendar
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

Precision = Literal["day", "month", "year", "unknown"]
PRECISION_RANK: dict[str, int] = {"day": 0, "month": 1, "year": 2, "unknown": 3}


class PartialDate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int | None
    month: int | None
    day: int | None
    precision: Precision

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        y, m, d, p = self.year, self.month, self.day, self.precision
        if p == "unknown":
            if (y, m, d) != (None, None, None):
                raise ValueError("unknown precision requires year, month, and day to be null")
            return self
        if y is None or not 1 <= y <= 9999:
            raise ValueError("year must be within 1..9999")
        if p == "year":
            if m is not None or d is not None:
                raise ValueError("year precision requires null month and day")
            return self
        if m is None or not 1 <= m <= 12:
            raise ValueError("month must be within 1..12")
        if p == "month":
            if d is not None:
                raise ValueError("month precision requires null day")
            return self
        if d is None or not 1 <= d <= calendar.monthrange(y, m)[1]:
            raise ValueError("day is not valid for this year and month")
        return self

    # -- constructors ----------------------------------------------------------

    @classmethod
    def unknown(cls) -> PartialDate:
        return cls(year=None, month=None, day=None, precision="unknown")

    @classmethod
    def of_year(cls, year: int) -> PartialDate:
        return cls(year=year, month=None, day=None, precision="year")

    @classmethod
    def of_month(cls, year: int, month: int) -> PartialDate:
        return cls(year=year, month=month, day=None, precision="month")

    @classmethod
    def of_day(cls, value: date) -> PartialDate:
        return cls(year=value.year, month=value.month, day=value.day, precision="day")

    # -- semantics ---------------------------------------------------------------

    def date_interval(self) -> tuple[date, date] | None:
        """Inclusive earliest/latest possible dates, or ``None`` when unknown.

        A computational representation only; never exported as an exact date.
        """
        if self.precision == "unknown":
            return None
        assert self.year is not None
        if self.precision == "year":
            return date(self.year, 1, 1), date(self.year, 12, 31)
        assert self.month is not None
        if self.precision == "month":
            last = calendar.monthrange(self.year, self.month)[1]
            return date(self.year, self.month, 1), date(self.year, self.month, last)
        assert self.day is not None
        exact = date(self.year, self.month, self.day)
        return exact, exact

    def exact(self) -> date | None:
        if self.precision != "day":
            return None
        assert self.year is not None and self.month is not None and self.day is not None
        return date(self.year, self.month, self.day)

    def token(self) -> str:
        """Search facet token: ``d:YYYY-MM-DD``, ``m:YYYY-MM``, ``y:YYYY``, ``unknown``."""
        if self.precision == "unknown":
            return "unknown"
        if self.precision == "year":
            return f"y:{self.year:04d}"
        if self.precision == "month":
            return f"m:{self.year:04d}-{self.month:02d}"
        return f"d:{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def iso(self) -> str | None:
        """ISO 8601 reduced-precision text (``1985``, ``2026-11``, ``2026-11-03``)."""
        tok = self.token()
        return None if tok == "unknown" else tok[2:]

    def display(self) -> str:
        if self.precision == "unknown":
            return "Date unknown"
        if self.precision == "year":
            return f"{self.year:04d} · publication year only"
        if self.precision == "month":
            assert self.month is not None
            return f"{calendar.month_name[self.month]} {self.year:04d} · month only"
        exact = self.exact()
        assert exact is not None
        return exact.isoformat()


def age_days(value: PartialDate, today: date) -> int | None:
    """``(today - d).days`` for day-precision dates; ``None`` otherwise."""
    exact = value.exact()
    if exact is None:
        return None
    return (today - exact).days


def in_recent_window(value: PartialDate, today: date, days: int) -> bool:
    """Last-N-days membership: ``0 <= age < days``; only day-precision dates qualify."""
    age = age_days(value, today)
    return age is not None and 0 <= age < days


def is_future(value: PartialDate, today: date) -> bool:
    """True when even the earliest possible date is after ``today``."""
    interval = value.date_interval()
    return interval is not None and interval[0] > today


def bucket(value: PartialDate, today: date) -> Literal["0-6", "7-29", "30-364"] | None:
    """Disjoint recency buckets used when three windows are shown together."""
    age = age_days(value, today)
    if age is None or age < 0:
        return None
    if age < 7:
        return "0-6"
    if age < 30:
        return "7-29"
    if age < 365:
        return "30-364"
    return None


def overlaps(value: PartialDate, start: date, end: date) -> bool:
    """Interval-overlap semantics for range filters. Unknown dates never overlap."""
    interval = value.date_interval()
    if interval is None:
        return False
    return interval[0] <= end and interval[1] >= start


def overlaps_years(value: PartialDate, year_from: int | None, year_to: int | None) -> bool:
    start = date(year_from if year_from is not None else 1, 1, 1)
    end = date(year_to if year_to is not None else 9999, 12, 31)
    return overlaps(value, start, end)


def date_issues(value: PartialDate, today: date) -> list[str]:
    """Metadata issue codes for a source date (future dates are flagged, never clamped)."""
    issues: list[str] = []
    if is_future(value, today):
        issues.append("future_publication_date")
    return issues
