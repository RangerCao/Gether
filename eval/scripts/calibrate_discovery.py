"""facet 匹配阈值标定：基于已保存的 discovery session + 人工标签。"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))

from app.core.config import Settings  # noqa: E402
from app.discovery.content import classify_content_types, classify_intent  # noqa: E402
from app.discovery.models import Candidate, Facet  # noqa: E402
from app.discovery.selection import select_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", help="discovery session JSON")
    parser.add_argument("labels", help="source_video_id -> 0/1 的 JSON")
    parser.add_argument("--out", default=str(ROOT / "eval/results/discovery_calibration.md"))
    args = parser.parse_args()

    session = json.loads(Path(args.session).read_text(encoding="utf-8"))
    label_payload = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    labels = label_payload["labels"]
    negative_terms = label_payload.get("negative_terms", [])
    intent = classify_intent(label_payload.get("question", ""))
    facets = [Facet(**item) for item in session["facets"]]
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        discovery_min_videos=2,
        discovery_max_videos=4,
        discovery_hard_max_videos=6,
    )

    rows = []
    for threshold in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75):
        candidates = []
        for item in session["candidates"]:
            facet_scores = item.get("metrics", {}).get("facet_scores", {})
            metrics = dict(item.get("metrics", {}))
            content_types = metrics.get("content_types") or classify_content_types(
                item["title"], metrics.get("description", "")
            )
            content_type = content_types[0]
            noise_hits = [
                term for term in negative_terms if term.lower() in item["title"].lower()
            ]
            metrics["content_type"] = content_type
            metrics["content_types"] = content_types
            evidence_types = {"experience", "research", "opinion", "news"} & set(content_types)
            metrics["content_penalty"] = (
                (0.35 if evidence_types else 1.0)
                if intent == "evaluation"
                and ("tutorial" in content_types or "career" in content_types)
                else 0.0
            )
            metrics["noise_penalty"] = min(0.25, 0.08 * len(noise_hits))
            candidate = Candidate(
                source=item["source"],
                source_video_id=item["source_video_id"],
                url=item["url"],
                title=item["title"],
                duration_ms=item.get("duration_ms"),
                uploader=item.get("uploader"),
                published_at=item.get("published_at"),
                views=item.get("views"),
                metrics=metrics,
                relevance=item.get("relevance", 0),
                authority=item.get("authority", 0),
                freshness=item.get("freshness", 0),
                redundancy=item.get("redundancy", 0),
                final_score=item.get("final_score", 0),
                cluster_id=item.get("cluster_id"),
                matched_facets=[
                    facet_id
                    for facet_id, score in facet_scores.items()
                    if score >= threshold
                ],
            )
            candidates.append(candidate)
        selected, info = select_candidates(
            copy.deepcopy(candidates), facets, settings, intent=intent
        )
        relevant = {
            candidate.source_video_id
            for candidate in candidates
            if labels.get(candidate.source_video_id, 0) == 1
        }
        selected_ids = {candidate.source_video_id for candidate in selected}
        precision = (
            len(selected_ids & relevant) / len(selected_ids) if selected_ids else 0.0
        )
        recall = len(selected_ids & relevant) / len(relevant) if relevant else 0.0
        rows.append(
            {
                "threshold": threshold,
                "selected": len(selected),
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "coverage": info.get("coverage", 0),
                "filter": info.get("filter", {}).get("level"),
                "titles": [candidate.title for candidate in selected],
            }
        )

    rows.sort(key=lambda row: (row["precision"], row["recall"]), reverse=True)
    lines = [
        "# Discovery facet 阈值标定",
        "",
        f"- session：`{args.session}`",
        f"- 标签：`{args.labels}`",
        "",
        "| threshold | selected | precision | recall | coverage | filter |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['threshold']} | {row['selected']} | {row['precision']} | "
            f"{row['recall']} | {row['coverage']} | {row['filter']} |"
        )
    lines += ["", "## 最佳配置选中视频", ""]
    for title in rows[0]["titles"]:
        lines.append(f"- {title}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
