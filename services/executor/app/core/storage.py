"""对象存储：关键帧/缩略图上传，MinIO 兼容 S3。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from app.core.config import Settings


class StorageError(RuntimeError):
    code = "STORAGE_FAILED"


class ObjectStorage(Protocol):
    async def upload_file(self, local_path: Path, key: str) -> str: ...


class MinioStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self._client = None

    def _load(self):
        if self._client is not None:
            return self._client
        try:
            from minio import Minio
        except ImportError as exc:
            raise StorageError("未安装 minio，请重新安装执行层依赖") from exc
        self._client = Minio(
            self.endpoint.replace("http://", "").replace("https://", ""),
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.endpoint.startswith("https://"),
        )
        return self._client

    def _upload_sync(self, local_path: Path, key: str) -> str:
        client = self._load()
        if not client.bucket_exists(self.bucket):
            client.make_bucket(self.bucket)
        client.fput_object(self.bucket, key, str(local_path))
        return key

    async def upload_file(self, local_path: Path, key: str) -> str:
        try:
            return await asyncio.to_thread(self._upload_sync, local_path, key)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"上传失败：{exc}") from exc


def build_storage(settings: Settings) -> ObjectStorage | None:
    if not settings.minio_access_key or not settings.minio_secret_key:
        return None
    return MinioStorage(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
    )
