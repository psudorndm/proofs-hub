"""Adapter contracts (spec section 7).

Adapters plan and parse; the shared HTTP client sends requests; the runner
stores and promotes. Adapters never write SQL and parsers never call the network.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from proof_index.errors import DataValidationError, ProviderError
from proof_index.http.client import RequestSpec
from proof_index.models.source import NormalizedRecord, ParseFailure, SourceName, Tombstone

Cursor = dict[str, Any]


class PageStructureError(DataValidationError):
    """The whole response is unusable (wrong document, truncated, protocol shape broken)."""

    code = "page_structure"


class ProtocolError(ProviderError):
    """A provider-reported protocol error (e.g. OAI ``badArgument``)."""

    code = "protocol_error"

    def __init__(self, protocol_code: str, message: str) -> None:
        super().__init__(f"{protocol_code}: {message}")
        self.protocol_code = protocol_code


class ExpiredCursor(ProtocolError):
    """The resumption cursor is no longer valid; the runner replays the window."""

    code = "expired_cursor"


class HarvestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_key: str
    mode: Literal["sync", "backfill", "verify", "reconcile"]
    window_from: datetime | None = None
    window_until: datetime | None = None
    max_pages: int | None = None  # verification bounds only; never a coverage cutoff
    params: dict[str, Any] = Field(default_factory=dict)  # fixed per run (stored on the run)


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completed_watermark: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class StreamPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: SourceName
    adapter: str
    stream_key: str
    base_url: str
    params: dict[str, Any] = Field(default_factory=dict)
    window_from: datetime | None = None
    window_until: datetime | None = None
    max_bytes: int = 50 * 1024 * 1024


class ParseContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: SourceName
    stream_key: str
    parser_version: str
    fetched_at: datetime
    plan_params: dict[str, Any] = Field(default_factory=dict)
    cursor: Cursor | None = None
    http_status: int = 200


class ParsedPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    records: list[NormalizedRecord | Tombstone]
    next_cursor: Cursor | None
    terminal: bool
    provider_response_at: datetime | None
    reported_total: int | None
    quarantined_items: list[ParseFailure] = Field(default_factory=list)
    # Items intentionally skipped by a documented local filter (e.g. arXiv category
    # filter inside an enclosing set). Counted for coverage, not quarantined.
    filtered_count: int = 0
    no_records_match: bool = False

    @model_validator(mode="after")
    def _terminal_agrees_with_cursor(self) -> Self:
        if self.terminal != (self.next_cursor is None):
            raise ValueError("ParsedPage.terminal must agree with cursor absence")
        return self


class SourceAdapter(Protocol):
    name: str
    provider: SourceName
    parser_version: str

    def plan(self, request: HarvestRequest, checkpoint: Checkpoint) -> StreamPlan: ...

    def request_page(self, plan: StreamPlan, cursor: Cursor | None) -> RequestSpec: ...

    def parse_page(self, body: bytes, context: ParseContext) -> ParsedPage: ...
