"""``BlobStore`` protocol (spec section 7).

Blobs are immutable and content-addressed by SHA-256. Writing a different body
under an existing key is a storage error, never a silent overwrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from proof_index.errors import StorageError
from proof_index.hashing import sha256_hex

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(?:/[A-Za-z0-9][A-Za-z0-9._\-]*)*$")


@dataclass(frozen=True)
class BlobRef:
    key: str
    sha256: str
    size: int
    created: bool  # False when the identical blob already existed


class BlobStore(Protocol):
    def put_if_absent(self, key: str, body: bytes, sha256: str) -> BlobRef: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str, sha256: str | None = None) -> bool: ...


def validate_key(key: str) -> str:
    if len(key) > 512 or not _KEY.match(key) or ".." in key.split("/"):
        raise StorageError(f"invalid blob key: {key[:80]!r}")
    return key


def verify_body(body: bytes, sha256: str) -> None:
    actual = sha256_hex(body)
    if actual != sha256:
        raise StorageError(f"body hash mismatch (expected {sha256[:12]}…, got {actual[:12]}…)")


def raw_response_key(provider: str, sha256: str) -> str:
    """``raw/<provider>/<aa>/<bb>/<sha256>`` — content-addressed raw response key."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise StorageError("raw response keys require a lower-case SHA-256 hex digest")
    return validate_key(f"raw/{provider}/{sha256[:2]}/{sha256[2:4]}/{sha256}")
