"""m/n/p 重估：固定 holdout，网格搜索 + P@5/nDCG@5。"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
POOL = ROOT / "eval/datasets/discovery_broad_pool.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
HOLDOUT = ROOT / "eval/datasets/discovery_holdout.json"
OUT = ROOT / "eval/results/broad_mnp_tuning.md"


def metrics(rows, scores, k=5):
    order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)[:k]
    labels = [row[2] for row in rows]
    hits = sum(labels[i] for i in order)
    dcg = sum(labels[i] / math.log2(rank + 2) for rank, i in enumerate(order))
    ideal = sum(label / math.log2(rank + 2) for rank, label in enumerate(sorted(labels, reverse=True)[:k]))
    return hits / k, dcg / ideal if ideal else 0.0


def average(groups, weights):
    m, n, p = weights
    values = []
    for _, rows in groups:
        scores = [m * r[1].get("relevance", 0) + n * r[1].get("authority", 0) + p * r[1].get("freshness", 0) for r in rows]
        values.append(metrics(rows, scores))
    return sum(v[0] for v in values) / len(values), sum(v[1] for v in values) / len(values)


def main() -> int:
    dataset = {q["id"]: q for q in json.loads(DATASET.read_text(encoding="utf-8"))["questions"]}
    pool = json.loads(POOL.read_text(encoding="utf-8"))["pool"]
    holdout = set(json.loads(HOLDOUT.read_text(encoding="utf-8"))["ids"])
    groups = []
    for group in pool:
        qid = group["id"]
        path = SESSIONS / f"{qid}.json"
        if qid not in dataset or not path.is_file():
            continue
        session = json.loads(path.read_text(encoding="utf-8"))
        by_id = {c["source_video_id"]: c for c in session.get("candidates", [])}
        rows = []
        for entry in group["candidates"]:
            candidate = by_id.get(entry["source_video_id"])
            if candidate:
                rows.append((entry, candidate, int(entry.get("label") or 0)))
        if rows:
            groups.append((qid, rows))

    train = [g for g in groups if g[0] not in holdout]
    test = [g for g in groups if g[0] in holdout]
    grid = []
    for m in range(1, 10):
        for n in range(1, 10):
            p = 10 - m - n
            if 1 <= p <= 8:
                weights = (m / 10, n / 10, p / 10)
                train_p, train_n = average(train, weights)
                test_p, test_n = average(test, weights)
                grid.append((weights, train_p, train_n, test_p, test_n))
    grid.sort(key=lambda row: (row[2], row[1]), reverse=True)
    best = grid[0]
    current = average(test, (0.60, 0.25, 0.15))
    r_only = average(test, (1.0, 0.0, 0.0))

    lines = [
        "# m/n/p 重估（固定 holdout）",
        "",
        f"holdout：{', '.join(sorted(holdout))}",
        "",
        "| 方案 | m | n | p | train P@5 | train nDCG@5 | holdout P@5 | holdout nDCG@5 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| 当前 relevance 权重 | 0.60 | 0.25 | 0.15 | - | - | {current[0]:.4f} | {current[1]:.4f} |",
        f"| R-only | 1.00 | 0.00 | 0.00 | - | - | {r_only[0]:.4f} | {r_only[1]:.4f} |",
        f"| 网格最佳（训练集） | {best[0][0]:.2f} | {best[0][1]:.2f} | {best[0][2]:.2f} | {best[1]:.4f} | {best[2]:.4f} | {best[3]:.4f} | {best[4]:.4f} |",
        "",
        "## 训练集前 8 组",
        "",
        "| m | n | p | train P@5 | train nDCG@5 | holdout P@5 | holdout nDCG@5 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for weights, tp, tn, vp, vn in grid[:8]:
        lines.append(f"| {weights[0]:.2f} | {weights[1]:.2f} | {weights[2]:.2f} | {tp:.4f} | {tn:.4f} | {vp:.4f} | {vn:.4f} |")
    lines += [
        "",
        "注意：这是基于 30 题 pooled seed 的重估，不直接修改生产权重；",
        "若 holdout 不支持，应保持当前权重。",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
