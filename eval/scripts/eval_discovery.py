"""Discovery 标注集评测：自动 priority + Selection Precision/Recall + 成本。

用法：
  python eval/scripts/eval_discovery.py
  python eval/scripts/eval_discovery.py --question ai_coding_efficiency
  python eval/scripts/eval_discovery.py --no-live   # 只读取已有 session 结果
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "eval/datasets/discovery_questions.json"
DEFAULT_BASE = "http://localhost:8081"
DEFAULT_USER = "11111111-1111-1111-1111-111111111111"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--user-id", default=DEFAULT_USER)
    parser.add_argument("--question", action="append", default=[])
    parser.add_argument("--session-dir", default=str(ROOT / "eval/results/discovery_sessions"))
    parser.add_argument("--no-live", action="store_true", help="只读取 results/discovery_sessions")
    parser.add_argument("--out", default=str(ROOT / "eval/results/discovery_eval.md"))
    args = parser.parse_args()

    payload = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    selected_questions = set(args.question)
    questions = [
        item for item in payload["questions"]
        if not selected_questions or item["id"] in selected_questions
    ]
    session_dir = Path(args.session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    token = None
    client = httpx.Client(timeout=300)
    if not args.no_live:
        auth = client.post(
            f"{args.base_url}/api/auth/token",
            json={"user_id": args.user_id},
        )
        auth.raise_for_status()
        token = auth.json()["token"]

    rows: list[dict[str, Any]] = []
    for item in questions:
        qid = item["id"]
        started = time.time()
        session_file = session_dir / f"{qid}.json"
        if args.no_live:
            if not session_file.is_file():
                rows.append({"id": qid, "error": "missing session file"})
                continue
            session = json.loads(session_file.read_text(encoding="utf-8"))
        else:
            response = client.post(
                f"{args.base_url}/api/discovery/sessions",
                headers={"Authorization": f"Bearer {token}"},
                json={"question": item["question"]},
            )
            if response.status_code != 200:
                rows.append(
                    {
                        "id": qid,
                        "error": f"HTTP {response.status_code}: {response.text[:200]}",
                    }
                )
                continue
            session = response.json()
            session_file.write_text(response.text, encoding="utf-8")

        row = evaluate(session, item, round(time.time() - started, 2))
        rows.append(row)

    client.close()
    write_report(rows, Path(args.out))
    try:
        print(Path(args.out).read_text(encoding="utf-8"))
    except UnicodeEncodeError:
        print(Path(args.out).read_text(encoding="utf-8").encode("utf-8", "replace").decode("utf-8"))
    return 0


def evaluate(session: dict, item: dict, elapsed: float) -> dict:
    selected = session.get("selected") or []
    candidates = session.get("candidates") or []
    labels = item.get("labels") or {}
    expected_priority = item.get("expected_priority")
    actual_priority = (session.get("ranking") or {}).get("priority")

    selected_ids = {entry["source_video_id"] for entry in selected}
    candidate_ids = {entry["source_video_id"] for entry in candidates}
    # 只评估当前候选池里出现过的标签，避免旧 session 的标签污染 recall。
    relevant = {key for key, value in labels.items() if value == 1 and key in candidate_ids}
    irrelevant = {key for key, value in labels.items() if value == 0 and key in candidate_ids}
    labeled_selected = selected_ids & (relevant | irrelevant)
    precision = (
        len(selected_ids & relevant) / len(labeled_selected)
        if labeled_selected
        else None
    )
    recall = len(selected_ids & relevant) / len(relevant) if relevant else None
    false_positive = len(selected_ids & irrelevant)
    duration = sum((entry.get("duration_ms") or 0) for entry in selected) / 60000
    coverage = (session.get("sufficiency") or {}).get("coverage")
    if coverage is None and session.get("facets"):
        covered = {
            facet_id
            for entry in selected
            for facet_id in (entry.get("matched_facets") or [])
        }
        coverage = len(covered) / len(session["facets"])
    warnings = (session.get("sufficiency") or {}).get("warnings") or []
    return {
        "id": item["id"],
        "question": item["question"],
        "expected_priority": expected_priority,
        "actual_priority": actual_priority,
        "priority_ok": (
            actual_priority == expected_priority
            if expected_priority and actual_priority
            else None
        ),
        "selected": len(selected),
        "candidates": len(candidates),
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "false_positive": false_positive,
        "coverage": round(coverage, 4) if coverage is not None else None,
        "duration_minutes": round(duration, 1),
        "elapsed_seconds": elapsed,
        "labels_status": "ready" if labels else "pending",
        "warnings": warnings,
        "selected_titles": [entry.get("title", "") for entry in selected],
    }


def write_report(rows: list[dict], path: Path) -> None:
    lines = [
        "# Discovery 标注集评测",
        "",
        "| id | priority | P | R | FP | coverage | selected | duration | labels |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        if row.get("error"):
            lines.append(f"| {row['id']} | ERROR | - | - | - | - | - | - | {row['error']} |")
            continue
        p = "-" if row["precision"] is None else f"{row['precision']:.3f}"
        r = "-" if row["recall"] is None else f"{row['recall']:.3f}"
        priority = f"{row['actual_priority']} / {row['expected_priority']}"
        if row["priority_ok"] is False:
            priority += " MISMATCH"
        lines.append(
            f"| {row['id']} | {priority} | {p} | {r} | {row['false_positive']} | "
            f"{row['coverage']} | {row['selected']} | {row['duration_minutes']}m | "
            f"{row['labels_status']} |"
        )
    valid = [row for row in rows if not row.get("error")]
    priority_rows = [row for row in valid if row.get("priority_ok") is not None]
    precision_rows = [row for row in valid if row.get("precision") is not None]
    recall_rows = [row for row in valid if row.get("recall") is not None]
    lines += [
        "",
        "## 汇总",
        "",
        f"- 问题数：{len(valid)} / {len(rows)}",
        f"- Priority accuracy：{sum(1 for row in priority_rows if row['priority_ok'])}/{len(priority_rows)}",
        f"- 平均 Precision：{sum(row['precision'] for row in precision_rows) / len(precision_rows):.4f}" if precision_rows else "- 平均 Precision：N/A",
        f"- 平均 Recall：{sum(row['recall'] for row in recall_rows) / len(recall_rows):.4f}" if recall_rows else "- 平均 Recall：N/A",
        f"- false positive 总数：{sum(row['false_positive'] for row in valid)}",
        f"- 平均总时长：{sum(row['duration_minutes'] for row in valid) / len(valid):.1f} 分钟",
        "",
    ]
    lines += ["## 明细", ""]
    for row in rows:
        if row.get("error"):
            continue
        lines.append(f"### {row['id']}")
        lines.append("")
        lines.append(f"- question: {row['question']}")
        lines.append(f"- selected: {row['selected']} / candidates {row['candidates']}")
        if row["selected_titles"]:
            lines += [f"  - {title}" for title in row["selected_titles"]]
        if row["warnings"]:
            lines.append(f"- warnings: {'; '.join(row['warnings'])}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
