from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from botocore.stub import Stubber

from proof_index.clock import FixedClock
from proof_index.errors import StorageError
from proof_index.hashing import sha256_hex
from proof_index.http.client import (
    ProviderHttpClient,
    RequestSpec,
    parse_retry_after,
    sanitize_url,
)
from proof_index.http.errors import (
    BudgetExhausted,
    HttpStatusError,
    ResponseTooLarge,
    TransportFailure,
)
from proof_index.http.rate_limit import Budget, LimiterRegistry, RateLimiter
from proof_index.storage.base import raw_response_key
from proof_index.storage.local import LocalBlobStore
from proof_index.storage.s3 import S3BlobStore

# --- local blob store -----------------------------------------------------------


def test_local_put_get_and_idempotent_put(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    body = b"<OAI-PMH/>"
    digest = sha256_hex(body)
    key = raw_response_key("arxiv", digest)
    ref = store.put_if_absent(key, body, digest)
    assert ref.created and ref.size == len(body)
    again = store.put_if_absent(key, body, digest)
    assert not again.created
    assert store.get(key) == body
    assert store.exists(key, digest)
    assert not store.exists(key, "0" * 64)


def test_local_rejects_hash_mismatch_and_overwrite(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    with pytest.raises(StorageError, match="mismatch"):
        store.put_if_absent("a/b", b"x", sha256_hex(b"y"))
    store.put_if_absent("a/b", b"x", sha256_hex(b"x"))
    with pytest.raises(StorageError, match="different content"):
        store.put_if_absent("a/b", b"z", sha256_hex(b"z"))
    assert store.get("a/b") == b"x"


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/abs", "a//b", ".hidden", ""])
def test_local_rejects_unsafe_keys(tmp_path: Path, bad: str) -> None:
    with pytest.raises(StorageError):
        LocalBlobStore(tmp_path).put_if_absent(bad, b"x", sha256_hex(b"x"))


def test_local_missing_blob(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="not found"):
        LocalBlobStore(tmp_path).get("nope")


# --- S3 blob store (botocore Stubber; no network) ----------------------------------------


def _s3() -> tuple[S3BlobStore, Stubber]:
    import boto3

    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",  # noqa: S106 - stub credentials, never sent
    )
    return S3BlobStore("bucket", prefix="p", client=client), Stubber(client)


def test_s3_put_if_absent_creates_with_condition() -> None:
    store, stub = _s3()
    body = b"data"
    digest = sha256_hex(body)
    stub.add_client_error("head_object", "404", http_status_code=404)
    stub.add_response(
        "put_object",
        {},
        {
            "Bucket": "bucket",
            "Key": "p/k",
            "Body": body,
            "Metadata": {"sha256": digest},
            "IfNoneMatch": "*",
        },
    )
    with stub:
        ref = store.put_if_absent("k", body, digest)
    assert ref.created
    stub.assert_no_pending_responses()


def test_s3_existing_different_content_is_error() -> None:
    store, stub = _s3()
    stub.add_response("head_object", {"Metadata": {"sha256": "other"}, "ContentLength": 4})
    with stub, pytest.raises(StorageError, match="different content"):
        store.put_if_absent("k", b"data", sha256_hex(b"data"))


# --- rate limiting and budget ------------------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(round(s, 3))
        self.t += s


def test_limiter_spaces_requests() -> None:
    ft = FakeTime()
    limiter = RateLimiter(3.1, sleep=ft.sleep, monotonic=ft.monotonic)
    limiter.wait()
    limiter.mark()
    ft.t += 1.0
    limiter.wait()
    assert ft.slept == [2.1]


def test_registry_shares_limiter_per_provider() -> None:
    reg = LimiterRegistry({"arxiv": 3.1})
    assert reg.get("arxiv") is reg.get("arxiv")
    with pytest.raises(KeyError):
        reg.get("unknown")


def test_budget() -> None:
    ft = FakeTime()
    budget = Budget(60, monotonic=ft.monotonic)
    budget.ensure(30)
    ft.t += 50
    with pytest.raises(BudgetExhausted):
        budget.ensure(30)
    assert budget.nearly_exhausted(15)
    assert Budget(None).remaining() == float("inf")


# --- HTTP client ---------------------------------------------------------------------------


def _client(
    handler: httpx.MockTransport, ft: FakeTime, budget: Budget | None = None
) -> ProviderHttpClient:
    return ProviderHttpClient(
        user_agent="test-agent",
        limiter=RateLimiter(0.0, sleep=ft.sleep, monotonic=ft.monotonic),
        budget=budget,
        transport=handler,
        sleep=ft.sleep,
        monotonic=ft.monotonic,
        clock=FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC)),
    )


