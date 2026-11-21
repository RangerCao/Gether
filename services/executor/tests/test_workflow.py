import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.contracts import EvidencePacket, EvidenceSourceType
from app.graph.workflow import ReportWorkflow
from app.index.chunking import Chunk
from app.index.retrieval import RetrievalHit
from app.llm.base import LLMRole

E1 = "11111111-1111-1111-1111-111111111111"
E2 = "22222222-2222-2222-2222-222222222222"


class FakeLLM:
    def __init__(self, citation: str = f"[ev:{E1}]") -> None:
        self.citation = citation

    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        if role == LLMRole.PLANNER:
            return (
                '{"sections":[{"id":"s1","title":"效率现状","points":["实测"]},'
                '{"id":"s2","title":"风险","points":["失败案例"]}]}'
            )
        if role == LLMRole.CRITIC:
            return '{"supported":true,"reason":"证据充分","missing":[]}'
        return f"本章节的核心结论 {self.citation}。"

    async def stream(self, role, messages, *, max_tokens=None):
        if False:
            yield ""

    async def aclose(self):
        return None


class FakeTaskRepo:
    def __init__(self) -> None:
        self.evidence = [
            EvidencePacket(
                evidence_id=E1,
                task_id="t1",
                video_id="v1",
                modality="asr",
                text="AI 编程工具能提升效率",
                start_ms=1000,
                end_ms=2000,
                source_type=EvidenceSourceType.WHISPER,
                content_hash="h1",
            ),
            EvidencePacket(
                evidence_id=E2,
                task_id="t1",
                video_id="v2",
                modality="asr",
                text="AI 编程工具存在质量风险",
                start_ms=3000,
                end_ms=4000,
                source_type=EvidenceSourceType.WHISPER,
                content_hash="h2",
            ),
        ]

    async def list_evidence(self, task_id, limit=500):
        return self.evidence


class FakeIndex:
    async def search(self, task_id, query, top_k=6):
        return [
            RetrievalHit(
                chunk=Chunk(
                    chunk_id="c1",
                    task_id="t1",
                    video_id="v1",
                    text="AI 编程工具能提升效率",
                    start_ms=1000,
                    end_ms=2000,
                    evidence_ids=(E1,),
                    modalities=("asr",),
                    content_hash="ch1",
                ),
                score=0.9,
            )
        ]


def _workflow(llm=None):
    return ReportWorkflow(
        llm=llm or FakeLLM(),
        index_service=FakeIndex(),  # type: ignore[arg-type]
        task_repository=FakeTaskRepo(),  # type: ignore[arg-type]
        settings=__import__("app.core.config", fromlist=["Settings"]).Settings(
            _env_file=None, llm_provider="mock"
        ),
    )


