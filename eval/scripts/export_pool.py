"""导出广泛集 Top-N 候选池，供人工 pooled labeling。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
OUT = ROOT / "eval/datasets/discovery_broad_pool.json"


def main() -> int:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    labels = {q["id"]: q.get("labels") or {} for q in dataset["questions"]}
    pool = []
    for question in dataset["questions"]:
        qid = question["id"]
        path = SESSIONS / f"{qid}.json"
        if not path.is_file():
            continue
        session = json.loads(path.read_text(encoding="utf-8"))
        rows = []
        for candidate in sorted(
            session.get("candidates", []),
            key=lambda item: item.get("final_score", 0),
            reverse=True,
        )[:10]:
            video_id = candidate["source_video_id"]
            rows.append(
                {
                    "source_video_id": video_id,
                    "title": candidate["title"],
                    "selected": bool(candidate.get("selected")),
                    "label": labels.get(qid, {}).get(video_id),
                    "final_score": candidate.get("final_score"),
                }
            )
        pool.append(
            {
                "id": qid,
                "question": question["question"],
                "category": question["category"],
                "candidates": rows,
            }
        )
    OUT.write_text(json.dumps({"version": 1, "pool": pool}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported {len(pool)} questions -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
