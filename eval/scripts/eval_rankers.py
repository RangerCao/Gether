"""通用排序基线对照：m/n/p 线性分 vs 单特征 vs 特征 rerank。

注意：当前广泛集只标注了选中视频，因此 Recall 是“选中标签内的召回”，
不是完整候选池 Recall；该脚本用于相对比较，不用于宣称绝对质量。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))

from app.core.config import Settings  # noqa: E402
from app.discovery.models import Candidate, Facet  # noqa: E402
from app.discovery.priority import weights_for  # noqa: E402
from app.discovery.selection import select_candidates  # noqa: E402

DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
OUT = ROOT / "eval/results/broad_ranker_comparison.md"

RANKERS = {
    "auto-mnp": None,
    "R-only": lambda c: c.relevance,
    "A-only": lambda c: c.authority,
    "F-only": lambda c: c.freshness,
    "equal": lambda c: (c.relevance + c.authority + c.freshness) / 3,
    "feature-v1": lambda c: (
        0.45 * c.relevance
        + 0.25 * c.authority
        + 0.15 * c.freshness
        + 0.10 * c.topic_score
        + 0.05 * c.metrics.get("best_facet", 0.0)
    ),
}


def main() -> int:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    labels = {q["id"]: q.get("labels") or {} for q in dataset["questions"]}
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        discovery_min_videos=2,
        discovery_max_videos=8,
        discovery_hard_max_videos=8,
        discovery_duration_budget_min=120,
    )
    sessions = []
    for path in sorted(SESSIONS.glob("*.json")):
        qid = path.stem
        if not labels.get(qid):
            continue
        sessions.append((qid, json.loads(path.read_text(encoding="utf-8"))))

    results = {}
    for name in RANKERS:
        ps, rs, fps, durations = [], [], 0, []
        for qid, session in sessions:
            facets = [Facet(**item) for item in session.get("facets", [])]
            priority = (session.get("ranking") or {}).get("priority", "balanced")
            weights = weights_for(priority)
            candidates = []
            for item in session.get("candidates", []):
                m = item.get("metrics") or {}
                candidate = Candidate(
                    source=item["source"],
                    source_video_id=item["source_video_id"],
                    url=item["url"],
                    title=item["title"],
                    duration_ms=item.get("duration_ms"),
                    views=item.get("views"),
                    published_at=item.get("published_at"),
                    uploader=item.get("uploader"),
                    description=item.get("description", ""),
                    metrics=dict(m),
                    matched_facets=list(item.get("matched_facets") or []),
                    relevance=item.get("relevance", 0),
                    authority=item.get("authority", 0),
                    freshness=item.get("freshness", 0),
                    topic_score=item.get("topic_score", 0),
                    topic_gate=item.get("topic_gate", True),
                    facet_confidence=item.get("facet_confidence") or {},
                    cluster_id=item.get("cluster_id"),
                )
                formula = RANKERS[name]
                if formula is None:
                    candidate.final_score = (
                        weights["m"] * candidate.relevance
                        + weights["n"] * candidate.authority
                        + weights["p"] * candidate.freshness
                    )
                else:
                    candidate.final_score = formula(candidate)
                candidates.append(candidate)
            selected, _ = select_candidates(
                candidates,
                facets,
                settings,
                intent=(session.get("constraints") or {}).get("intent", "general"),
            )
            ids = {c.source_video_id for c in selected}
            label = labels[qid]
            pool_ids = {c.source_video_id for c in candidates}
            relevant = {k for k, v in label.items() if v == 1 and k in pool_ids}
            irrelevant = {k for k, v in label.items() if v == 0 and k in pool_ids}
            labeled_selected = ids & (relevant | irrelevant)
            ps.append(len(ids & relevant) / len(labeled_selected) if labeled_selected else None)
            rs.append(len(ids & relevant) / len(relevant) if relevant else None)
            fps += len(ids & irrelevant)
            durations.append(sum((c.duration_ms or 0) for c in selected) / 60000)
        valid_p = [x for x in ps if x is not None]
        valid_r = [x for x in rs if x is not None]
        results[name] = {
            "precision": sum(valid_p) / len(valid_p) if valid_p else 0.0,
            "recall": sum(valid_r) / len(valid_r) if valid_r else 0.0,
            "false_positive": fps,
            "duration": sum(durations) / len(durations) if durations else 0.0,
            "questions": len(sessions),
        }

    lines = [
        "# 广泛集排序基线对照",
        "",
        "| ranker | questions | P | R | FP | avg duration |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, row in sorted(results.items(), key=lambda item: item[1]["precision"], reverse=True):
        lines.append(
            f"| {name} | {row['questions']} | {row['precision']:.4f} | "
            f"{row['recall']:.4f} | {row['false_positive']} | {row['duration']:.1f}m |"
        )
    lines += [
        "",
        "说明：labels 目前只覆盖选中视频；Recall 不等于完整候选池 Recall。",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
