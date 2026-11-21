"""候选视频筛选与集合选择：Topic Gate + 时长/反方/低播放约束 + MMR。"""

from __future__ import annotations

import math

from app.core.config import Settings
from app.discovery.models import Candidate, Facet

DEFAULT_ESTIMATED_MINUTES = 20.0


def select_candidates(
    candidates: list[Candidate],
    facets: list[Facet],
    settings: Settings,
    *,
    intent: str = "general",
) -> tuple[list[Candidate], dict]:
    warnings: list[str] = []

    candidates, topic_info = _filter_by_topic(candidates, settings, warnings)
    candidates, age_info = _filter_by_age(candidates, settings, warnings)
    filtered, filter_info = _filter_by_views(candidates, facets, settings, warnings)
    filter_info = {
        **filter_info,
        "topic": topic_info,
        "age": age_info,
    }
    if not filtered:
        return [], {
            "ok": False,
            "coverage": 0.0,
            "warnings": warnings + ["没有通过过滤的候选视频"],
            "filter": filter_info,
        }

    n_target = _target_count(filtered, facets, settings)
    selected: list[Candidate] = []
    covered: set[str] = set()

    def total_duration() -> float:
        return sum(_estimated_minutes(item) for item in selected)

    def fits(item: Candidate) -> bool:
        estimated = _estimated_minutes(item)
        if settings.discovery_max_video_minutes > 0 and estimated > settings.discovery_max_video_minutes:
            return False
        return total_duration() + estimated <= settings.discovery_duration_budget_min

    def add(item: Candidate) -> None:
        item.selected = True
        item.reason = _reason(item, facets)
        selected.append(item)
        covered.update(item.matched_facets)

    # 1) 从全局候选做 coverage-aware 贪心选择，而不是先锁死 facet 代表
    while len(selected) < n_target:
        pool = [item for item in filtered if item not in selected and fits(item)]
        if not pool:
            break
        best = max(pool, key=lambda item: _select_score(item, covered, facets, selected, settings))
        if _select_score(best, covered, facets, selected, settings) < settings.discovery_select_min_score:
            break
        add(best)

    if not selected and filtered:
        too_long = all(
            settings.discovery_max_video_minutes > 0
            and _estimated_minutes(item) > settings.discovery_max_video_minutes
            for item in filtered
        )
        if too_long:
            warnings.append(
                f"所有候选均超过单视频上限 {settings.discovery_max_video_minutes} 分钟"
            )

    # 2) 评估/决策型必须有反方或风险来源
    negative_facets = [facet for facet in facets if facet.stance == "negative"]
    if intent in {"evaluation", "decision"} and negative_facets:
        has_negative = any(
            any(facet.id in item.matched_facets for facet in negative_facets)
            for item in selected
        )
        if not has_negative:
            negative_pool = [
                item
                for item in filtered
                if item not in selected
                and any(facet.id in item.matched_facets for facet in negative_facets)
                and fits(item)
            ]
            if negative_pool:
                best = max(
                    negative_pool,
                    key=lambda item: _select_score(item, covered, facets, selected, settings),
                )
                if len(selected) >= n_target:
                    replaceable = [
                        item
                        for item in selected
                        if not any(facet.id in item.matched_facets for facet in negative_facets)
                    ]
                    if replaceable:
                        victim = min(
                            replaceable,
                            key=lambda item: _select_score(item, covered, facets, selected, settings),
                        )
                        victim.selected = False
                        selected.remove(victim)
                add(best)
            else:
                warnings.append("缺少反方/风险来源，研报冲突覆盖可能不足")

    # 4) 低播放例外最多保留 1 条
    low_view = [item for item in selected if _is_low_view(item, settings)]
    if len(low_view) > 1:
        keep = max(low_view, key=lambda item: item.final_score)
        for item in low_view:
            if item is not keep:
                item.selected = False
                selected.remove(item)
        warnings.append("低播放例外超过 1 条，已保留分数最高的一条")

    # 低播放裁剪可能腾出名额，优先用正常播放候选补位，不再放回低播放。
    while len(selected) < n_target:
        pool = [
            item
            for item in filtered
            if item not in selected
            and not _is_low_view(item, settings)
            and fits(item)
        ]
        if not pool:
            break
        best = max(pool, key=lambda item: _select_score(item, covered, facets, selected, settings))
        if _select_score(best, covered, facets, selected, settings) < settings.discovery_select_min_score:
            break
        add(best)

    coverage = len(covered) / len(facets) if facets else 0.0
    top_relevance = max((item.relevance for item in filtered), default=0.0)
    if len(selected) < settings.discovery_min_videos:
        warnings.append(
            f"仅选出 {len(selected)} 个视频，低于最小数量 {settings.discovery_min_videos}"
        )
    if coverage < settings.discovery_min_facet_coverage:
        warnings.append(
            f"facet 覆盖率 {coverage:.0%} 低于阈值 {settings.discovery_min_facet_coverage:.0%}"
        )
    if top_relevance < settings.discovery_min_relevance:
        warnings.append(
            f"最高相关性 {top_relevance:.2f} 低于阈值 {settings.discovery_min_relevance}"
        )
    if total_duration() > settings.discovery_duration_budget_min:
        warnings.append(
            f"选中视频总时长 {total_duration():.0f} 分钟，超过预算 "
            f"{settings.discovery_duration_budget_min} 分钟"
        )
    return selected, {
        "ok": not warnings,
        "coverage": round(coverage, 4),
        "warnings": warnings,
        "filter": {
            **filter_info,
            "duration": {
                "level": "budgeted",
                "budget_minutes": settings.discovery_duration_budget_min,
                "selected_minutes": round(total_duration(), 1),
            },
            "low_view": {
                "level": "capped",
                "count": len([item for item in selected if _is_low_view(item, settings)]),
            },
        },
        "target_count": n_target,
    }


