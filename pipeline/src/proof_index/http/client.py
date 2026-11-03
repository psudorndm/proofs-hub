"""Shared provider HTTP client (spec section 7).

Defaults: connect timeout 10 s, read timeout 60 s, at most 4 attempts. Retries
connection errors/timeouts, 429, and transient 5xx; never retries other 4xx.
Honors ``Retry-After``; when a wait would exceed the job budget it raises
``BudgetExhausted`` so the caller can checkpoint. Bodies are size-bounded.
Logs carry provider, sanitized URL, status, and elapsed time only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from proof_index.clock import Clock, SystemClock
from proof_index.hashing import sha256_json
from proof_index.http.errors import (
    BudgetExhausted,
    HttpStatusError,
    ResponseTooLarge,
    TransportFailure,
)
from proof_index.http.rate_limit import Budget, Monotonic, RateLimiter, Sleep

log = logging.getLogger("proof_index.http")

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_SENSITIVE_PARAMS = frozenset(
    {
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "secret",
        "password",
        "signature",
        "x-amz-signature",
        "x-amz-credential",
        "mailto",
    }
)
_FORWARDED_HEADERS = ("content-type", "date", "last-modified", "etag", "content-length")


@dataclass(frozen=True)
class RequestSpec:
    provider: str
    url: str
    params: tuple[tuple[str, str], ...] = ()
    method: Literal["GET"] = "GET"
    accept: str | None = None
    max_bytes: int = DEFAULT_MAX_BYTES
    # Non-2xx statuses the adapter parses itself (e.g. Crossref 404 = unknown DOI item).
    ok_statuses: frozenset[int] = frozenset()

    def fingerprint(self) -> str:
        """Stable request identity (method, URL, sorted parameters)."""
        return sha256_json({"method": self.method, "url": self.url, "params": sorted(self.params)})

    def full_url(self) -> str:
        if not self.params:
            return self.url
        return f"{self.url}?{urlencode(list(self.params))}"


@dataclass(frozen=True)
class FetchedResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    provider_date: datetime | None
    fetched_at: datetime
    elapsed_s: float
    attempts: int
    sanitized_url: str
    fingerprint: str = field(default="")

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")


def sanitize_url(url: str) -> str:
    """Drop userinfo and redact sensitive query parameter values."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    query = urlencode(
        [
            (k, "REDACTED" if k.lower() in _SENSITIVE_PARAMS else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def _provider_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class ProviderHttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        limiter: RateLimiter,
        budget: Budget | None = None,
        transport: httpx.BaseTransport | None = None,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 60.0,
        max_attempts: int = 4,
        backoff_base_s: float = 2.0,
        sleep: Sleep = time.sleep,
        monotonic: Monotonic = time.monotonic,
        clock: Clock | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.limiter = limiter
        self.budget = budget
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._clock: Clock = clock or SystemClock()
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(read_timeout_s, connect=connect_timeout_s),
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            max_redirects=5,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ProviderHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.budget is not None:
            self.budget.ensure(seconds)
        self._sleep(seconds)

    def fetch(self, spec: RequestSpec) -> FetchedResponse:
        url = spec.full_url()
        safe = sanitize_url(url)
        headers = {"Accept": spec.accept} if spec.accept else {}
        last_error: str = "no attempt made"
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.wait(self.budget)
            started = self._monotonic()
            self.limiter.mark()
            try:
                status, body, resp_headers = self._send(spec, url, headers)
            except ResponseTooLarge:
                raise
            except httpx.ProxyError as exc:
                # A proxy refusal (e.g. CONNECT 403 from an egress policy) is a policy
                # denial, not a transient fault: never retry it.
                log.warning(
                    "http provider=%s error=ProxyError attempt=%d url=%s (not retried)",
                    spec.provider,
                    attempt,
                    safe,
                )
                raise TransportFailure(
                    f"proxy refused the connection to {safe} ({exc}); not retried"
                ) from None
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                elapsed = self._monotonic() - started
                last_error = type(exc).__name__
                log.warning(
                    "http provider=%s error=%s attempt=%d elapsed=%.2fs url=%s",
                    spec.provider,
                    last_error,
                    attempt,
                    elapsed,
                    safe,
                )
                if attempt == self.max_attempts:
                    raise TransportFailure(
                        f"{last_error} from {safe} after {attempt} attempt(s)"
                    ) from None
                self._wait(self.backoff_base_s * 2 ** (attempt - 1))
                continue
            elapsed = self._monotonic() - started
            log.info(
                "http provider=%s status=%d attempt=%d elapsed=%.2fs bytes=%d url=%s",
                spec.provider,
                status,
                attempt,
                elapsed,
                len(body),
                safe,
            )
            if 200 <= status < 300 or status in spec.ok_statuses:
                return FetchedResponse(
                    status=status,
                    body=body,
                    headers=resp_headers,
                    provider_date=_provider_date(resp_headers.get("date")),
                    fetched_at=self._clock.now(),
                    elapsed_s=elapsed,
                    attempts=attempt,
                    sanitized_url=safe,
                    fingerprint=spec.fingerprint(),
                )
            if status not in RETRY_STATUSES or attempt == self.max_attempts:
                raise HttpStatusError(status, safe, attempt)
            retry_after = parse_retry_after(resp_headers.get("retry-after"), self._clock.now())
            delay = (
                retry_after if retry_after is not None else self.backoff_base_s * 2 ** (attempt - 1)
            )
            try:
                self._wait(delay)
            except BudgetExhausted:
                log.warning(
                    "http provider=%s status=%d retry_after=%.1fs exceeds budget url=%s",
                    spec.provider,
                    status,
                    delay,
                    safe,
                )
                raise
        raise TransportFailure(f"{last_error} from {safe}")  # pragma: no cover

    def _send(
        self, spec: RequestSpec, url: str, headers: dict[str, str]
    ) -> tuple[int, bytes, dict[str, str]]:
        with self._client.stream(spec.method, url, headers=headers) as response:
            resp_headers = {
                k: response.headers[k]
                for k in (*_FORWARDED_HEADERS, "retry-after")
                if k in response.headers
            }
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > spec.max_bytes:
                raise ResponseTooLarge(
                    f"declared size {declared} exceeds limit {spec.max_bytes} for "
                    f"{sanitize_url(url)}"
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > spec.max_bytes:
                    raise ResponseTooLarge(
                        f"response exceeds limit {spec.max_bytes} bytes for {sanitize_url(url)}"
                    )
                chunks.append(chunk)
            return response.status_code, b"".join(chunks), resp_headers
