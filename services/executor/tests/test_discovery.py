import pytest

from app.core.config import Settings
from app.discovery.models import Candidate, Facet, SearchHit
from app.discovery.planning import plan_facets
from app.discovery.ranking import rank_candidates
from app.discovery.selection import select_candidates
from app.discovery.service import filter_hits_by_min_views
from app.index.embedding import MockEmbeddingProvider


class FakeLLM:
    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        return '{"sections":[{"id":"s1","title":"只返回章节格式"}]}'

    async def stream(self, role, messages, *, max_tokens=None):
        if False:
            yield ""

    async def aclose(self):
        return None


def _settings(**kwargs) -> Settings:
    values = {
        "discovery_min_videos": 2,
        "discovery_max_videos": 3,
        "discovery_hard_max_videos": 5,
        "discovery_duration_budget_min": 120,
    }
    values.update(kwargs)
    return Settings(_env_file=None, llm_provider="mock", **values)


def _candidate(
    title: str,
    *,
    views: int,
    relevance: float = 0.8,
    authority: float = 0.8,
    freshness: float = 0.9,
    facets: list[str] | None = None,
    cluster: str = "c1",
    duration_min: int = 20,
    engagement: float = 0.05,
) -> Candidate:
    return Candidate(
        source="bilibili",
        source_video_id=title,
        url=f"https://www.bilibili.com/video/{title}",
        title=title,
        views=views,
        duration_ms=duration_min * 60000,
        relevance=relevance,
        authority=authority,
        freshness=freshness,
        final_score=0.7,
        matched_facets=facets or [],
        cluster_id=cluster,
        metrics={"engagement_rate": engagement},
    )


@pytest.mark.asyncio
async def test_plan_facets_falls_back_when_llm_schema_mismatch():
    facets = await plan_facets(FakeLLM(), "AI 编程工具真的能提升效率吗")
    assert facets
    assert facets[0].query == "AI 编程工具真的能提升效率吗"


@pytest.mark.asyncio
async def test_rank_candidates_scores_and_clusters():
    candidates = [
        _candidate("杭州房租很高 隔断房很多", views=20000, cluster="a"),
        _candidate("模型训练需要显卡", views=15000, cluster="b"),
    ]
    facets = [Facet("f1", "房租", "杭州房租")]
    ranked = await rank_candidates(candidates, facets, "杭州房租为什么高", MockEmbeddingProvider(64))
    assert len(ranked) == 2
    assert ranked[0].relevance > 0
    assert ranked[0].final_score >= ranked[1].final_score


def test_selection_uses_facet_coverage():
    c1 = _candidate("视频一", views=20000, facets=["f1", "f2"], cluster="a")
    c2 = _candidate("视频二", views=15000, facets=["f1"], cluster="b")
    c3 = _candidate("视频三", views=12000, facets=["f2"], cluster="c")
    selected, info = select_candidates(
        [c1, c2, c3],
        [Facet("f1", "效率", "效率"), Facet("f2", "风险", "风险")],
        _settings(),
    )
    assert len(selected) >= 2
    assert info["coverage"] == 1.0
    assert all(item.selected for item in selected)


def test_selection_falls_back_when_high_view_candidates_insufficient():
    c1 = _candidate("高播放", views=20000, facets=["f1"], cluster="a", authority=0.9)
    c2 = _candidate(
        "中等播放", views=5000, facets=["f2"], cluster="b", authority=0.0, engagement=0.0
    )
    selected, info = select_candidates(
        [c1, c2],
        [Facet("f1", "效率", "效率"), Facet("f2", "风险", "风险")],
        _settings(),
    )
    assert len(selected) == 2
    assert info["filter"]["level"] == "soft"


def test_selection_opens_threshold_when_all_low_views():
    c1 = _candidate("低播放一", views=500, facets=["f1"], cluster="a", authority=0.0, engagement=0.0)
    c2 = _candidate("低播放二", views=800, facets=["f2"], cluster="b", authority=0.0, engagement=0.0)
    selected, info = select_candidates(
        [c1, c2],
        [Facet("f1", "效率", "效率"), Facet("f2", "风险", "风险")],
        _settings(),
    )
    assert len(selected) == 1
    assert info["filter"]["level"] == "soft"
    assert any("低播放" in warning for warning in info["warnings"])


