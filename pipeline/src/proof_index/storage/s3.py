"""S3-compatible ``BlobStore`` using boto3 and the normal SDK credential chain.

The SHA-256 is stored as object metadata and checked on reads. Creation uses a
conditional ``If-None-Match: *`` write so two writers cannot overwrite each other.
"""

from __future__ import annotations

from typing import Any

from proof_index.errors import StorageError
from proof_index.hashing import sha256_hex
from proof_index.storage.base import BlobRef, validate_key, verify_body

_META_KEY = "sha256"


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    return str(response.get("Error", {}).get("Code", type(exc).__name__))


class S3BlobStore:
    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region)
        self.client = client

    def _key(self, key: str) -> str:
        validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def _head(self, key: str) -> dict[str, Any] | None:
        try:
            return dict(self.client.head_object(Bucket=self.bucket, Key=self._key(key)))
        except Exception as exc:
            if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageError(f"S3 head_object failed ({_error_code(exc)})") from exc

    def put_if_absent(self, key: str, body: bytes, sha256: str) -> BlobRef:
        verify_body(body, sha256)
        head = self._head(key)
        if head is not None:
            existing = head.get("Metadata", {}).get(_META_KEY)
            if existing != sha256:
                raise StorageError(f"blob {key!r} already exists with different content")
            return BlobRef(key, sha256, int(head.get("ContentLength", len(body))), created=False)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(key),
                Body=body,
                Metadata={_META_KEY: sha256},
                IfNoneMatch="*",
            )
        except Exception as exc:
            if _error_code(exc) in {"PreconditionFailed", "412", "ConditionalRequestConflict"}:
                return self.put_if_absent(key, body, sha256)
            raise StorageError(f"S3 put_object failed ({_error_code(exc)})") from exc
        return BlobRef(key, sha256, len(body), created=True)

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            raise StorageError(f"S3 get_object failed for {key!r} ({_error_code(exc)})") from exc
        body: bytes = response["Body"].read()
        expected = response.get("Metadata", {}).get(_META_KEY)
        if expected is not None and sha256_hex(body) != expected:
            raise StorageError(f"S3 blob {key!r} failed its integrity check")
        return body

    def exists(self, key: str, sha256: str | None = None) -> bool:
        head = self._head(key)
        if head is None:
            return False
        return sha256 is None or head.get("Metadata", {}).get(_META_KEY) == sha256
