from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.storage import MinioStorage, StorageError, build_storage


class FakeMinio:
    def __init__(self) -> None:
        self.buckets: list[str] = []
        self.uploads: list[tuple[str, str, str]] = []

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.append(bucket)

    def fput_object(self, bucket: str, key: str, path: str) -> None:
        self.uploads.append((bucket, key, path))


def _storage() -> MinioStorage:
    return MinioStorage(
        endpoint="http://localhost:9000",
        access_key="key",
        secret_key="secret",
        bucket="gether-media",
    )


@pytest.mark.asyncio
async def test_upload_creates_bucket_and_returns_key(tmp_path: Path):
    storage = _storage()
    fake = FakeMinio()
    storage._client = fake
    local = tmp_path / "frame.jpg"
    local.write_bytes(b"image")

    key = await storage.upload_file(local, "tasks/t1/frames/v1/frame.jpg")
    assert key == "tasks/t1/frames/v1/frame.jpg"
    assert fake.buckets == ["gether-media"]
    assert fake.uploads == [("gether-media", key, str(local))]


@pytest.mark.asyncio
async def test_upload_wraps_errors(tmp_path: Path):
    storage = _storage()

    class Broken:
        def bucket_exists(self, bucket):
            raise RuntimeError("boom")

    storage._client = Broken()
    with pytest.raises(StorageError):
        await storage.upload_file(tmp_path / "x.jpg", "key")


def test_build_storage_disabled_without_keys():
    settings = Settings(_env_file=None, llm_provider="mock", minio_access_key="", minio_secret_key="")
    assert build_storage(settings) is None