def test_candidate_from_search_hit_keeps_metrics():
    hit = SearchHit(
        source="bilibili",
        source_video_id="BV1xx411c7mD",
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        title="标题",
        views=10000,
        likes=100,
        coins=20,
        favorites=30,
    )
    assert hit.likes == 100
    assert hit.coins == 20


def test_content_type_and_intent():
    from app.discovery.content import classify_content_type, classify_intent

    assert classify_content_type("Cursor 安装教程，手把手带你入门") == "tutorial"
    assert classify_content_type("Science 论文研究：AI 编程效率数据") == "research"
    assert classify_content_type("Cursor 实测对比：效率提升多少") == "experience"
    assert classify_intent("AI 编程工具真的能提升效率吗？") == "evaluation"
    assert classify_intent("如何安装 Cursor？") == "howto"


def test_decision_intent_and_fallback_facets():
    from app.discovery.content import classify_intent
    from app.discovery.planning import _fallback

    assert classify_intent("人生第一辆车应不应该买二手？") == "decision"
    plan = _fallback("人生第一辆车应不应该买二手？")
    assert any("支持" in facet.label for facet in plan.facets)
    assert "教程" in plan.negative_terms


def test_freshness_age_filter():
    now = int(__import__("time").time())
    old = _candidate("旧视频", views=20000, facets=["f1"], cluster="a")
    old.published_at = now - 400 * 86400
    new = _candidate("新视频", views=20000, facets=["f1"], cluster="b")
    new.published_at = now - 5 * 86400
    selected, info = select_candidates(
        [old, new],
        [Facet("f1", "效率", "效率")],
        _settings(discovery_min_videos=1, discovery_max_videos=1, discovery_max_age_days=30),
    )
    assert selected[0].source_video_id == new.source_video_id
    assert info["filter"]["age"]["level"] == "max_age"


def test_priority_inference_profiles():
    from app.discovery.priority import infer_priority

    fresh = infer_priority("2026 年 AI 编程工具最新格局", "evaluation")
    assert fresh.name == "freshness"
    assert fresh.weights == {"m": 0.25, "n": 0.15, "p": 0.60}

    authority = infer_priority("有哪些权威研究或官方数据", "evaluation")
    assert authority.name == "authority"

    relevance = infer_priority("AI 编程工具真的能提升开发效率吗？", "evaluation")
    assert relevance.name == "relevance"

    balanced = infer_priority("介绍一下这个行业", "general")
    assert balanced.name == "balanced"


@pytest.mark.asyncio
async def test_topic_gate_rejects_adjacent_domain():
    from app.discovery.ranking import rank_candidates

    target = _candidate("4 款 AI 编程 Agent 实测对比", views=20000, cluster="a")
    target.description = "Cursor Copilot 编程开发效率"
    adjacent = _candidate("4 家 AI 短剧工具测评", views=50000, cluster="b")
    adjacent.description = "AI 短剧生成工具"
    facets = [Facet("f1", "效率", "AI 编程 效率", "positive", required_any=["编程"])]
    ranked = await rank_candidates(
        [target, adjacent],
        facets,
        "AI 编程工具真的能提升效率吗",
        MockEmbeddingProvider(64),
        entities=["AI 编程"],
        must_have_any=["编程"],
        priority="relevance",
    )
    by_title = {item.title: item for item in ranked}
    assert by_title[target.title].topic_gate is True
    assert by_title[adjacent.title].topic_gate is False
    assert by_title[adjacent.title].rejection_reason == "topic_mismatch"