@pytest.mark.asyncio
async def test_workflow_hitl_and_citations():
    graph = _workflow().build(MemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    first = await graph.ainvoke({"task_id": "t1"}, config)
    assert "__interrupt__" in first
    assert len(first["outline"]) == 2

    final = await graph.ainvoke(Command(resume={"outline": first["outline"]}), config)
    assert len(final["claims"]) == 2
    assert final["citation_stats"]["citations_valid"] >= 2
    assert final["citation_stats"]["citations_invalid"] == 0
    assert final["report_markdown"].startswith("## 效率现状")
    assert final["ledger"]


@pytest.mark.asyncio
async def test_fast_mode_parallel_report():
    graph = _workflow().build(MemorySaver())
    config = {"configurable": {"thread_id": "t-fast"}}
    first = await graph.ainvoke({"task_id": "t1", "mode": "fast"}, config)
    assert "__interrupt__" in first
    final = await graph.ainvoke(
        Command(resume={"outline": first["outline"]}), config
    )
    assert final["citation_stats"]["citations_valid"] >= 2
    assert final["report_markdown"].startswith("## 效率现状")
    assert final["ledger"]


@pytest.mark.asyncio
async def test_invalid_citation_is_removed():
    graph = _workflow(FakeLLM("[ev:deadbeef-dead-dead-dead-deaddeadbeef]")).build(MemorySaver())
    config = {"configurable": {"thread_id": "t2"}}
    first = await graph.ainvoke({"task_id": "t1"}, config)
    final = await graph.ainvoke(Command(resume={"outline": first["outline"]}), config)
    assert final["citation_stats"]["citations_invalid"] == 2
    assert all("[ev:deadbeef-dead-dead-dead-deaddeadbeef]" not in claim["text"] for claim in final["claims"])
    assert any("无效引用" in warning for warning in final["warnings"])


class FakeReportRepo:
    def __init__(self):
        self.saved = None

    async def save_report(self, **kwargs):
        self.saved = kwargs


class StreamingTaskRepo(FakeTaskRepo):
    def __init__(self):
        super().__init__()
        self.status = "PLANNING"
        self.transitions = []

    async def get_task(self, task_id):
        return {"id": task_id, "status": self.status}

    async def transition(self, task_id, from_status, to_status, **kwargs):
        self.transitions.append((from_status, to_status))
        self.status = to_status
        return True


@pytest.mark.asyncio
async def test_stream_resume_emits_sse_events():
    from app.graph.service import ReportService

    report_repo = FakeReportRepo()
    tasks = StreamingTaskRepo()
    service = ReportService(
        workflow=_workflow(),
        task_repository=tasks,  # type: ignore[arg-type]
        report_repository=report_repo,  # type: ignore[arg-type]
        checkpointer=MemorySaver(),
    )
    started = await service.start("t1")
    events = [
        event
        async for event in service.stream_resume(
            "t1", outline=started["outline"]
        )
    ]
    text = "".join(events)
    assert "event: token" in text
    assert "event: done" in text
    assert report_repo.saved is not None


class ConflictLLM(FakeLLM):
    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        if role == LLMRole.PLANNER:
            return (
                '{"sections":[{"id":"s1","title":"乐观观点","points":["增速"]},'
                '{"id":"s2","title":"谨慎观点","points":["风险"]}]}'
            )
        if role == LLMRole.CRITIC:
            return '{"supported":true,"reason":"ok","missing":[]}'
        text = messages[-1].content
        if "乐观观点" in text:
            return f"该行业增速将达到 10% [ev:{E1}]。"
        return f"该行业增速将下降到 3% [ev:{E2}]。"


class FakeIndexBoth(FakeIndex):
    async def search(self, task_id, query, top_k=6):
        return [
            RetrievalHit(
                chunk=Chunk(
                    chunk_id="c1",
                    task_id="t1",
                    video_id="v1",
                    text="证据",
                    start_ms=1000,
                    end_ms=2000,
                    evidence_ids=(E1, E2),
                    modalities=("asr",),
                    content_hash="ch1",
                ),
                score=0.9,
            )
        ]


@pytest.mark.asyncio
async def test_multi_video_conflict_end_to_end():
    workflow = ReportWorkflow(
        llm=ConflictLLM(),  # type: ignore[arg-type]
        index_service=FakeIndexBoth(),  # type: ignore[arg-type]
        task_repository=FakeTaskRepo(),  # type: ignore[arg-type]
        settings=__import__("app.core.config", fromlist=["Settings"]).Settings(
            _env_file=None, llm_provider="mock"
        ),
    )
    graph = workflow.build(MemorySaver())
    config = {"configurable": {"thread_id": "t-conflict"}}
    first = await graph.ainvoke({"task_id": "t1"}, config)
    final = await graph.ainvoke(Command(resume={"outline": first["outline"]}), config)
    assert len(final["conflicts"]) == 1
    assert final["conflicts"][0]["type"] == "numeric_mismatch"
    assert any(entry["support_relation"] == "rebut" for entry in final["ledger"])
