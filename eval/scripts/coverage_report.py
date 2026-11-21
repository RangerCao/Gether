"""广泛集覆盖报告：facet / stance / source / Topic Gate。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
OUT = ROOT / "eval/results/broad_coverage.md"


def main() -> int:
    dataset = {q["id"]: q for q in json.loads(DATASET.read_text(encoding="utf-8"))["questions"]}
    rows = []
    for path in sorted(SESSIONS.glob("*.json")):
        qid = path.stem
        if qid not in dataset:
            continue
        session = json.loads(path.read_text(encoding="utf-8"))
        selected = session.get("selected") or []
        facets = session.get("facets") or []
        covered = {f for item in selected for f in (item.get("matched_facets") or [])}
        stances = {f.get("stance", "neutral") for f in facets if f.get("id") in covered}
        sources = {item.get("source") for item in selected}
        candidates = session.get("candidates") or []
        rows.append(
            {
                "id": qid,
                "category": dataset[qid]["category"],
                "facets": len(facets),
                "coverage": len(covered) / len(facets) if facets else 0.0,
                "stances": len(stances),
                "sources": len(sources),
                "gate_pass": sum(1 for c in candidates if c.get("topic_gate")),
                "candidates": len(candidates),
                "selected": len(selected),
            }
        )
    lines = [
        "# 广泛集覆盖报告",
        "",
        "| id | category | facets | coverage | stances | sources | gate pass | candidates | selected |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['category']} | {row['facets']} | {row['coverage']:.2f} | "
            f"{row['stances']} | {row['sources']} | {row['gate_pass']}/{row['candidates']} | "
            f"{row['candidates']} | {row['selected']} |"
        )
    lines += [
        "",
        f"- 问题数：{len(rows)}",
        f"- 平均 facet coverage：{sum(r['coverage'] for r in rows) / len(rows):.4f}" if rows else "- 无 session",
        f"- 平均 Topic Gate 通过率：{sum(r['gate_pass'] / r['candidates'] for r in rows if r['candidates']) / len(rows):.4f}" if rows else "",
        "",
        "注意：coverage 只统计选中视频的 facet 覆盖，不等同于完整候选池 Recall。",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
