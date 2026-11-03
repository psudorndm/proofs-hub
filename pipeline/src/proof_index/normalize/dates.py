"""Parse provider date strings into ``PartialDate`` without inventing precision."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from proof_index.models.dates import PartialDate


class DateParseError(ValueError):
    """The source value is not a plausible date; quarantine instead of clamping."""


_ISO_PARTIAL = re.compile(
    r"^(?P<y>\d{4})(?:-(?P<m>\d{1,2})(?:-(?P<d>\d{1,2}))?)?"
    r"(?:[T ](?P<time>\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(?P<tz>Z|[+-]\d{2}:?\d{2})?)?$"
)


def parse_partial_date(value: str | None) -> PartialDate:
    """``2020`` -> year, ``2020-05`` -> month, ``2020-05-17`` (or with time) -> day.

    ``None`` or empty -> unknown. Anything else raises ``DateParseError``.
    A timestamp with an offset is converted to the UTC calendar date.
    """
    if value is None or not value.strip():
        return PartialDate.unknown()
    text = value.strip()
    match = _ISO_PARTIAL.match(text)
    if not match:
        raise DateParseError(f"unrecognized date format: {text[:40]!r}")
    year = int(match["y"])
    try:
        if match["time"] and match["d"]:
            parsed = parse_timestamp(text)
            assert parsed is not None
            return PartialDate.of_day(parsed.date())
        if match["d"]:
            return PartialDate.of_day(date(year, int(match["m"]), int(match["d"])))
        if match["m"]:
            return PartialDate.of_month(year, int(match["m"]))
        return PartialDate.of_year(year)
    except ValueError as exc:  # includes pydantic ValidationError
        raise DateParseError(f"implausible date value: {text[:40]!r}") from exc


def from_parts(
    year: int | str | None, month: int | str | None = None, day: int | str | None = None
) -> PartialDate:
    """Build from separate parts (e.g. Crossref ``date-parts``). Missing tail -> lower precision."""

    def _int(v: int | str | None) -> int | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return int(v)
        except (TypeError, ValueError) as exc:
            raise DateParseError(f"non-numeric date part: {v!r}") from exc

    y, m, d = _int(year), _int(month), _int(day)
    try:
        if y is None:
            if m is not None or d is not None:
                raise DateParseError("month/day without a year")
            return PartialDate.unknown()
        if m is None:
            if d is not None:
                raise DateParseError("day without a month")
            return PartialDate.of_year(y)
        if d is None:
            return PartialDate.of_month(y, m)
        return PartialDate.of_day(date(y, m, d))
    except DateParseError:
        raise
    except ValueError as exc:
        raise DateParseError(f"implausible date parts: {y}-{m}-{d}") from exc


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp into an aware UTC datetime.

    A naive timestamp is interpreted as UTC (OAI-PMH datestamps are UTC by protocol).
    A bare date becomes midnight UTC.
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DateParseError(f"unrecognized timestamp: {value[:40]!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