SPEC = RequestSpec(provider="arxiv", url="https://example.org/oai", params=(("verb", "Identify"),))


def test_success_returns_body_and_fingerprint() -> None:
    ft = FakeTime()
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ok", headers={"Date": "Tue, 03 Nov 2026 12:00:00 GMT"})

    resp = _client(httpx.MockTransport(handle), ft).fetch(SPEC)
    assert resp.body == b"ok" and resp.attempts == 1
    assert resp.provider_date == datetime(2026, 11, 3, 12, tzinfo=UTC)
    assert resp.fingerprint == SPEC.fingerprint()
    assert seen[0].headers["user-agent"] == "test-agent"
    assert str(seen[0].url) == "https://example.org/oai?verb=Identify"


def test_retries_503_then_succeeds_with_backoff() -> None:
    ft = FakeTime()
    statuses = iter([503, 503, 200])

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), content=b"x")

    resp = _client(httpx.MockTransport(handle), ft).fetch(SPEC)
    assert resp.attempts == 3
    assert ft.slept == [2.0, 4.0]


def test_honors_retry_after_seconds() -> None:
    ft = FakeTime()
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, content=b"x")]
    )
    resp = _client(httpx.MockTransport(lambda r: next(responses)), ft).fetch(SPEC)
    assert resp.status == 200
    assert ft.slept == [7.0]


def test_retry_after_beyond_budget_checkpoints() -> None:
    ft = FakeTime()
    budget = Budget(60, monotonic=ft.monotonic)
    handler = httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(BudgetExhausted):
        _client(handler, ft, budget).fetch(SPEC)
    assert ft.slept == []


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_retryable_status_fails_once(status: int) -> None:
    ft = FakeTime()
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(HttpStatusError) as info:
        _client(httpx.MockTransport(handle), ft).fetch(SPEC)
    assert info.value.status == status
    assert info.value.exit_code == 3
    assert len(calls) == 1


def test_gives_up_after_four_attempts() -> None:
    ft = FakeTime()
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(HttpStatusError):
        _client(httpx.MockTransport(handle), ft).fetch(SPEC)
    assert len(calls) == 4


def test_connect_error_retried_then_transport_failure() -> None:
    ft = FakeTime()

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TransportFailure):
        _client(httpx.MockTransport(handle), ft).fetch(SPEC)
    assert ft.slept == [2.0, 4.0, 8.0]


def test_response_size_limit() -> None:
    ft = FakeTime()
    spec = RequestSpec(provider="arxiv", url="https://example.org/big", max_bytes=10)
    handler = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 11))
    with pytest.raises(ResponseTooLarge):
        _client(handler, ft).fetch(spec)


def test_logs_are_sanitized(caplog: pytest.LogCaptureFixture) -> None:
    ft = FakeTime()
    spec = RequestSpec(
        provider="crossref",
        url="https://user:pw@api.example.org/works",
        params=(("mailto", "someone@example.org"), ("rows", "1")),
    )
    handler = httpx.MockTransport(lambda r: httpx.Response(200, content=b"{}"))
    with caplog.at_level(logging.INFO, logger="proof_index.http"):
        resp = _client(handler, ft).fetch(spec)
    text = caplog.text + resp.sanitized_url
    assert "someone@example.org" not in text
    assert "pw" not in text.replace("api", "")
    assert "rows=1" in text


def test_sanitize_and_retry_after_parsing() -> None:
    assert sanitize_url("https://a:b@h.org/p?token=s&x=1") == "https://h.org/p?token=REDACTED&x=1"
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    assert parse_retry_after("120", now) == 120.0
    assert parse_retry_after("Tue, 03 Nov 2026 12:01:00 GMT", now) == 60.0
    assert parse_retry_after("garbage", now) is None


def test_fingerprint_ignores_param_order() -> None:
    a = RequestSpec(provider="x", url="https://h.org", params=(("a", "1"), ("b", "2")))
    b = RequestSpec(provider="x", url="https://h.org", params=(("b", "2"), ("a", "1")))
    assert a.fingerprint() == b.fingerprint()
