"""Choose the configured blob backend."""

from __future__ import annotations

from proof_index.settings import Settings
from proof_index.storage.base import BlobStore
from proof_index.storage.local import LocalBlobStore


def make_blob_store(settings: Settings) -> BlobStore:
    if settings.blob_backend == "local":
        return LocalBlobStore(settings.local_blob_root)
    bucket, endpoint, region = settings.require_s3()
    from proof_index.storage.s3 import S3BlobStore

    return S3BlobStore(bucket, endpoint_url=endpoint, region=region)
