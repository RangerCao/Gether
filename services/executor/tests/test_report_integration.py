import os

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.contracts import EvidencePacket, EvidenceSourceType, VideoRef, VideoSourceName
from app.core.config import Settings
from app.graph.repository import ReportRepository
from app.graph.service import ReportService
from app.graph.workflow import ReportWorkflow
from app.index.chunking import Chunk
from app.index.retrieval import RetrievalHit
from app.llm.base import LLMRole
from app.repository import TaskRepository

TEST_DATABASE_URL = os.getenv("GETHER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="需要 GETHER_TEST_DATABASE_URL"),
]

E1 = "11111111-1111-1111-1111-111111111111"
E3 = "33333333-3333-3333-3333-333333333333"


class FakeLLM:
    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        if role == LLMRole.PLANNER:
            return '{"sections":[{"id":"s1","title":"效率","points":["实测"]}]}'
        if role == LLMRole.CRITIC:
            return '{"supported":true,"reason":"ok","missing":[]}'
        return f"结论 [ev:{E1}]。"

    async def stream(self, role, messages, *, max_tokens=None):
        if False:
            yield ""

    async def aclose(self):
        return None


class FakeIndex:
    async def search(self, task_id, query, top_k=6):
        return [
            RetrievalHit(
                chunk=Chunk(
                    chunk_id="c1",
                    task_id=task_id,
                    video_id="v1",
                    text="证据",
                    start_ms=1000,
                    end_ms=2000,
                    evidence_ids=(E1,),
                    modalities=("asr",),
                    content_hash="ch",
                ),
                score=0.9,
            )
        ]


@pytest.mark.asyncio
async def test_report_service_hitl_persistence():
    settings = Settings(_env_file=None, llm_provider="mock", database_url=TEST_DATABASE_URL)
    tasks = TaskRepository(settings)
    async with tasks.db.connection() as conn:
        await conn.execute("TRUNCATE tasks CASCADE")
    task_id = await tasks.create_task({"videos": []})
    video_id = await tasks.upsert_video(
        task_id,
        VideoRef(
            source=VideoSourceName.BILIBILI,
            source_video_id="BV1xx411c7mD",
            page=1,
            url="https://www.bilibili.com/video/BV1xx411c7mD",
        ),
    )
    await tasks.insert_evidence(
        [
            EvidencePacket(
                evidence_id=E1,
                task_id=task_id,
                video_id=video_id,
                modality="asr",
                text="镜头里的证据",
                start_ms=1000,
                end_ms=2000,
                source_type=EvidenceSourceType.WHISPER,
                content_hash="h1",
            )
        ]
    )
    await tasks.transition(task_id, "PENDING", "PARSING", progress=1)
    await tasks.transition(task_id, "PARSING", "PLANNING", progress=90)

    service = ReportService(
        workflow=ReportWorkflow(
            llm=FakeLLM(),  # type: ignore[arg-type]
            index_service=FakeIndex(),  # type: ignore[arg-type]
            task_repository=tasks,
            settings=settings,
        ),
        task_repository=tasks,
        report_repository=ReportRepository(settings),
        checkpointer=MemorySaver(),
    )
    started = await service.start(task_id)
    assert started["status"] == "WAITING_APPROVAL"
    assert started["outline"]

    result = await service.resume(task_id, outline=started["outline"])
    assert result["status"] == "DONE"
    assert result["claims"] == 1
    report = await service.get_report(task_id)
    assert len(report["sections"]) == 1
    assert len(report["claims"]) == 1
    assert report["ledger"]
    assert report["status"] == "DONE"


@pytest.mark.asyncio
async def test_postgres_checkpointer_survives_service_restart():
    from app.graph.service import PostgresCheckpointer

    settings = Settings(_env_file=None, llm_provider="mock", database_url=TEST_DATABASE_URL)
    tasks = TaskRepository(settings)
    task_id = await tasks.create_task({"videos": []})
    video_id = await tasks.upsert_video(
        task_id,
        VideoRef(
            source=VideoSourceName.BILIBILI,
            source_video_id="BV1restart",
            page=1,
            url="https://www.bilibili.com/video/BV1restart",
        ),
    )
    await tasks.insert_evidence(
        [
            EvidencePacket(
                evidence_id=E3,
                task_id=task_id,
                video_id=video_id,
                modality="asr",
                text="持久化证据",
                start_ms=1000,
                end_ms=2000,
                source_type=EvidenceSourceType.WHISPER,
                content_hash="restart-h1",
            )
        ]
    )
    await tasks.transition(task_id, "PENDING", "PARSING", progress=1)
    await tasks.transition(task_id, "PARSING", "PLANNING", progress=90)

    first = ReportService(
        workflow=ReportWorkflow(
            llm=FakeLLM(),  # type: ignore[arg-type]
            index_service=FakeIndex(),  # type: ignore[arg-type]
            task_repository=tasks,
            settings=settings,
        ),
        task_repository=tasks,
        report_repository=ReportRepository(settings),
        checkpointer=PostgresCheckpointer(settings),
    )
    started = await first.start(task_id)
    await first.aclose()

    second = ReportService(
        workflow=ReportWorkflow(
            llm=FakeLLM(),  # type: ignore[arg-type]
            index_service=FakeIndex(),  # type: ignore[arg-type]
            task_repository=tasks,
            settings=settings,
        ),
        task_repository=tasks,
        report_repository=ReportRepository(settings),
        checkpointer=PostgresCheckpointer(settings),
    )
    result = await second.resume(task_id, outline=started["outline"])
    await second.aclose()
    assert result["status"] == "DONE"
