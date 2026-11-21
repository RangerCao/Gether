"""候选视频排序：Topic Gate + 可解释的 mR + nA + pF 公式。"""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, datetime

from app.discovery.content import classify_content_types, credibility_score
from app.discovery.models import Candidate, Facet
from app.discovery.priority import weights_for
from app.index.bm25 import BM25Index
from app.index.embedding import EmbeddingProvider


def _percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = sum(1 for item in ordered if item <= value)
    return rank / len(ordered)


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _duration_penalty(duration_ms: int | None) -> float:
    if not duration_ms:
        return 0.2
    minutes = duration_ms / 60000
    if minutes < 8:
        return (8 - minutes) / 8
    if minutes > 45:
        return (minutes - 45) / 45
    return 0.0


def _freshness(published_at: int | None, half_life_days: int = 180) -> float:
    if not published_at:
        return 0.5
    age_days = max((time.time() - published_at) / 86400, 0)
    return math.exp(-age_days / half_life_days)


def _compact(text: str) -> str:
    return re.sub(r"[\s\-_/·|,，。！？!?：:；;（）()\[\]【】]+", "", text.lower())


GENERIC_ANCHORS = {
    "就业", "就业前景", "前景", "岗位", "趋势", "工作", "行业",
    "影响", "成本", "团队", "适用性", "效率", "开发效率",
    "风险", "安全", "问题", "案例", "对比",
}


def _term_hit(term: str, text: str) -> bool:
    compact_term = _compact(term)
    return bool(compact_term) and compact_term in _compact(text)


def _variants(term: str) -> list[str]:
    """主题实体允许省略通用后缀：AI编程工具 -> AI编程。"""
    term = term.strip()
    if not term:
        return []
    variants = [term]
    for suffix in ("工具", "助手", "平台", "软件", "产品", "应用", "方案"):
        if term.endswith(suffix) and len(_compact(term[:-len(suffix)])) >= 2:
            variants.append(term[: -len(suffix)])
    return variants


def _hit_any(terms: list[str], text: str) -> bool:
    return any(
        _term_hit(variant, text)
        for term in terms
        if term.strip()
        for variant in _variants(term)
    )


def _hit_terms(terms: list[str], text: str) -> list[str]:
    return [term for term in terms if _hit_any([term], text)]


