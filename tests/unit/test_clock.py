from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from proof_index.clock import FixedClock, SystemClock


def test_fixed_clock_defaults_to_spec_calendar() -> None:
    clock = FixedClock()
    assert clock.today() == date(2026, 11, 3)
    assert clock.now().tzinfo is UTC


def test_fixed_clock_normalizes_to_utc_and_advances() -> None:
    clock = FixedClock(datetime(2026, 11, 3, 23, 30, tzinfo=timezone(timedelta(hours=-2))))
    assert clock.today() == date(2026, 11, 4)
    clock.advance(timedelta(days=1))
    assert clock.today() == date(2026, 11, 5)


def test_fixed_clock_rejects_naive() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 3))  # noqa: DTZ001


def test_system_clock_is_aware() -> None:
    assert SystemClock().now().tzinfo is not None