@pytest.mark.asyncio
async def test_rank_formula_is_mr_plus_na_plus_pf():
    from app.discovery.ranking import rank_candidates

    candidate = _candidate("AI 编程效率实测", views=20000, cluster="a")
    ranked = await rank_candidates(
        [candidate],
        [Facet("f1", "效率", "AI 编程 效率")],
        "AI 编程 效率",
        MockEmbeddingProvider(64),
        priority="freshness",
    )
    item = ranked[0]
    expected = 0.25 * item.relevance + 0.15 * item.authority + 0.60 * item.freshness
    assert abs(item.final_score - expected) < 1e-6
    assert item.metrics["ranking_weights"] == {"m": 0.25, "n": 0.15, "p": 0.60}


def test_selection_enforces_duration_budget():
    long_a = _candidate("长视频一", views=20000, facets=["f1"], cluster="a", duration_min=61)
    long_b = _candidate("长视频二", views=20000, facets=["f2"], cluster="b", duration_min=61)
    selected, info = select_candidates(
        [long_a, long_b],
        [Facet("f1", "效率", "效率"), Facet("f2", "风险", "风险")],
        _settings(discovery_min_videos=1, discovery_max_videos=2),
    )
    assert selected == []
    assert any("单视频上限" in warning for warning in info["warnings"])


def test_selection_warns_when_negative_missing():
    positive = _candidate("只有支持观点", views=20000, facets=["f1"], cluster="a")
    positive.metrics.update(content_types=["opinion"], content_type="opinion")
    selected, info = select_candidates(
        [positive],
        [
            Facet("f1", "支持", "支持", "positive"),
            Facet("f2", "风险", "风险", "negative"),
        ],
        _settings(discovery_min_videos=1, discovery_max_videos=2),
        intent="evaluation",
    )
    assert len(selected) == 1
    assert any("缺少反方" in warning for warning in info["warnings"])


@pytest.mark.asyncio
async def test_search_cache_hits_same_provider_query():
    from app.discovery.service import DiscoveryService

    class FakeProvider:
        name = "fake"

        def __init__(self):
            self.calls = 0

        async def search(self, query: str, limit: int = 20):
            self.calls += 1
            return [SearchHit(source="bilibili", source_video_id=query, url="https://example.com")]

    service = DiscoveryService.__new__(DiscoveryService)
    service.settings = _settings(discovery_search_cache_ttl_seconds=60)
    service._search_cache = {}
    provider = FakeProvider()
    first = await service._cached_search(provider, "AI 编程", 20)  # type: ignore[arg-type]
    second = await service._cached_search(provider, "AI 编程", 20)  # type: ignore[arg-type]
    assert provider.calls == 1
    assert first == second


def test_bilibili_api_result_parser():
    from app.discovery.bilibili_api import duration_ms, parse_api_results

    hits = parse_api_results(
        [
            {
                "bvid": "BV1xx411c7mD",
                "title": '程序员必看 <em class="keyword">AI 编程</em> 工具',
                "author": "测试作者",
                "play": 12345,
                "favorites": 234,
                "duration": "1:02:03",
                "pubdate": 1700000000,
                "description": "简介",
                "aid": 1,
                "tag": "AI编程,效率,工具",
            },
            {"title": "无 bvid 的广告"},
        ],
        20,
    )
    assert len(hits) == 1
    assert hits[0].title == "程序员必看 AI 编程 工具"
    assert hits[0].duration_ms == 3723000
    assert hits[0].views == 12345
    assert hits[0].metrics["bilibili_api"] is True
    assert hits[0].metrics["tag"] == "AI编程,效率,工具"
    assert duration_ms("5:20") == 320000


def test_min_views_filter_runs_before_ranking():
    hits = [
        SearchHit("bilibili", "low", "u1", views=7999),
        SearchHit("bilibili", "edge", "u2", views=8000),
        SearchHit("bilibili", "unknown", "u3", views=None),
    ]
    kept, info = filter_hits_by_min_views(hits, 8000)
    assert [hit.source_video_id for hit in kept] == ["edge"]
    assert info == {
        "level": "hard",
        "threshold": 8000,
        "before": 3,
        "after": 1,
        "removed": 2,
    }
