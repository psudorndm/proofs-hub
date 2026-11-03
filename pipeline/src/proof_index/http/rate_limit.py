"""In-process request spacing plus a job time budget.

Cross-process exclusivity comes from the PostgreSQL provider advisory lock
(``proof_index.db.locks``); this module only spaces requests made by the process
that holds it. Limiters are keyed by *provider*, so both arXiv adapters share one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from proof_index.http.errors import BudgetExhausted

Sleep = Callable[[float], None]
Monotonic = Callable[[], float]


@dataclass
class Budget:
    """Wall-clock budget for one bounded job invocation (e.g. ``--max-minutes 45``)."""

    seconds: float | None
    monotonic: Monotonic = time.monotonic
    started: float = field(init=False)

    def __post_init__(self) -> None:
        self.started = self.monotonic()

    def remaining(self) -> float:
        if self.seconds is None:
            return float("inf")
        return max(0.0, self.seconds - (self.monotonic() - self.started))

    def ensure(self, wait_s: float, reserve_s: float = 0.0) -> None:
        """Raise ``BudgetExhausted`` if waiting ``wait_s`` (plus a reserve) cannot fit."""
        remaining = self.remaining()
        if wait_s + reserve_s > remaining:
            raise BudgetExhausted(wait_s, remaining)

    def nearly_exhausted(self, reserve_s: float) -> bool:
        return self.remaining() <= reserve_s


@dataclass
class RateLimiter:
    min_interval_s: float
    sleep: Sleep = time.sleep
    monotonic: Monotonic = time.monotonic
    _last: float | None = None

    def wait(self, budget: Budget | None = None) -> float:
        """Sleep until the next request is allowed; returns the seconds slept."""
        if self._last is None:
            return 0.0
        delay = self._last + self.min_interval_s - self.monotonic()
        if delay <= 0:
            return 0.0
        if budget is not None:
            budget.ensure(delay)
        self.sleep(delay)
        return delay

    def mark(self) -> None:
        self._last = self.monotonic()


class LimiterRegistry:
    """One limiter per provider name, shared by every adapter of that provider."""

    def __init__(
        self,
        intervals: dict[str, float],
        sleep: Sleep = time.sleep,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        self._intervals = dict(intervals)
        self._sleep = sleep
        self._monotonic = monotonic
        self._limiters: dict[str, RateLimiter] = {}

    def get(self, provider: str) -> RateLimiter:
        if provider not in self._limiters:
            if provider not in self._intervals:
                raise KeyError(f"no rate limit configured for provider {provider!r}")
            self._limiters[provider] = RateLimiter(
                self._intervals[provider], sleep=self._sleep, monotonic=self._monotonic
            )
        return self._limiters[provider]
