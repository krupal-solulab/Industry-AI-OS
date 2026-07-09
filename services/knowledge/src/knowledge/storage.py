"""MinIO (S3-compatible) object storage wrapper.

The only place object storage is touched. Swapping to real S3 in cloud is a config
change (endpoint + credentials), not a code change — MinIO speaks the S3 API.
"""

from __future__ import annotations

import io

from minio import Minio

from ai_os_shared.settings import get_settings

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        s = get_settings()
        _client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        if not _client.bucket_exists(s.minio_bucket):
            _client.make_bucket(s.minio_bucket)
    return _client


def put_object(object_key: str, data: bytes, content_type: str | None) -> None:
    s = get_settings()
    get_client().put_object(
        s.minio_bucket,
        object_key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type or "application/octet-stream",
    )


def get_object(object_key: str) -> bytes:
    s = get_settings()
    resp = get_client().get_object(s.minio_bucket, object_key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()
