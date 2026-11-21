"""本地 BGE cross-encoder rerank 基线（Top-10，不改变召回）。"""

from __future__ import annotations

import json
import math
from pathlib import Path

from fastembed.rerank.cross_encoder import TextCrossEncoder

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
POOL = ROOT / "eval/datasets/discovery_broad_pool.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
HOLDOUT = ROOT / "eval/datasets/discovery_holdout.json"
OUT = ROOT / "eval/results/broad_cross_encoder.md"


def metrics(rows, scores, k=5):
    order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)[:k]
    labels = [row[2] for row in rows]
    hits = sum(labels[i] for i in order)
    dcg = sum(labels[i] / math.log2(rank + 2) for rank, i in enumerate(order))
    ideal = sum(label / math.log2(rank + 2) for rank, label in enumerate(sorted(labels, reverse=True)[:k]))
    return hits / k, dcg / ideal if ideal else 0.0


def main() -> int:
    dataset = {q["id"]: q for q in json.loads(DATASET.read_text(encoding="utf-8"))["questions"]}
    pool = json.loads(POOL.read_text(encoding="utf-8"))["pool"]
    holdout = set(json.loads(HOLDOUT.read_text(encoding="utf-8"))["ids"])
    model = TextCrossEncoder(
        model_name="BAAI/bge-reranker-v2-m3-int8",
        cache_dir="D:/DevCache/fastembed_rerank",
    )
    all_rows = []
    holdout_rows = []
    for group in pool:
        qid = group["id"]
        path = SESSIONS / f"{qid}.json"
        if qid not in dataset or not path.is_file():
            continue
        session = json.loads(path.read_text(encoding="utf-8"))
        by_id = {c["source_video_id"]: c for c in session.get("candidates", [])}
        rows = []
        documents = []
        for entry in group["candidates"]:
            candidate = by_id.get(entry["source_video_id"])
            if not candidate:
                continue
            rows.append((entry, candidate, int(entry.get("label") or 0)))
            documents.append(f"{candidate.get('title', '')}\n{candidate.get('description', '')[:500]}")
        if not rows:
            continue
        scores = list(model.rerank(dataset[qid]["question"], documents))
        base = [row[1].get("final_score", 0) for row in rows]
        ce = metrics(rows, scores)
        bs = metrics(rows, base)
        all_rows.append((qid, bs, ce))
        if qid in holdout:
            holdout_rows.append((qid, bs, ce))

    def avg(rows, index):
        return sum(row[index][0] for row in rows) / len(rows), sum(row[index][1] for row in rows) / len(rows)

    lines = [
        "# 本地 cross-encoder rerank",
        "",
        "模型：`BAAI/bge-reranker-v2-m3-int8`；只重排 Top-10。",
        "",
        "| 数据 | base P@5 | CE P@5 | base nDCG@5 | CE nDCG@5 |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, rows in [("全部", all_rows), ("holdout", holdout_rows)]:
        bp, bn = avg(rows, 1)
        cp, cn = avg(rows, 2)
        lines.append(f"| {label} | {bp:.4f} | {cp:.4f} | {bn:.4f} | {cn:.4f} |")
    lines += ["", "| id | base P@5 | CE P@5 | base nDCG | CE nDCG |", "|---|---:|---:|---:|---:|"]
    for qid, base, ce in all_rows:
        lines.append(f"| {qid} | {base[0]:.3f} | {ce[0]:.3f} | {base[1]:.3f} | {ce[1]:.3f} |")
    lines += ["", "注意：标签为 pooled seed；本次只评估 Top-10 重排。", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
