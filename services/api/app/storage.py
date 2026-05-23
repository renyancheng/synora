from __future__ import annotations

import io
from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from app.config import Settings, get_settings


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = self._build_client(settings)

    @staticmethod
    def _build_client(settings: Settings) -> BaseClient:
        return boto3.client(
            "s3",
            endpoint_url=f"{'https' if settings.minio_secure else 'http'}://{settings.minio_endpoint}",
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name=settings.minio_region,
        )

    @property
    def bucket(self) -> str:
        return self._settings.minio_bucket

    def ensure_bucket_exists(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, object_key: str, payload: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=io.BytesIO(payload),
            ContentType=content_type,
        )

    def get_bytes(self, object_key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=object_key)
        return response["Body"].read()


@lru_cache
def get_object_storage() -> ObjectStorage:
    return ObjectStorage(get_settings())
