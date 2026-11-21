import pytest

from app.contracts import EvidencePacket, EvidenceSourceType, VideoRef, VideoSourceName
from app.ingest.pipeline import IngestError, ParseResult
from app.services.parse_service import InvalidTaskState, ParseService


def _ref() -> VideoRef:
    return VideoRef(
        source=VideoSourceName.BILIBILI,
        source_video_id="BV1xx411c7mD",
        page=1,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )


def _packet() -> EvidencePacket:
    return EvidencePacket(
        task_id="task-1",
        video_id="video-1",
        modality="asr",
        text="证据",
        start_ms=1000,
        end_ms=2000,
        source_type=EvidenceSourceType.NATIVE_SUB,
        content_hash="sha256:abc",
    )


class FakeRepository:
    def __init__(self, task: dict | None = None) -> None:
        self.task = task
        self.created_input = None
        self.transitions: list[tuple[str, str]] = []
        self.progress: list[int] = []
        self.inserted = 0
        self.count = 0
        self.videos = 0

    async def create_task(self, input_json, user_id=None):
        self.created_input = input_json
        return "task-1"

    async def get_task(self, task_id):
        return self.task

    async def transition(self, task_id, from_status, to_status, **kwargs):
        self.transitions.append((from_status, to_status))
        return True

    async def update_progress(self, task_id, progress):
        self.progress.append(progress)

    async def upsert_video(self, task_id, ref):
        self.videos += 1
        return "video-1"

    async def video_evidence_count(self, video_id):
        return 0

    async def insert_evidence(self, packets):
        self.inserted += len(packets)
        self.count += len(packets)
        return len(packets)

    async def count_evidence(self, task_id):
        return self.count

    async def list_videos(self, task_id):
        return []

    async def list_evidence(self, task_id, limit=500):
        return []


class FakePipeline:
    def __init__(self, evidence: list[EvidencePacket], *, fail_first: bool = False) -> None:
        self.evidence = evidence
        self.fail_first = fail_first
        self.calls = 0

    async def parse_video(self, *, task_id, video_id, ref, progress=None, mode="quality"):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise IngestError("NO_EVIDENCE", "第一条失败")
        if progress:
            result = progress("subtitle", 1, 1, "ok")
            if hasattr(result, "__await__"):
                await result
        return ParseResult(ref=ref, evidence=self.evidence, warnings=["warn"])


class FakeSources:
    async def normalize(self, url: str) -> VideoRef:
        return _ref()


def _task(status: str = "PENDING") -> dict:
    return {
        "id": "task-1",
        "status": status,
        "progress": 0,
        "input_json": {"videos": [_ref().model_dump(mode="json")]},
        "error_code": None,
        "error_message": None,
        "evidence_count": 0,
        "created_at": None,
        "updated_at": None,
    }


def _service(repo: FakeRepository, pipeline: FakePipeline) -> ParseService:
    return ParseService(repository=repo, pipeline=pipeline, sources=FakeSources())


@pytest.mark.asyncio
async def test_create_task_stores_normalized_refs():
    repo = FakeRepository()
    service = _service(repo, FakePipeline([]))
    task_id = await service.create_task(["https://www.bilibili.com/video/BV1xx411c7mD"])
    assert task_id == "task-1"
    assert repo.created_input["videos"][0]["source"] == "bilibili"
    assert repo.created_input["videos"][0]["source_video_id"] == "BV1xx411c7mD"


@pytest.mark.asyncio
async def test_parse_success_moves_to_planning():
    repo = FakeRepository(task=_task())
    pipeline = FakePipeline([_packet()])
    result = await _service(repo, pipeline).parse_task("task-1")
    assert result["status"] == "PLANNING"
    assert result["evidence_count"] == 1
    assert result["failures"] == []
    assert ("PENDING", "PARSING") in repo.transitions
    assert ("PARSING", "PLANNING") in repo.transitions
    assert repo.progress


@pytest.mark.asyncio
async def test_parse_without_evidence_marks_failed():
    repo = FakeRepository(task=_task())
    pipeline = FakePipeline([])
    result = await _service(repo, pipeline).parse_task("task-1")
    assert result["status"] == "FAILED"
    assert ("PARSING", "FAILED") in repo.transitions


@pytest.mark.asyncio
async def test_parse_invalid_state_rejected():
    repo = FakeRepository(task=_task("DONE"))
    with pytest.raises(InvalidTaskState):
        await _service(repo, FakePipeline([])).parse_task("task-1")


@pytest.mark.asyncio
async def test_parse_continues_after_one_video_fails():
    repo = FakeRepository(task=_task())
    repo.task["input_json"] = {
        "videos": [
            _ref().model_dump(mode="json"),
            _ref().model_dump(mode="json"),
        ]
    }
    pipeline = FakePipeline([_packet()], fail_first=True)
    result = await _service(repo, pipeline).parse_task("task-1")
    assert result["status"] == "PLANNING"
    assert len(result["failures"]) == 1
    assert pipeline.calls == 2


@pytest.mark.asyncio
async def test_create_task_stores_question():
    repo = FakeRepository()
    service = _service(repo, FakePipeline([]))
    await service.create_task(
        ["https://www.bilibili.com/video/BV1xx411c7mD"],
        question="人生第一辆车应不应该买二手？",
    )
    assert repo.created_input["question"] == "人生第一辆车应不应该买二手？"
