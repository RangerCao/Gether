"""视频发现编排：query planning → search → metadata → rank → select。"""

from __future__ import annotations

import asyncio
import logging
import time

from app.core.config import Settings
from app.discovery.metadata import BilibiliMetadataClient
from app.discovery.models import Candidate, SearchHit
from app.discovery.planning import build_queries, plan_discovery
from app.discovery.priority import PRIORITY_LABELS, weights_for
from app.discovery.ranking import rank_candidates
from app.discovery.repository import DiscoveryRepository
from app.discovery.rewrite import rewrite_question
from app.discovery.search import SearchError, SearchProvider
from app.discovery.selection import select_candidates
from app.index.embedding import EmbeddingProvider
from app.llm.base import LLMClient
from app.services.parse_service import ParseService

logger = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    pass


def filter_hits_by_min_views(
    hits: list[SearchHit], min_views: int
) -> tuple[list[SearchHit], dict]:
    """在排序/选择前执行播放量硬过滤。

    播放量未知的候选也排除，避免“只看 8000+”被未知值绕过。
    """
    if min_views <= 0:
        return list(hits), {"level": "off", "threshold": 0}
    kept = [
        hit for hit in hits
        if hit.views is not None and hit.views >= min_views
    ]
    removed = len(hits) - len(kept)
    return kept, {
        "level": "hard",
        "threshold": min_views,
        "before": len(hits),
        "after": len(kept),
        "removed": removed,
    }


