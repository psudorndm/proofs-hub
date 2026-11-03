"""Canonical JSON and SHA-256 helpers (spec section 5)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Sorted keys, compact separators, UTF-8 text (no ASCII escaping)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def stable_int64(key: str) -> int:
    """Stable signed 64-bit integer from SHA-256 (for advisory lock keys).

    Python's ``hash()`` is randomized per process and must not be used for this.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)
