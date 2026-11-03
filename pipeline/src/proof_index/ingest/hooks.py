"""Promotion hooks: classification, identity, and events plug in here.

Hooks run inside the promotion batch transaction (spec section 8, step 5), so
their writes commit or roll back together with the observation change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Connection

from proof_index.models.source import NormalizedRecord, Tombstone

ChangeKind = Literal["initial", "changed", "reverted", "tombstone"]


@dataclass(frozen=True)
class CurrentChange:
    source_record_id: uuid.UUID
    observation_id: uuid.UUID
    previous_observation_id: uuid.UUID | None
    item: NormalizedRecord | Tombstone
    change_kind: ChangeKind
    run_id: uuid.UUID
    run_mode: str
    stream_kind: str
    observed_at: datetime


class PromotionHooks(Protocol):
    def current_changed(self, conn: Connection, change: CurrentChange) -> None: ...


class NoopHooks:
    def current_changed(self, conn: Connection, change: CurrentChange) -> None:
        return None


class HookChain:
    def __init__(self, *hooks: PromotionHooks) -> None:
        self.hooks = hooks

    def current_changed(self, conn: Connection, change: CurrentChange) -> None:
        for hook in self.hooks:
            hook.current_changed(conn, change)