def _filter_by_topic(
    candidates: list[Candidate],
    settings: Settings,
    warnings: list[str],
) -> tuple[list[Candidate], dict]:
    eligible = [item for item in candidates if item.topic_gate]
    if len(eligible) < settings.discovery_min_videos:
        warnings.append(
            f"Topic Gate 后仅剩 {len(eligible)} 个候选，低于最小数量 "
            f"{settings.discovery_min_videos}"
        )
    if not eligible:
        return [], {"level": "drop_topic_mismatch", "before": len(candidates), "after": 0}
    return eligible, {
        "level": "drop_topic_mismatch",
        "before": len(candidates),
        "after": len(eligible),
    }


def _filter_by_age(
    candidates: list[Candidate],
    settings: Settings,
    warnings: list[str],
) -> tuple[list[Candidate], dict]:
    if settings.discovery_max_age_days <= 0:
        return candidates, {"level": "off"}
    import time

    cutoff = time.time() - settings.discovery_max_age_days * 86400
    recent = [
        item for item in candidates
        if item.published_at is None or item.published_at >= cutoff
    ]
    if len(recent) >= settings.discovery_min_videos:
        return recent, {
            "level": "max_age",
            "max_age_days": settings.discovery_max_age_days,
            "before": len(candidates),
        }
    warnings.append(
        f"发布时间 {settings.discovery_max_age_days} 天过滤后候选不足，已保留旧视频"
    )
    return candidates, {"level": "off", "max_age_days": settings.discovery_max_age_days}


def _filter_by_views(
    candidates: list[Candidate],
    facets: list[Facet],
    settings: Settings,
    warnings: list[str],
) -> tuple[list[Candidate], dict]:
    """播放量不再做硬淘汰，只作为选择阶段的软信号。

    低播放专家/垂直内容如果先被过滤，永远无法进入 coverage 选择；
    低播放数量由 selection 的低播放上限控制。
    """
    low_view = [item for item in candidates if _is_low_view(item, settings)]
    return candidates, {
        "level": "soft",
        "threshold": "soft_signal",
        "before": len(candidates),
        "low_view": len(low_view),
    }


def _target_count(candidates: list[Candidate], facets: list[Facet], settings: Settings) -> int:
    max_minutes = settings.discovery_max_video_minutes or 60
    durations = [
        min(item.duration_ms / 60000, max_minutes)
        for item in candidates
        if item.duration_ms
    ]
    avg_duration = sum(durations) / len(durations) if durations else 20.0
    budget_count = max(1, int(settings.discovery_duration_budget_min / max(avg_duration, 1)))
    target = math.ceil(1.5 * max(len(facets), 1))
    return max(
        settings.discovery_min_videos,
        min(target, settings.discovery_hard_max_videos, budget_count, settings.discovery_max_videos),
    )


def _estimated_minutes(item: Candidate) -> float:
    if item.duration_ms:
        return max(item.duration_ms / 60000, 0.1)
    return DEFAULT_ESTIMATED_MINUTES


def _is_low_view(item: Candidate, settings: Settings) -> bool:
    if item.views is None:
        return False
    threshold = (
        settings.bilibili_min_views
        if item.source == "bilibili"
        else settings.youtube_min_views
    )
    return item.views < threshold


def _select_score(
    item: Candidate,
    covered: set[str],
    facets: list[Facet],
    selected: list[Candidate],
    settings: Settings,
) -> float:
    new_facets = max(len(set(item.matched_facets) - covered), 0)
    coverage = new_facets / max(len(facets), 1)
    debate = 1.0 if any(
        facet.stance == "negative" and facet.id in item.matched_facets for facet in facets
    ) else 0.0
    same_cluster = 0.9 if any(other.cluster_id == item.cluster_id for other in selected) else 0.0
    redundancy = max(item.redundancy, same_cluster)
    duration_penalty = float(item.metrics.get("duration_penalty", 0.0))
    view_penalty = 0.03 if _is_low_view(item, settings) else 0.0
    tag_bonus = 0.05 * float(item.metrics.get("tag_score", 0.0))
    lam = settings.discovery_mmr_lambda
    base_score = 0.70 * item.relevance + 0.30 * item.final_score
    return (
        lam * base_score
        + (1 - lam) * coverage
        + 0.10 * debate
        + tag_bonus
        - 0.15 * redundancy
        - 0.05 * duration_penalty
        - view_penalty
    )


def _reason(item: Candidate, facets: list[Facet]) -> str:
    labels = [
        facet.label for facet in facets if facet.id in item.matched_facets
    ]
    coverage = f"覆盖：{'、'.join(labels)}" if labels else "相关性候选"
    return (
        f"{coverage}；播放 {item.views or 0}；"
        f"综合 {item.final_score:.2f} "
        f"(R {item.relevance:.2f} / A {item.authority:.2f} / F {item.freshness:.2f})；"
        f"类型 {item.metrics.get('content_type', 'other')}"
    )
