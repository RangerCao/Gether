import os

import pytest

from app.contracts import EvidencePacket, EvidenceSourceType, VideoRef, VideoSourceName
from app.core.config import Settings
from app.repository import InvalidTaskTransition, TaskRepository

TEST_DATABASE_URL = os.getenv("GETHER_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="需要 GETHER_TEST_DATABASE_URL"),
]


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="mock",
        database_url=TEST_DATABASE_URL,
    )


def _ref() -> VideoRef:
    return VideoRef(
        source=VideoSourceName.BILIBILI,
        source_video_id="BV1xx411c7mD",
        page=1,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )


@pytest.mark.asyncio
async def test_task_and_evidence_roundtrip():
    repository = TaskRepository(_settings())
    async with repository.db.connection() as conn:
        await conn.execute("TRUNCATE tasks CASCADE")

    task_id = await repository.create_task({"videos": [{"url": _ref().url}]})
    task = await repository.get_task(task_id)
    assert task["status"] == "PENDING"
    assert task["evidence_count"] == 0

    assert await repository.transition(task_id, "PENDING", "PARSING", progress=1)
    assert not await repository.transition(task_id, "PENDING", "PARSING")

    video_id = await repository.upsert_video(task_id, _ref())
    assert video_id

    packet = EvidencePacket(
        task_id=task_id,
        video_id=video_id,
        modality="asr",
        text="市场规模将在明年回落",
        raw_text="市场规模将在明年回洛",
        start_ms=1000,
        end_ms=3000,
        source_type=EvidenceSourceType.NATIVE_SUB,
        content_hash="sha256:integration-test",
    )
    assert await repository.insert_evidence([packet]) == 1
    assert await repository.insert_evidence([packet]) == 0

    assert await repository.count_evidence(task_id) == 1
    evidence = await repository.list_evidence(task_id)
    assert len(evidence) == 1
    assert evidence[0].text == "市场规模将在明年回落"
    assert evidence[0].raw_text == "市场规模将在明年回洛"
    assert evidence[0].start_ms == 1000

    assert await repository.transition(task_id, "PARSING", "PLANNING", progress=90)
    with pytest.raises(InvalidTaskTransition):
        await repository.transition(task_id, "PLANNING", "PARSING")

    summary = await repository.get_task(task_id)
    assert summary["status"] == "PLANNING"
    assert summary["progress"] == 90
