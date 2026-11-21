"""广泛问题集的通用排序基线运行器。

只记录候选规模、Topic Gate 通过数、选中数、coverage、时长和 warnings；
不依赖人工标签，用于后续 pooled labeling / 消融实验的稳定输入。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
BASE = "http://localhost:8081"
USER = "11111111-1111-1111-1111-111111111111"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--category", default=None)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "eval/results/discovery_broad_baseline.md"))
    args = parser.parse_args()

    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    questions = payload["questions"]
    if args.category:
        questions = [q for q in questions if q.get("category") == args.category]
    if args.id:
        selected_ids = set(args.id)
        questions = [q for q in questions if q["id"] in selected_ids]
    elif not args.all:
        questions = questions[: max(args.limit, 1)]

    session_dir = ROOT / "eval/results/discovery_broad_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=300) as client:
        token = client.post(
            f"{BASE}/api/auth/token", json={"user_id": USER}
        ).json()["token"]
        rows = []
        for item in questions:
            started = time.time()
            try:
                response = client.post(
                    f"{BASE}/api/discovery/sessions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"question": item["question"]},
                )
                response.raise_for_status()
                session = response.json()
            except Exception as exc:
                rows.append(
                    {
                        "id": item["id"],
                        "category": item["category"],
                        "question": item["question"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            (session_dir / f"{item['id']}.json").write_text(
                json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            candidates = session.get("candidates") or []
            selected = session.get("selected") or []
            sufficiency = session.get("sufficiency") or {}
            rows.append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "question": item["question"],
                    "priority": (session.get("ranking") or {}).get("priority"),
                    "candidates": len(candidates),
                    "topic_pass": sum(1 for c in candidates if c.get("topic_gate")),
                    "selected": len(selected),
                    "coverage": sufficiency.get("coverage"),
                    "duration_minutes": round(
                        sum((c.get("duration_ms") or 0) for c in selected) / 60000, 1
                    ),
                    "warnings": len(sufficiency.get("warnings") or []),
                    "elapsed_seconds": round(time.time() - started, 1),
                }
            )

    lines = [
        "# Discovery 广泛集基线",
        "",
        "| id | category | priority | candidates | topic pass | selected | coverage | duration | warnings |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row.get("error"):
            lines.append(f"| {row['id']} | {row['category']} | ERROR | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| {row['id']} | {row['category']} | {row['priority']} | {row['candidates']} | "
            f"{row['topic_pass']} | {row['selected']} | {row['coverage']} | "
            f"{row['duration_minutes']}m | {row['warnings']} |"
        )
    lines += ["", "## Questions", ""]
    for row in rows:
        lines.append(f"- **{row['id']}** {row['question']}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
