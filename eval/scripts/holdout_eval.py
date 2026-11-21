"""固定 holdout 对照：auto / R-only / LTR / LLM rerank。"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))
sys.path.insert(0, str(ROOT / "eval" / "scripts"))

from app.index.bm25 import BM25Index  # noqa: E402
from train_ltr import features, fit_logistic  # noqa: E402

DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
POOL = ROOT / "eval/datasets/discovery_broad_pool.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
HOLDOUT = ROOT / "eval/datasets/discovery_holdout.json"
LLM = ROOT / "eval/results/broad_llm_rerank.json"
OUT = ROOT / "eval/results/broad_holdout.md"


def rank_metrics(rows, scores, k=5):
    order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)[:k]
    labels = [row[2] for row in rows]
    hits = sum(labels[i] for i in order)
    dcg = sum(labels[i] / math.log2(rank + 2) for rank, i in enumerate(order))
    ideal = sum(label / math.log2(rank + 2) for rank, label in enumerate(sorted(labels, reverse=True)[:k]))
    return hits / k, dcg / ideal if ideal else 0.0


def main() -> int:
    holdout = set(json.loads(HOLDOUT.read_text(encoding="utf-8"))["ids"])
    dataset = {q["id"]: q for q in json.loads(DATASET.read_text(encoding="utf-8"))["questions"]}
    pool = json.loads(POOL.read_text(encoding="utf-8"))["pool"]
    llm_rows = {row["id"]: row for row in json.loads(LLM.read_text(encoding="utf-8"))}
    groups = []
    for group in pool:
        qid = group["id"]
        path = SESSIONS / f"{qid}.json"
        if qid not in dataset or not path.is_file():
            continue
        session = json.loads(path.read_text(encoding="utf-8"))
        by_id = {c["source_video_id"]: c for c in session.get("candidates", [])}
        candidates = []
        for entry in group["candidates"]:
            base = by_id.get(entry["source_video_id"])
            if base:
                candidates.append((entry, base, int(entry.get("label") or 0)))
        if not candidates:
            continue
        bm25 = BM25Index([f"{c.get('title', '')} {c.get('description', '')}" for _, c, _ in candidates])
        hits = bm25.search(dataset[qid]["question"], top_k=len(candidates))
        max_bm25 = max((h.score for h in hits), default=1.0) or 1.0
        by_index = {h.index: h.score / max_bm25 for h in hits}
        groups.append(
            (qid, [(entry, candidate, label, by_index.get(i, 0.0)) for i, (entry, candidate, label) in enumerate(candidates)])
        )

    train_groups = [group for group in groups if group[0] not in holdout]
    test_groups = [group for group in groups if group[0] in holdout]
    train_rows = [row for _, group in train_groups for row in group]
    x_train = np.array([features(candidate, bm25) for _, candidate, _, bm25 in train_rows])
    y_train = np.array([label for _, _, label, _ in train_rows], dtype=float)
    mean = x_train.mean(axis=0, keepdims=True)
    std = x_train.std(axis=0, keepdims=True)
    std[std == 0] = 1.0
    x_train = np.c_[np.ones(len(x_train)), (x_train - mean) / std]
    weights = fit_logistic(x_train, y_train)

    result = {name: [] for name in ["auto", "R-only", "LTR", "LLM"]}
    for qid, group in test_groups:
        x_test = np.array([features(candidate, bm25) for _, candidate, _, bm25 in group])
        x_test = np.c_[np.ones(len(x_test)), (x_test - mean) / std]
        ltr = list(x_test @ weights)
        auto = [candidate.get("final_score", 0) for _, candidate, _, _ in group]
        r_only = [candidate.get("relevance", 0) for _, candidate, _, _ in group]
        result["auto"].append(rank_metrics(group, auto))
        result["R-only"].append(rank_metrics(group, r_only))
        result["LTR"].append(rank_metrics(group, ltr))
        if qid in llm_rows:
            # LLM 只保存指标；此处用其记录值
            result["LLM"].append((llm_rows[qid]["llm_p"], llm_rows[qid]["llm_n"]))

    lines = [
        "# 固定 holdout 对照",
        "",
        f"holdout：{', '.join(sorted(holdout))}",
        "",
        "| ranker | P@5 | nDCG@5 |",
        "|---|---:|---:|",
    ]
    for name in ["auto", "R-only", "LTR", "LLM"]:
        values = result[name]
        lines.append(
            f"| {name} | {sum(v[0] for v in values) / len(values):.4f} | "
            f"{sum(v[1] for v in values) / len(values):.4f} |"
        )
    lines += ["", "注意：LLM 指标来自 `broad_llm_rerank.json` 的同一 holdout 运行。", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
