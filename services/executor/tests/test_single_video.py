"""单视频研报：大纲、时间轴、证据缺口、服务与工作流。"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.api.routes import router
from app.contracts import EvidencePacket, EvidenceSourceType, VideoRef, VideoSourceName
from app.core.config import Settings
from app.discovery.metadata import BilibiliMetadataClient, video_meta
from app.graph.guard import expand_citation_brackets, strip_invalid_citations
from app.graph.single import (
    build_single_outline,
    single_gaps,
    timeline_windows,
)
from app.graph.workflow import ReportWorkflow
from app.index.chunking import Chunk
from app.index.retrieval import RetrievalHit
from app.llm.base import LLMRole
from app.single.service import SingleVideoError, SingleVideoService

E1 = "11111111-1111-1111-1111-111111111111"
E2 = "22222222-2222-2222-2222-222222222222"
E3 = "33333333-3333-3333-3333-333333333333"


def _packet(evidence_id: str, start_ms: int, text: str) -> EvidencePacket:
    return EvidencePacket(
        evidence_id=evidence_id,
        task_id="t1",
        video_id="v1",
        modality="asr",
        text=text,
        start_ms=start_ms,
        end_ms=start_ms + 5000,
        source_type=EvidenceSourceType.NATIVE_SUB,
        content_hash=f"h-{evidence_id}",
    )


def test_single_outline_is_fixed_and_appends_question():
    base = build_single_outline()
    assert [section["id"] for section in base] == ["s1", "s2", "s3", "s4", "s5", "s6"]
    assert base[0]["title"] == "一句话结论"

    with_question = build_single_outline("这套方案适合小团队吗？")
    assert with_question[-1]["id"] == "s7"
    assert with_question[-1]["title"] == "针对你的问题"
    assert with_question[-1]["points"] == ["这套方案适合小团队吗？"]


def test_timeline_windows_cover_whole_video():
    evidence = [
        _packet(E1, 0, "开场介绍"),
        _packet(E2, 10 * 60 * 1000, "中段讲成本和返工"),
        _packet(E3, 40 * 60 * 1000, "结尾给结论"),
    ]
    windows = timeline_windows(evidence, max_windows=5)
    assert len(windows) >= 3
    # 时间轴必须覆盖到片尾，而不是只取前几条证据
    assert windows[-1]["end_ms"] == evidence[-1].end_ms
    assert E3 in windows[-1]["evidence_ids"]
    assert windows[0]["start_ms"] == 0


def test_timeline_windows_empty_without_evidence():
    assert timeline_windows([]) == []


def test_expand_multi_id_citation_brackets():
    text = f"结论 [ev:{E1}, ev:{E2}][ev:{E3}]。"
    expanded = expand_citation_brackets(text)
    assert expanded == f"结论 [ev:{E1}][ev:{E2}][ev:{E3}]。"
    cleaned, stats = strip_invalid_citations(expanded, {E1, E2, E3})
    assert cleaned == expanded
    assert stats.valid == 3


def test_single_gaps_marks_unverified_claims():
    claims = [
        {"claim_id": "c1", "section_id": "s1", "text": "有证据", "evidence_ids": [E1]},
        {"claim_id": "c2", "section_id": "s1", "text": "相似度低", "evidence_ids": [E2]},
        {"claim_id": "c3", "section_id": "s3", "text": "没有引用", "evidence_ids": []},
    ]
    ledger = [
        {"claim_id": "c1", "evidence_id": E1, "verified": True},
        {"claim_id": "c2", "evidence_id": E2, "verified": False},
    ]
    gaps = single_gaps(claims, ledger)
    assert [gap["claim_id"] for gap in gaps] == ["c2", "c3"]
    assert "措辞差异较大" in gaps[0]["reason"]
    assert gaps[1]["reason"] == "未绑定原片证据"


def test_video_meta_normalizes_view_payload():
    meta = video_meta(
        {
            "bvid": "BV1xx411c7mD",
            "aid": 123,
            "title": "实测 AI 编程",
            "duration": 600,
            "pubdate": 1700000000,
            "desc": "简介",
            "pic": "https://i0.hdslb.com/cover.jpg",
            "owner": {"name": "某UP", "mid": 9},
            "stat": {"view": 120000, "like": 1000, "danmaku": 30},
            "pages": [{"page": 1}],
        }
    )
    assert meta["source_video_id"] == "BV1xx411c7mD"
    assert meta["duration_ms"] == 600000
    assert meta["uploader"] == "某UP"
    assert meta["views"] == 120000
    assert meta["url"].endswith("/BV1xx411c7mD")


@pytest.mark.asyncio
async def test_metadata_client_queries_by_bvid():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({key: value for key, value in request.url.params.items()})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "bvid": "BV1xx411c7mD",
                    "title": "标题",
                    "duration": 120,
                    "owner": {"name": "UP"},
                    "stat": {"view": 5},
                },
            },
        )

    client = BilibiliMetadataClient(cookies_file=None)
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        meta = await client.fetch_video("BV1xx411c7mD")
    finally:
        await client.client.aclose()
    assert seen.get("bvid") == "BV1xx411c7mD"
    assert meta is not None
    assert meta["title"] == "标题"


class FakeSources:
    def __init__(self, ref: VideoRef) -> None:
        self.ref = ref

    async def normalize(self, url: str) -> VideoRef:
        if "youtube" in url:
            return VideoRef(
                source=VideoSourceName.YOUTUBE,
                source_video_id="yt-1",
                url=url,
            )
        return self.ref


class FakeParseService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_task(self, urls, user_id=None, *, question=None, mode="quality", extra=None):
        self.calls.append(
            {
                "urls": urls,
                "user_id": user_id,
                "question": question,
                "mode": mode,
                "extra": extra,
            }
        )
        return "task-1"


class FakeMetadata:
    def __init__(self, meta: dict | None) -> None:
        self.meta = meta

    async def fetch_video(self, video_id: str):
        return self.meta

    async def aclose(self):
        return None


def _service(parse: FakeParseService, meta: dict | None = None) -> SingleVideoService:
    ref = VideoRef(
        source=VideoSourceName.BILIBILI,
        source_video_id="BV1xx411c7mD",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    return SingleVideoService(
        sources=FakeSources(ref),  # type: ignore[arg-type]
        parse_service=parse,  # type: ignore[arg-type]
        settings=Settings(_env_file=None, llm_provider="mock"),
        metadata_client=FakeMetadata(meta),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_single_service_creates_single_video_task():
    parse = FakeParseService()
    service = _service(
        parse,
        {
            "source": "bilibili",
            "source_video_id": "BV1xx411c7mD",
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "标题",
            "duration_ms": 600000,
            "uploader": "UP",
            "views": 10,
        },
    )
    result = await service.create(input="BV1xx411c7mD", question="值得学吗", user_id="u1")
    assert result["task_id"] == "task-1"
    assert result["kind"] == "single_video"
    call = parse.calls[0]
    assert call["urls"] == ["https://www.bilibili.com/video/BV1xx411c7mD"]
    assert call["user_id"] == "u1"
    assert call["mode"] == "fast"
    assert call["extra"]["kind"] == "single_video"
    assert call["extra"]["single_question"] == "值得学吗"
    assert call["extra"]["video_meta"]["title"] == "标题"
    assert call["question"] == "值得学吗"


@pytest.mark.asyncio
async def test_single_service_rejects_non_bilibili():
    parse = FakeParseService()
    service = _service(parse)
    with pytest.raises(SingleVideoError) as exc:
        await service.create(input="https://www.youtube.com/watch?v=x")
    assert exc.value.code == "UNSUPPORTED_SOURCE"
    assert not parse.calls


@pytest.mark.asyncio
async def test_single_service_rejects_empty_input():
    service = _service(FakeParseService())
    with pytest.raises(SingleVideoError) as exc:
        await service.create(input="   ")
    assert exc.value.code == "INVALID_INPUT"


class FakeSingleService:
    async def create(self, **kwargs):
        assert kwargs["input"] == "BV1xx411c7mD"
        return {
            "task_id": "task-1",
            "status": "PENDING",
            "kind": "single_video",
            "video": {"source_video_id": "BV1xx411c7mD", "title": "标题"},
        }


class RejectingSingleService:
    async def create(self, **kwargs):
        raise SingleVideoError("UNSUPPORTED_SOURCE", "单视频研报目前只支持 B 站视频")


def _api(service) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.single_service = service
    return TestClient(app)


def test_single_task_route_returns_task():
    client = _api(FakeSingleService())
    response = client.post(
        "/internal/single/tasks", json={"input": "BV1xx411c7mD", "mode": "fast"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["task_id"] == "task-1"
    assert body["video"]["title"] == "标题"


def test_single_task_route_maps_domain_error():
    client = _api(RejectingSingleService())
    response = client.post(
        "/internal/single/tasks", json={"input": "https://youtu.be/x"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "UNSUPPORTED_SOURCE"


class FakeLLM:
    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        if role == LLMRole.PLANNER:
            raise AssertionError("单视频不应调用 planner LLM")
        if role == LLMRole.CRITIC:
            return '{"supported":true,"reason":"ok","missing":[]}'
        return f"视频给出的关键结论 [ev:{E1}]。"

    async def stream(self, role, messages, *, max_tokens=None):
        if False:
            yield ""

    async def aclose(self):
        return None


class FakeTaskRepo:
    def __init__(self) -> None:
        self.evidence = [
            _packet(E1, 0, "开场：这条视频讨论 AI 编程工具"),
            _packet(E2, 8 * 60 * 1000, "中段：作者认为返工成本被低估"),
            _packet(E3, 20 * 60 * 1000, "结尾：作者给出适用条件"),
        ]

    async def list_evidence(self, task_id, limit=500):
        return self.evidence


class FakeIndex:
    async def search(self, task_id, query, top_k=6):
        return [
            RetrievalHit(
                chunk=Chunk(
                    chunk_id="c1",
                    task_id=task_id,
                    video_id="v1",
                    text="开场：这条视频讨论 AI 编程工具",
                    start_ms=0,
                    end_ms=5000,
                    evidence_ids=(E1,),
                    modalities=("asr",),
                    content_hash="ch1",
                ),
                score=0.9,
            )
        ]


@pytest.mark.asyncio
async def test_single_video_workflow_has_timeline_and_no_conflicts():
    workflow = ReportWorkflow(
        llm=FakeLLM(),  # type: ignore[arg-type]
        index_service=FakeIndex(),  # type: ignore[arg-type]
        task_repository=FakeTaskRepo(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None, llm_provider="mock"),
    )
    graph = workflow.build(MemorySaver())
    config = {"configurable": {"thread_id": "t-single"}}
    first = await graph.ainvoke(
        {
            "task_id": "t1",
            "mode": "fast",
            "report_kind": "single_video",
            "video_meta": {"title": "标题", "url": "https://www.bilibili.com/video/BV1"},
            "warnings": [],
            "outline": [],
        },
        config,
    )
    assert "__interrupt__" in first
    assert [section["id"] for section in first["outline"]][:2] == ["s1", "s2"]

    final = await graph.ainvoke(
        Command(resume={"outline": first["outline"]}), config
    )
    assert final["conflicts"] == []
    assert final["quality"]["timeline_windows"] >= 2
    assert final["report_markdown"].startswith("# 标题")
    assert "## 内容脉络" in final["report_markdown"]
    assert final["claims"]


class StubTaskRepo:
    async def get_task(self, task_id):
        return {
            "status": "DONE",
            "input_json": {
                "kind": "single_video",
                "video_meta": {
                    "source_video_id": "BV1xx411c7mD",
                    "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                    "title": "标题",
                },
            },
        }

    async def list_evidence(self, task_id, limit=500):
        return [
            _packet(E1, 0, "开场"),
            _packet(E2, 60_000, "结尾"),
        ]

    async def list_videos(self, task_id):
        return []


class StubReportRepo:
    async def get_report(self, task_id):
        return {
            "task_id": task_id,
            "sections": [
                {"order_no": 1, "title": "一句话结论", "content_md": "结论", "status": "DONE"}
            ],
            "claims": [
                {"claim_id": "c1", "section_id": "s1", "text": "结论"},
            ],
            "ledger": [{"claim_id": "c1", "evidence_id": E1, "verified": True}],
            "citations": [],
            "conflicts": [],
        }


@pytest.mark.asyncio
async def test_report_service_attaches_single_video_payload():
    from app.graph.service import ReportService

    service = ReportService(
        workflow=None,  # type: ignore[arg-type]
        task_repository=StubTaskRepo(),  # type: ignore[arg-type]
        report_repository=StubReportRepo(),  # type: ignore[arg-type]
        checkpointer=None,
    )
    report = await service.get_report("t1")
    assert report["kind"] == "single_video"
    assert report["video"]["title"] == "标题"
    assert report["timeline"]
    assert report["quality"]["evidence_count"] == 2
    assert report["gaps"] == []
