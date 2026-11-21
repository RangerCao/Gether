"""为每题抽取 rank 11–30 的分层候选，用于真正候选池 Recall。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
OUT = ROOT / "eval/datasets/discovery_outside_sample.json"


def main() -> int:
    dataset = {q["id"]: q for q in json.loads(DATASET.read_text(encoding="utf-8"))["questions"]}
    rows = []
    for path in sorted(SESSIONS.glob("*.json")):
        qid = path.stem
        if qid not in dataset:
            continue
        session = json.loads(path.read_text(encoding="utf-8"))
        candidates = sorted(
            session.get("candidates", []),
            key=lambda c: c.get("final_score", 0),
            reverse=True,
        )
        window = candidates[10:30]
        if not window:
            continue
        # 固定取窗口的第 1、6 个，保证可复现
        picks = [window[0], window[5]] if len(window) > 5 else window[:2]
        rows.append(
            {
                "id": qid,
                "question": dataset[qid]["question"],
                "category": dataset[qid]["category"],
                "candidates": [
                    {
                        "source_video_id": c["source_video_id"],
                        "title": c["title"],
                        "rank": candidates.index(c) + 1,
                        "selected": bool(c.get("selected")),
                        "label": None,
                    }
                    for c in picks
                ],
            }
        )
    OUT.write_text(json.dumps({"version": 1, "questions": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported {len(rows)} questions, {sum(len(r['candidates']) for r in rows)} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