async def rank_candidates(
    candidates: list[Candidate],
    facets: list[Facet],
    question: str,
    embedder: EmbeddingProvider,
    *,
    facet_match_threshold: float = 0.55,
    facet_lexical_weight: float = 0.5,
    facet_embedding_weight: float = 0.5,
    negative_terms: list[str] | None = None,
    freshness_half_life_days: int = 180,
    priority: str = "balanced",
    entities: list[str] | None = None,
    must_have_any: list[str] | None = None,
    exclude_topics: list[str] | None = None,
    keywords: list[str] | None = None,
    topic_semantic_threshold: float = 0.72,
) -> list[Candidate]:
    if not candidates:
        return []
    texts = [item.text for item in candidates]
    # 用户问题与视频标题做向量匹配；描述只参与 BM25，避免简介噪声
    embeddings = await embedder.embed_documents(
        [item.title or item.text for item in candidates]
    )
    question_vector = await embedder.embed_query(question)
    facet_vectors = [await embedder.embed_query(facet.query) for facet in facets]

    bm25 = BM25Index(texts)
    facet_hits = [
        {hit.index: hit.score for hit in bm25.search(facet.query, top_k=len(texts))}
        for facet in facets
    ]

    view_values = [math.log1p(item.views or 0) for item in candidates]
    engagement_values = [
        (item.metrics.get("likes", item.metrics.get("like", 0)) or 0)
        + (item.metrics.get("coins", 0) or 0)
        + (item.metrics.get("favorites", 0) or 0)
        for item in candidates
    ]
    engagement_rates = [
        engagement / max(item.views or 0, 1)
        for item, engagement in zip(candidates, engagement_values, strict=True)
    ]

    anchors = list(entities or []) + list(must_have_any or [])
    excludes = list(exclude_topics or [])
    weights = weights_for(priority)
    m, n, p = weights["m"], weights["n"], weights["p"]

    for index, item in enumerate(candidates):
        title_text = f"{item.title}\n{item.description}"
        question_semantic = (_cosine(question_vector, embeddings[index]) + 1) / 2

        entity_terms = [
            term for term in (entities or [])
            if _compact(term) not in GENERIC_ANCHORS
        ]
        entity_hits = _hit_terms(entity_terms, title_text)
        must_hits = _hit_terms(list(must_have_any or []), title_text)
        anchor_hits = list(dict.fromkeys(entity_hits + must_hits))
        # 只按标题排除“安装/教程”等噪声，避免简介里的一个词误杀内容型视频。
        exclude_hits = _hit_terms(excludes, item.title)
        # 有实体时必须命中实体；通用 must_have（如“就业/影响”）不能单独放行。
        anchor_hit = bool(entity_hits) if entity_terms else bool(must_hits)
        anchor_strength = 1.0 if anchor_hit else 0.0
        if not anchors:
            topic_score = question_semantic
        else:
            topic_score = 0.60 * anchor_strength + 0.40 * question_semantic

        if exclude_hits:
            topic_gate = False
            rejection_reason = "excluded_topic"
        elif not anchors:
            topic_gate = True
            rejection_reason = None
        else:
            # 有主题锚点时，必须命中锚点；语义相似度不能单独放行，
            # 否则相邻 AI 领域会被高相似度误选。
            topic_gate = anchor_hit
            rejection_reason = None if topic_gate else "topic_mismatch"

        matched: list[str] = []
        facet_scores: list[float] = []
        facet_confidence: dict[str, float] = {}
        for facet_index, facet in enumerate(facets):
            lexical = facet_hits[facet_index].get(index, 0.0) / (
                max(facet_hits[facet_index].values(), default=1.0) or 1.0
            )
            semantic = (_cosine(facet_vectors[facet_index], embeddings[index]) + 1) / 2
            score = (
                facet_lexical_weight * lexical
                + facet_embedding_weight * semantic
            )
            facet_scores.append(score)
            facet_confidence[facet.id] = score
            required_ok = not facet.required_any or _hit_any(facet.required_any, title_text)
            excluded = bool(facet.exclude_any) and _hit_any(facet.exclude_any, item.title)
            # 负向 facet 必须显式命中风险词，避免把“支持观点”算成反方；
            # 正向/中性 facet 允许主题门通过后按语义匹配，避免漏掉综述型视频。
            facet_ok = score >= facet_match_threshold and not excluded
            if facet.stance == "negative":
                facet_ok = facet_ok and required_ok
            if facet_ok:
                matched.append(facet.id)

        best_facet = max(facet_scores, default=0.0)
        relevance = 0.55 * topic_score + 0.30 * best_facet + 0.15 * anchor_strength
        content_types = classify_content_types(item.title, item.description)
        credibility = credibility_score(content_types, item.title)
        authority = (
            0.50 * _percentile(view_values, view_values[index])
            + 0.30 * _percentile(engagement_rates, engagement_rates[index])
            + 0.20 * credibility
        )
        freshness = _freshness(item.published_at, freshness_half_life_days)
        age_days = (
            max((time.time() - item.published_at) / 86400, 0)
            if item.published_at
            else None
        )
        duration_penalty = _duration_penalty(item.duration_ms)
        content_type = content_types[0]
        tag_text = str(item.metrics.get("tag") or "")
        tag_hits = [
            keyword for keyword in (keywords or [])
            if keyword.strip() and _term_hit(keyword, tag_text)
        ]
        tag_score = min(len(tag_hits) / max(len(keywords or []), 1), 1.0)
        text_lower = title_text.lower()
        noise_hits = [
            term for term in (negative_terms or []) if term.lower() in text_lower
        ]

        item.matched_facets = matched
        item.relevance = max(0.0, min(1.0, relevance))
        item.authority = authority
        item.freshness = freshness
        item.topic_score = max(0.0, min(1.0, topic_score))
        item.topic_gate = topic_gate
        item.rejection_reason = rejection_reason
        item.facet_confidence = facet_confidence
        item.final_score = m * item.relevance + n * authority + p * freshness
        item.metrics.update(
            {
                "topic_score": round(item.topic_score, 4),
                "topic_gate": topic_gate,
                "rejection_reason": rejection_reason,
                "anchor_hits": anchor_hits,
                "entity_hits": entity_hits,
                "must_have_hits": must_hits,
                "exclude_hits": exclude_hits,
                "facet_scores": {
                    facet.id: round(score, 4)
                    for facet, score in zip(facets, facet_scores, strict=True)
                },
                "facet_confidence": {
                    key: round(value, 4) for key, value in facet_confidence.items()
                },
                "best_facet": round(best_facet, 4),
                "credibility": round(credibility, 4),
                "tag_hits": tag_hits,
                "tag_score": round(tag_score, 4),
                "title_semantic": round(question_semantic, 4),
                "age_days": round(age_days, 1) if age_days is not None else None,
                "freshness": round(freshness, 4),
                "engagement_rate": round(engagement_rates[index], 5),
                "duration_penalty": round(duration_penalty, 4),
                "content_type": content_type,
                "content_types": content_types,
                "noise_terms": noise_hits,
                "ranking_priority": priority,
                "ranking_weights": weights,
                "score_breakdown": {
                    "relevance": round(item.relevance, 4),
                    "authority": round(authority, 4),
                    "freshness": round(freshness, 4),
                    "m": m,
                    "n": n,
                    "p": p,
                },
            }
        )

    # 先按公式排序，再做候选池内的冗余/聚类标记；冗余不进入主分数。
    ordered = sorted(
        enumerate(candidates),
        key=lambda pair: (pair[1].topic_gate, pair[1].final_score),
        reverse=True,
    )
    kept: list[tuple[Candidate, list[float]]] = []
    for index, item in ordered:
        vector = embeddings[index]
        max_similarity = max((_cosine(vector, other) for _, other in kept), default=0.0)
        item.redundancy = max(0.0, (max_similarity - 0.75) / 0.25)
        item.cluster_id = (
            next(
                (
                    candidate.cluster_id
                    for candidate, other in kept
                    if _cosine(vector, other) >= 0.9
                ),
                None,
            )
            or item.candidate_id
        )
        kept.append((item, vector))
    return sorted(
        (item for _, item in ordered),
        key=lambda item: (item.topic_gate, item.final_score),
        reverse=True,
    )


def freshness_now() -> str:
    return datetime.now(UTC).isoformat()
