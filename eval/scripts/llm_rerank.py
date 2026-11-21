"""LLM rerank 基线：只重排 Top-10，不改变召回。

用法：python eval/scripts/llm_rerank.py --limit 24
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))

from app.core.config import get_settings  # noqa: E402
from app.llm.base import LLMRole, Message  # noqa: E402
from app.llm.factory import build_llm_client  # noqa: E402

DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
POOL = ROOT / "eval/datasets/discovery_broad_pool.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
OUT = ROOT / "eval/results/broad_llm_rerank.md"

SYSTEM = """你是视频搜索重排器。根据用户问题，对候选视频排序。
排序目标：是否直接帮助回答问题、是否提供实测/证据、是否可靠、是否覆盖问题角度。
不要按播放量或标题党程度排序。
只输出 JSON：{"order":["candidate_id", ...]}
必须包含所有候选 id，每个 id 只出现一次。"""


async def rerank(client, question: str, candidates: list[dict]) -> list[dict]:
    lines = [
        f'{item["source_video_id"]}|{item["title"]}|{(item.get("description") or "")[:120]}'
        for item in candidates
    ]
    raw = await client.complete(
        LLMRole.PLANNER,
        [
            Message("system", SYSTEM),
            Message("user", f"问题：{question}\n候选：\n" + "\n".join(lines)),
        ],
        json_mode=True,
        max_tokens=500,
    )
    try:
        data = json.loads(raw)
        order = [str(item) for item in data.get("order", [])]
    except Exception:
        order = []
    by_id = {item["source_video_id"]: item for item in candidates}
    ordered = [by_id[item] for item in order if item in by_id]
    ordered += [item for item in candidates if item not in ordered]
    return ordered


def ndcg(order: list[dict], labels: dict[str, int], k: int = 5) -> tuple[float, float]:
    ranked = order[:k]
    hits = sum(labels.get(item["source_video_id"], 0) for item in ranked)
    dcg = sum(
        labels.get(item["source_video_id"], 0) / math.log2(rank + 2)
        for rank, item in enumerate(ranked)
    )
    ideal_labels = sorted(labels.values(), reverse=True)[:k]
    ideal = sum(label / math.log2(rank + 2) for rank, label in enumerate(ideal_labels))
    return hits / k, dcg / ideal if ideal else 0.0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--json-out", default=str(ROOT / "eval/results/broad_llm_rerank.json"))
    args = parser.parse_args()
    dataset = {q["id"]: q for q in json.loads(DATASET.read_text(encoding="utf-8"))["questions"]}
    pool = json.loads(POOL.read_text(encoding="utf-8"))["pool"]
    if args.id:
        selected = set(args.id)
        pool = [item for item in pool if item["id"] in selected]
    else:
        pool = pool[: args.limit]
    settings = get_settings()
    client = build_llm_client(settings)
    rows = []
    try:
        for group in pool:
            qid = group["id"]
            session_path = SESSIONS / f"{qid}.json"
            if not session_path.is_file():
                continue
            session = json.loads(session_path.read_text(encoding="utf-8"))
            by_id = {c["source_video_id"]: c for c in session.get("candidates", [])}
            candidates = []
            for entry in group["candidates"]:
                base = by_id.get(entry["source_video_id"])
                if base:
                    candidates.append({**base, "label": entry.get("label", 0)})
            if not candidates:
                continue
            ordered = await rerank(client, dataset[qid]["question"], candidates)
            labels = {c["source_video_id"]: int(c.get("label") or 0) for c in candidates}
            base_order = sorted(candidates, key=lambda c: c.get("final_score", 0), reverse=True)
            base_p, base_n = ndcg(base_order, labels)
            llm_p, llm_n = ndcg(ordered, labels)
            rows.append(
                {
                    "id": qid,
                    "category": group["category"],
                    "base_p": base_p,
                    "base_n": base_n,
                    "llm_p": llm_p,
                    "llm_n": llm_n,
                }
            )
            print(qid, "base", round(base_p, 3), "llm", round(llm_p, 3))
    finally:
        await client.aclose()

    lines = [
        "# LLM rerank 基线",
        "",
        "只重排 Top-10，不改变召回；标签为 pooled seed。",
        "",
        "| id | category | base P@5 | LLM P@5 | base nDCG@5 | LLM nDCG@5 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['category']} | {row['base_p']:.3f} | {row['llm_p']:.3f} | "
            f"{row['base_n']:.3f} | {row['llm_n']:.3f} |"
        )
    Path(args.json_out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        lines += [
            "",
            f"**平均 P@5**：base {sum(x['base_p'] for x in rows) / len(rows):.4f} / "
            f"LLM {sum(x['llm_p'] for x in rows) / len(rows):.4f}",
            f"**平均 nDCG@5**：base {sum(x['base_n'] for x in rows) / len(rows):.4f} / "
            f"LLM {sum(x['llm_n'] for x in rows) / len(rows):.4f}",
            "",
        ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
