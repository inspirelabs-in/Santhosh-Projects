"""Cloudflare R2 (S3-compatible) object storage wrapper.

Used for resumes, consent artifact snapshots, interview transcripts.
Keys follow: `{bucket_logical}/{candidate_id}/{uuid}_{sanitised_filename}`.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

import boto3
from botocore.client import Config

from src.config import get_settings

_settings = get_settings()
_SANITISE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitise_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    return _SANITISE.sub("", name)[:180] or "file"


@dataclass
class StoredFile:
    key: str
    bucket: str
    size_bytes: int
    content_type: str | None


def _client():
    return boto3.client(
        "s3",
        endpoint_url=_settings.r2_endpoint_url,
        aws_access_key_id=_settings.r2_access_key_id,
        aws_secret_access_key=_settings.r2_secret_access_key,
        region_name=_settings.r2_region,
        # MinIO needs path-style addressing; R2 + AWS S3 also accept it.
        # This keeps the backend portable across all three providers.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


async def upload_resume(
    *,
    candidate_id: UUID,
    filename: str,
    content: bytes,
    content_type: str | None = None,
) -> StoredFile:
    """Upload a resume to R2. Returns the stored key (path inside the bucket)."""
    bucket = _settings.r2_bucket_resumes
    key = f"{candidate_id}/{uuid4()}_{_sanitise_filename(filename)}"

    def _put() -> None:
        extra: dict[str, str] = {}
        # R2 / S3 support SSE; MinIO encrypts at rest by its own config.
        if _settings.r2_endpoint_url and "minio" not in (_settings.r2_endpoint_url or ""):
            extra["ServerSideEncryption"] = "AES256"
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=content_type or "application/octet-stream",
            **extra,
        )

    await asyncio.to_thread(_put)
    return StoredFile(key=key, bucket=bucket, size_bytes=len(content), content_type=content_type)


async def upload_blob(
    *,
    bucket: str,
    key: str,
    content: bytes,
    content_type: str | None = None,
) -> StoredFile:
    """Upload an arbitrary blob to a bucket under a caller-chosen key.

    Used by the voice + meeting pipelines for transcripts and recordings;
    the resume helper above stays the canonical path for resumes.
    """

    def _put() -> None:
        extra: dict[str, str] = {}
        if _settings.r2_endpoint_url and "minio" not in (_settings.r2_endpoint_url or ""):
            extra["ServerSideEncryption"] = "AES256"
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=content_type or "application/octet-stream",
            **extra,
        )

    await asyncio.to_thread(_put)
    return StoredFile(key=key, bucket=bucket, size_bytes=len(content), content_type=content_type)


async def download(bucket: str, key: str) -> bytes:
    def _get() -> bytes:
        resp = _client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()

    return await asyncio.to_thread(_get)


def _public_client():
    """S3 client bound to the browser-facing endpoint so presigned URLs are
    reachable from outside the docker network."""
    endpoint = _settings.r2_public_endpoint_url or _settings.r2_endpoint_url
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=_settings.r2_access_key_id,
        aws_secret_access_key=_settings.r2_secret_access_key,
        region_name=_settings.r2_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


async def presigned_get_url(bucket: str, key: str, ttl_seconds: int = 900) -> str:
    def _sign() -> str:
        return _public_client().generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl_seconds
        )

    return await asyncio.to_thread(_sign)


async def delete(bucket: str, key: str) -> None:
    def _del() -> None:
        _client().delete_object(Bucket=bucket, Key=key)

    await asyncio.to_thread(_del)
