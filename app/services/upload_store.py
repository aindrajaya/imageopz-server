"""
Uploads bucket adapter (browser -> R2 via presigned PUT, ADR-002).
Always talks to the R2 S3 endpoint, never the public custom domain. Tests replace it with an in-memory fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional, Protocol

from app.config import settings


@dataclass(frozen=True)
class ObjectHead:
    size: int
    content_type: Optional[str]


class UploadStore(Protocol):
    def presign_put(self, key: str, content_type: str, expires_in: int, content_length: Optional[int] = None) -> str: ...

    def head(self, key: str) -> Optional[ObjectHead]:
        """Object metadata, or None when the object does not exist."""

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Bytes start..end inclusive (HTTP Range semantics)."""

    def get_stream(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]: ...

    def delete(self, key: str) -> None: ...


class UploadStoreNotConfigured(RuntimeError):
    pass


def uploads_configured() -> bool:
    """Only the uploads bucket's own token counts (no fallback to the outputs-bucket credentials)."""
    return all([settings.r2_account_id, settings.r2_uploads_access_key_id, settings.r2_uploads_secret_access_key])


class R2UploadStore:
    def __init__(self) -> None:
        if not uploads_configured():
            raise UploadStoreNotConfigured("R2 uploads bucket credentials not configured")
        import boto3
        from botocore.config import Config

        self.bucket = settings.r2_uploads_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_uploads_access_key_id,
            aws_secret_access_key=settings.r2_uploads_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def presign_put(self, key: str, content_type: str, expires_in: int, content_length: Optional[int] = None) -> str:
        params = {"Bucket": self.bucket, "Key": key, "ContentType": content_type}
        if content_length is not None:
            params["ContentLength"] = content_length
        return self.client.generate_presigned_url("put_object", Params=params, ExpiresIn=expires_in)

    def head(self, key: str) -> Optional[ObjectHead]:
        from botocore.exceptions import ClientError

        try:
            res = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise
        return ObjectHead(size=int(res["ContentLength"]), content_type=res.get("ContentType"))

    def get_range(self, key: str, start: int, end: int) -> bytes:
        res = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}")
        return res["Body"].read()

    def get_stream(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            yield from body.iter_chunks(chunk_size)
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_store: Optional[UploadStore] = None


def get_upload_store() -> UploadStore:
    """Process-wide store, built on first use (raises UploadStoreNotConfigured without credentials)."""
    global _store
    if _store is None:
        _store = R2UploadStore()
    return _store
