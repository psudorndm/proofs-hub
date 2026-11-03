from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from proof_index.models.dates import (
    PartialDate,
    bucket,
    date_issues,
    in_recent_window,
    is_future,
    overlaps,
    overlaps_years,
)
from proof_index.normalize.dates import (
    DateParseError,
    from_parts,
    parse_partial_date,
    parse_timestamp,
)

TODAY = date(2026, 11, 3)  # spec section 12 test calendar


def windows(pd: PartialDate) -> set[int]:
    return {n for n in (7, 30, 365) if in_recent_window(pd, TODAY, n)}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-03", {7, 30, 365}),
        ("2026-10-28", {7, 30, 365}),
        ("2026-10-27", {30, 365}),
        ("2026-10-05", {30, 365}),
        ("2026-10-04", {365}),
        ("2026-11-04", set()),
        ("1985", set()),
        ("2026-11", set()),
    ],
)
def test_spec_calendar_table(value: str, expected: set[int]) -> None:
    assert windows(parse_partial_date(value)) == expected


def test_recent_window_uses_zero_through_six_days() -> None:
    assert in_recent_window(parse_partial_date("2026-10-28"), TODAY, 7)  # age 6
    assert not in_recent_window(parse_partial_date("2026-10-27"), TODAY, 7)  # age 7


def test_future_date_does_not_enter_recent_results() -> None:
    future = parse_partial_date("2026-11-04")
    assert windows(future) == set()
    assert is_future(future, TODAY)
    assert date_issues(future, TODAY) == ["future_publication_date"]
    assert bucket(future, TODAY) is None


def test_month_in_progress_is_not_future() -> None:
    assert not is_future(PartialDate.of_month(2026, 11), TODAY)
    assert is_future(PartialDate.of_month(2026, 12), TODAY)


def test_year_only_date_is_not_fabricated_as_january_first() -> None:
    pd = parse_partial_date("1985")
    assert pd.precision == "year"
    assert pd.month is None and pd.day is None
    assert pd.exact() is None
    assert pd.iso() == "1985"
    assert pd.token() == "y:1985"
    assert pd.display() == "1985 · publication year only"
    # The interval is computational only.
    assert pd.date_interval() == (date(1985, 1, 1), date(1985, 12, 31))


def test_archive_year_overlap() -> None:
    assert overlaps_years(parse_partial_date("1985"), 1985, 1985)
    assert overlaps_years(parse_partial_date("2026-11"), 2026, 2026)
    assert not overlaps_years(parse_partial_date("1985"), 1986, 2026)
    assert not overlaps_years(PartialDate.unknown(), None, None)


def test_partial_date_range_uses_documented_overlap_semantics() -> None:
    month = PartialDate.of_month(2026, 10)
    assert overlaps(month, date(2026, 10, 31), date(2026, 11, 3))
    assert not overlaps(month, date(2026, 11, 1), date(2026, 11, 3))


def test_buckets_are_disjoint() -> None:
    assert bucket(parse_partial_date("2026-10-28"), TODAY) == "0-6"
    assert bucket(parse_partial_date("2026-10-27"), TODAY) == "7-29"
    assert bucket(parse_partial_date("2026-10-04"), TODAY) == "30-364"
    assert bucket(parse_partial_date("2025-11-03"), TODAY) is None


def test_unknown_date() -> None:
    pd = parse_partial_date(None)
    assert pd.precision == "unknown"
    assert pd.date_interval() is None
    assert pd.token() == "unknown"
    assert windows(pd) == set()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"year": 1985, "month": None, "day": None, "precision": "unknown"},
        {"year": 1985, "month": 3, "day": None, "precision": "year"},
        {"year": 2026, "month": 2, "day": 30, "precision": "day"},
        {"year": 0, "month": None, "day": None, "precision": "year"},
        {"year": 10000, "month": None, "day": None, "precision": "year"},
        {"year": 2026, "month": 13, "day": None, "precision": "month"},
        {"year": 2026, "month": 1, "day": 5, "precision": "month"},
    ],
)
def test_invalid_partial_dates_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PartialDate(**kwargs)  # type: ignore[arg-type]


def test_leap_day_and_historical_years() -> None:
    assert parse_partial_date("2024-02-29").precision == "day"
    assert PartialDate.of_year(1).year == 1


@pytest.mark.parametrize("bad", ["2026-13-01", "2023-02-29", "Spring 2020", "20201", "2020/05"])
def test_implausible_values_raise_instead_of_clamping(bad: str) -> None:
    with pytest.raises(DateParseError):
        parse_partial_date(bad)


def test_timestamp_with_offset_converts_to_utc_date() -> None:
    assert parse_partial_date("2026-11-03T23:30:00-02:00").exact() == date(2026, 11, 4)
    ts = parse_timestamp("2026-11-03T10:00:00Z")
    assert ts is not None and ts.utcoffset() is not None
    naive = parse_timestamp("2026-11-03T10:00:00")
    assert naive is not None and naive.utcoffset() is not None


def test_from_parts() -> None:
    assert from_parts(2020).precision == "year"
    assert from_parts(2020, 5).precision == "month"
    assert from_parts("2020", "5", "7").exact() == date(2020, 5, 7)
    assert from_parts(None).precision == "unknown"
    with pytest.raises(DateParseError):
        from_parts(None, 5)
    with pytest.raises(DateParseError):
        from_parts(2020, 2, 31)
