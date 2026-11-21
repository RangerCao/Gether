import os

import pytest
from fastapi.testclient import TestClient

from app.contracts import EvidencePacket, EvidenceSourceType
from app.core.config import Settings
from app.ingest.pipeline import ParseResult
from app.main import create_app
from app.repository import TaskRepository

TEST_DATABASE_URL = os.getenv("GETHER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="需要 GETHER_TEST_DATABASE_URL"),
]


class FakePipeline:
    async def parse_video(self, *, task_id, video_id, ref, progress=None):
        if progress:
            await progress("subtitle", 1, 1, "ok")
        packet = EvidencePacket(
            task_id=task_id,
            video_id=video_id,
            modality="asr",
            text="API 集成测试证据",
            start_ms=1000,
            end_ms=2000,
            source_type=EvidenceSourceType.NATIVE_SUB,
            content_hash=f"sha256:{video_id}",
        )
        return ParseResult(ref=ref, evidence=[packet], warnings=[], stats={})


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="mock",
        app_env="local",
        database_url=TEST_DATABASE_URL,
        ocr_enabled=False,
        embedding_provider="mock",
        embedding_dim=512,
        chunk_target_chars=100,
        chunk_max_chars=200,
    )


def test_parse_task_end_to_end():
    settings = _settings()
    repository = TaskRepository(settings)
    app = create_app(settings)
    app.state.parse_service.pipeline = FakePipeline()

    import asyncio

    async def _cleanup():
        async with repository.db.connection() as conn:
            await conn.execute("TRUNCATE tasks CASCADE")

    asyncio.run(_cleanup())

    with TestClient(app) as client:
        created = client.post(
            "/internal/tasks",
            json={"videos": ["https://www.bilibili.com/video/BV1xx411c7mD"]},
        )
        assert created.status_code == 201
        task_id = created.json()["task_id"]

        accepted = client.post(f"/internal/tasks/{task_id}/parse")
        assert accepted.status_code == 202

        summary = client.get(f"/internal/tasks/{task_id}")
        assert summary.status_code == 200
        body = summary.json()
        assert body["status"] == "PLANNING"
        assert body["evidence_count"] == 1
        assert body["progress"] == 90

        evidence = client.get(f"/internal/tasks/{task_id}/evidence")
        assert evidence.status_code == 200
        assert evidence.json()["count"] == 1
        assert evidence.json()["evidence"][0]["text"] == "API 集成测试证据"

        indexed = client.post(f"/internal/tasks/{task_id}/index")
        assert indexed.status_code == 200
        assert indexed.json()["chunk_count"] == 1

        chunks = client.get(f"/internal/tasks/{task_id}/chunks")
        assert chunks.status_code == 200
        assert chunks.json()["count"] == 1

        retrieved = client.post(
            "/internal/retrieve",
            json={"task_id": task_id, "query": "API 集成测试证据", "top_k": 5},
        )
        assert retrieved.status_code == 200
        assert retrieved.json()["count"] == 1
        assert retrieved.json()["hits"][0]["text"] == "API 集成测试证据"

        video_id = body["videos"][0]["id"]
        for filters, expected in [
            ({"modalities": ["asr"]}, 1),
            ({"modalities": ["ocr"]}, 0),
            ({"time_start_ms": 1500}, 1),
            ({"time_start_ms": 3000}, 0),
            ({"time_end_ms": 500}, 0),
            ({"video_id": video_id}, 1),
            ({"video_id": "00000000-0000-0000-0000-000000000000"}, 0),
        ]:
            filtered = client.post(
                "/internal/retrieve",
                json={"task_id": task_id, "query": "API 集成测试证据", "top_k": 5, **filters},
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["count"] == expected, filters
