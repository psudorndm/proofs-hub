"""Local filesystem ``BlobStore``. Writes are atomic (temp file + rename)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from proof_index.errors import StorageError
from proof_index.hashing import sha256_hex
from proof_index.storage.base import BlobRef, validate_key, verify_body


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError(f"blob key escapes the store root: {key[:80]!r}")
        return path

    def put_if_absent(self, key: str, body: bytes, sha256: str) -> BlobRef:
        verify_body(body, sha256)
        path = self._path(key)
        if path.exists():
            existing = path.read_bytes()
            if sha256_hex(existing) != sha256:
                raise StorageError(f"blob {key!r} already exists with different content")
            return BlobRef(key, sha256, len(existing), created=False)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                # os.link fails if the target appeared meanwhile: no overwrite race.
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    return self.put_if_absent(key, body, sha256)
            finally:
                Path(tmp).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"could not write blob {key!r} ({type(exc).__name__})") from exc
        return BlobRef(key, sha256, len(body), created=True)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise StorageError(f"blob not found: {key!r}") from None
        except OSError as exc:
            raise StorageError(f"could not read blob {key!r} ({type(exc).__name__})") from exc

    def exists(self, key: str, sha256: str | None = None) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        return sha256 is None or sha256_hex(path.read_bytes()) == sha256
