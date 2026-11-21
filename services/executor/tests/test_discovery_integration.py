import os

import pytest

from app.core.config import Settings
from app.discovery.models import SearchHit
from app.discovery.repository import DiscoveryRepository
from app.discovery.service import DiscoveryService
from app.index.embedding import MockEmbeddingProvider
from app.repository import TaskRepository

TEST_DATABASE_URL = os.getenv("GETHER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="需要 GETHER_TEST_DATABASE_URL"),
]


class FakeLLM:
    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        return (
            '{"facets":['
            '{"id":"f1","label":"效率","query":"AI 编程 效率","stance":"positive"},'
            '{"id":"f2","label":"风险","query":"AI 编程 风险","stance":"negative"}'
            "]}"
        )

    async def stream(self, role, messages, *, max_tokens=None):
        if False:
            yield ""

    async def aclose(self):
        return None


class FakeSearch:
    name = "bilibili"

    async def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        return [
            SearchHit("bilibili", "BV1", "https://www.bilibili.com/video/BV1"),
            SearchHit("bilibili", "BV2", "https://www.bilibili.com/video/BV2"),
            SearchHit("bilibili", "BV3", "https://www.bilibili.com/video/BV3"),
        ]


class FakeMetadata:
    async def fetch_many(self, hits: list[SearchHit]) -> list[SearchHit]:
        data = {
            "BV1": ("AI 编程 效率 实测", 30000, 20 * 60, 1000, 0.08),
            "BV2": ("AI 编程 风险 翻车 案例", 15000, 25 * 60, 800, 0.06),
            "BV3": ("低播放 视频", 500, 10 * 60, 5, 0.0),
        }
        output = []
        for hit in hits:
            title, views, duration, likes, rate = data[hit.source_video_id]
            output.append(
                SearchHit(
                    "bilibili",
                    hit.source_video_id,
                    hit.url,
                    title=title,
                    views=views,
                    duration_ms=duration * 1000,
                    likes=likes,
                    coins=0,
                    favorites=0,
                    published_at=1700000000,
                    metrics={"engagement_rate": rate},
                )
            )
        return output

    async def aclose(self):
        return None


class FakeParse:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    async def create_task(self, urls, user_id=None, question=None):
        assert urls
        self.question = question
        return self.task_id


@pytest.mark.asyncio
async def test_discovery_service_and_repository_roundtrip():
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        database_url=TEST_DATABASE_URL,
        discovery_min_videos=2,
        discovery_max_videos=3,
        discovery_metadata_top_n=10,
    )
    repository = DiscoveryRepository(settings)
    task_id = await TaskRepository(settings).create_task({"test": True})
    service = DiscoveryService(
        settings=settings,
        repository=repository,
        search_providers=[FakeSearch()],
        metadata_client=FakeMetadata(),  # type: ignore[arg-type]
        embedder=MockEmbeddingProvider(64),
        llm=FakeLLM(),  # type: ignore[arg-type]
        parse_service=FakeParse(task_id),  # type: ignore[arg-type]
    )
    result = await service.discover(question="AI 编程工具真的能提升效率吗")
    assert result["selected"]
    assert len(result["selected"]) >= 2
    assert all(item["views"] >= 8000 for item in result["selected"])
    session = await service.get_session(result["session_id"])
    # BV3 只有 500 播放，应在排序/选择之前被硬过滤掉。
    assert len(session["candidates"]) == 2
    task_id = await service.confirm(result["session_id"])
    assert task_id
    confirmed = await service.get_session(result["session_id"])
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["task_id"] == task_id