class DiscoveryService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: DiscoveryRepository,
        search_providers: list[SearchProvider],
        metadata_client: BilibiliMetadataClient,
        embedder: EmbeddingProvider,
        llm: LLMClient,
        parse_service: ParseService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.search_providers = search_providers
        self.metadata_client = metadata_client
        self.embedder = embedder
        self.llm = llm
        self.parse_service = parse_service
        self._search_cache: dict[tuple[str, str, int], tuple[float, list[SearchHit]]] = {}

    async def _cached_search(
        self,
        provider: SearchProvider,
        query: str,
        limit: int,
    ) -> list[SearchHit]:
        key = (provider.name, query.strip().lower(), limit)
        ttl = max(self.settings.discovery_search_cache_ttl_seconds, 0)
        now = time.monotonic()
        cached = self._search_cache.get(key)
        if cached and cached[0] > now:
            logger.info("discovery_search_cache_hit provider=%s query=%s", provider.name, query)
            return list(cached[1])
        results = await provider.search(query, limit=limit)
        if ttl:
            self._search_cache[key] = (now + ttl, list(results))
        return results

    async def discover(
        self,
        *,
        question: str,
        max_videos: int | None = None,
        duration_budget_min: int | None = None,
        min_views: int | None = None,
        mode: str = "quality",
    ) -> dict:
        question = question.strip()
        if not question:
            raise DiscoveryError("研究问题不能为空")
        settings = self.settings
        if mode not in {"fast", "quality"}:
            mode = "quality"
        update: dict = {"mode": mode}
        if max_videos is not None:
            update["discovery_max_videos"] = max_videos
        if duration_budget_min is not None:
            update["discovery_duration_budget_min"] = duration_budget_min
        if min_views is not None:
            update["discovery_min_views"] = max(min_views, 0)
        settings = settings.model_copy(update=update)
        if max_videos is None and mode == "fast":
            settings = settings.model_copy(
                update={"discovery_max_videos": min(settings.discovery_max_videos, 4)}
            )

        rewritten = await rewrite_question(self.llm, question)
        plan = await plan_discovery(
            self.llm,
            rewritten.rewritten,
            queries_per_facet=settings.discovery_queries_per_facet,
            entities=rewritten.entities,
            must_have_any=rewritten.must_have,
            exclude_topics=[],
        )
        facets = plan.facets
        negative_terms = list(
            dict.fromkeys(plan.negative_terms + rewritten.avoid)
        )
        ranking = {
            "priority": rewritten.priority,
            "label": PRIORITY_LABELS.get(rewritten.priority, "均衡"),
            "weights": weights_for(rewritten.priority),
            "reason": rewritten.priority_reason,
        }
        # 关键词也作为独立检索词：B站搜索会命中视频标签，而不是宽泛分区。
        keyword_queries = [
            keyword for keyword in rewritten.keywords
            if len(keyword.strip()) >= 2
        ][:4]
        queries = list(
            dict.fromkeys(
                [rewritten.rewritten]
                + build_queries(
                    facets,
                    rewritten.rewritten,
                    per_facet=settings.discovery_queries_per_facet,
                )
                + keyword_queries
            )
        )[:10]
        session_id = await self.repository.create_session(
            question=question,
            constraints={
                "max_videos": settings.discovery_max_videos,
                "min_views": settings.discovery_min_views,
                "duration_budget_min": settings.discovery_duration_budget_min,
                "negative_terms": negative_terms,
                "intent": rewritten.intent,
                "mode": mode,
                "rewritten_question": rewritten.rewritten,
                "entities": plan.entities,
                "must_have_any": plan.must_have_any,
                "exclude_topics": plan.exclude_topics,
                "ranking": ranking,
            },
            queries=queries,
            facets=facets,
        )

        warnings: list[str] = []
        hits: list[SearchHit] = []
        seen: set[tuple[str, str]] = set()
        blocked_providers: set[str] = set()
        provider_failures: dict[str, int] = {}
        for query_index, query in enumerate(queries):
            if query_index:
                await asyncio.sleep(settings.discovery_query_interval_seconds)
            for provider in self.search_providers:
                if provider.name in blocked_providers:
                    continue
                try:
                    results = await self._cached_search(
                        provider, query, settings.discovery_search_results_per_query
                    )
                except SearchError as exc:
                    warnings.append(f"{provider.name} 搜索失败：{exc}")
                    if any(token in str(exc) for token in ("412", "429")):
                        provider_failures[provider.name] = provider_failures.get(provider.name, 0) + 1
                        # 首次 412 可能是瞬时风控，给下一个 query 一次机会；
                        # 连续两次限流才熔断，避免第一条 query 失败就 0 候选。
                        if provider_failures[provider.name] >= 2:
                            blocked_providers.add(provider.name)
                            warnings.append(
                                f"{provider.name} 连续触发限流，本次会话停止继续请求"
                            )
                    continue
                for hit in results:
                    key = (hit.source, hit.source_video_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(hit)

        if not hits:
            await self.repository.set_warnings(session_id, warnings + ["未搜索到候选视频"])
            return {
                "session_id": session_id,
                "question": question,
                "facets": [facet.as_dict() for facet in facets],
                "queries": queries,
                "negative_terms": negative_terms,
                "rewritten_question": rewritten.rewritten,
                "ranking": ranking,
                "candidates": [],
                "selected": [],
                "sufficiency": {"ok": False, "warnings": warnings + ["未搜索到候选视频"]},
            }

        # B站 API 搜索结果已带播放/时长/简介，无需再逐条请求 view 接口；
        # 仅对缺少元数据的来源做 Top N 补全。
        api_hits = [hit for hit in hits if hit.metrics.get("bilibili_api")]
        enrich = [
            hit for hit in hits if not hit.metrics.get("bilibili_api")
        ][: settings.discovery_metadata_top_n]
        enriched = await self.metadata_client.fetch_many(enrich) if enrich else []
        candidate_hits = api_hits + enriched
        if not candidate_hits:
            warnings.append("元数据补全失败，无法进行播放量/权威度排序")
            await self.repository.set_warnings(session_id, warnings)
            return {
                "session_id": session_id,
                "question": question,
                "facets": [facet.as_dict() for facet in facets],
                "queries": queries,
                "negative_terms": negative_terms,
                "rewritten_question": rewritten.rewritten,
                "ranking": ranking,
                "candidates": [],
                "selected": [],
                "sufficiency": {"ok": False, "warnings": warnings},
            }

        # 播放量硬过滤必须发生在 Topic Gate / 排序 / coverage 选择之前。
        # 低于阈值或播放量未知的候选都不进入候选池。
        min_views = settings.discovery_min_views
        candidate_hits, min_views_info = filter_hits_by_min_views(
            candidate_hits, min_views
        )
        if min_views_info.get("level") == "hard" and not candidate_hits:
            warnings.append(
                f"播放量过滤后没有剩余候选（硬门槛 {min_views}）"
            )

        candidates = [_to_candidate(hit) for hit in candidate_hits]
        candidates = await rank_candidates(
            candidates,
            facets,
            rewritten.rewritten,
            self.embedder,
            facet_match_threshold=settings.discovery_facet_match_threshold,
            facet_lexical_weight=settings.discovery_facet_lexical_weight,
            facet_embedding_weight=settings.discovery_facet_embedding_weight,
            negative_terms=negative_terms,
            freshness_half_life_days=settings.discovery_freshness_half_life_days,
            priority=rewritten.priority,
            entities=plan.entities or rewritten.entities,
            must_have_any=plan.must_have_any or rewritten.must_have,
            exclude_topics=plan.exclude_topics,
            keywords=list(
                dict.fromkeys(
                    rewritten.keywords + plan.entities + plan.must_have_any
                )
            ),
            topic_semantic_threshold=settings.discovery_topic_semantic_threshold,
        )
        selected, sufficiency = select_candidates(
            candidates, facets, settings, intent=rewritten.intent
        )
        sufficiency["warnings"] = warnings + sufficiency.get("warnings", [])
        if min_views_info is not None:
            sufficiency["filter"] = {
                **sufficiency.get("filter", {}),
                "min_views": min_views_info,
            }
        sufficiency["ok"] = bool(selected) and not sufficiency["warnings"]

        await self.repository.save_candidates(session_id, candidates)
        await self.repository.set_warnings(session_id, sufficiency["warnings"])
        return {
            "session_id": session_id,
            "question": question,
            "facets": [facet.as_dict() for facet in facets],
            "queries": queries,
            "negative_terms": negative_terms,
            "rewritten_question": rewritten.rewritten,
            "ranking": ranking,
            "candidates": [item.as_dict() for item in candidates],
            "selected": [item.as_dict() for item in selected],
            "sufficiency": sufficiency,
        }

    async def get_session(self, session_id: str) -> dict:
        session = await self.repository.get_session(session_id)
        if session is None:
            raise DiscoveryError("发现会话不存在")
        return session

    async def confirm(self, session_id: str, selected_ids: list[str] | None = None) -> str:
        session = await self.get_session(session_id)
        if session["status"] == "CONFIRMED" and session["task_id"]:
            return session["task_id"]
        ids = selected_ids or [
            item["candidate_id"] for item in session["candidates"] if item["selected"]
        ]
        if not ids:
            raise DiscoveryError("没有选中的视频")
        by_id = {item["candidate_id"]: item for item in session["candidates"]}
        urls = [by_id[item]["url"] for item in ids if item in by_id]
        if not urls:
            raise DiscoveryError("选中的视频不存在")
        task_id = await self.parse_service.create_task(
            urls,
            question=session["question"],
            mode=(session.get("constraints") or {}).get("mode", "quality"),
        )
        await self.repository.confirm(session_id, task_id, ids)
        return task_id

    async def aclose(self) -> None:
        await self.metadata_client.aclose()


def _to_candidate(hit: SearchHit) -> Candidate:
    return Candidate(
        source=hit.source,
        source_video_id=hit.source_video_id,
        url=hit.url,
        title=hit.title,
        duration_ms=hit.duration_ms,
        uploader=hit.uploader,
        published_at=hit.published_at,
        views=hit.views,
        description=hit.description,
        metrics={
            "likes": hit.likes or 0,
            "coins": hit.coins or 0,
            "favorites": hit.favorites or 0,
            **hit.metrics,
        },
    )
