"""广泛集 pointwise LTR/rerank 基线。

标签来自 Top-10 pooled seed，16 题 × 10 候选。
使用留一问题交叉验证，比较：
  auto-mnp / R-only / A-only / F-only / logistic-LTR。
指标：P@5、nDCG@5；只用于相对比较。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))

from app.index.bm25 import BM25Index  # noqa: E402

DATASET = ROOT / "eval/datasets/discovery_broad_questions.json"
POOL = ROOT / "eval/datasets/discovery_broad_pool.json"
SESSIONS = ROOT / "eval/results/discovery_broad_sessions"
OUT = ROOT / "eval/results/broad_ltr.md"


def features(candidate: dict, bm25_score: float = 0.0) -> list[float]:
    metrics = candidate.get("metrics") or {}
    types = set(metrics.get("content_types") or [])
    relevance = float(candidate.get("relevance") or 0)
    authority = float(candidate.get("authority") or 0)
    freshness = float(candidate.get("freshness") or 0)
    topic = float(candidate.get("topic_score") or 0)
    credibility = float(metrics.get("credibility") or 0)
    return [
        relevance,
        authority,
        freshness,
        topic,
        float(metrics.get("best_facet") or 0),
        credibility,
        float(metrics.get("duration_penalty") or 0),
        math.log1p(max(candidate.get("views") or 0, 0)),
        float(metrics.get("engagement_rate") or 0),
        bm25_score,
        relevance * freshness,
        relevance * topic,
        authority * freshness,
        relevance * credibility,
        1.0 if "research" in types else 0.0,
        1.0 if "official" in types else 0.0,
        1.0 if "news" in types else 0.0,
        1.0 if "tutorial" in types else 0.0,
        1.0 if "experience" in types else 0.0,
    ]


def sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -30, 30)))


def fit_logistic(x: np.ndarray, y: np.ndarray, *, steps: int = 600, lr: float = 0.15, l2: float = 0.01) -> np.ndarray:
    # x already has bias column
    w = np.zeros(x.shape[1], dtype=float)
    for _ in range(steps):
        pred = sigmoid(x @ w)
        grad = (x.T @ (pred - y)) / max(len(y), 1) + l2 * w
        w -= lr * grad
    return w


def metrics(scores: list[float], labels: list[int], k: int = 5) -> tuple[float, float]:
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    hits = sum(labels[i] for i in order)
    precision = hits / k
    dcg = sum(labels[i] / math.log2(rank + 2) for rank, i in enumerate(order))
    ideal = sum(label / math.log2(rank + 2) for rank, label in enumerate(sorted(labels, reverse=True)[:k]))
    return precision, dcg / ideal if ideal else 0.0


def main() -> int:
    dataset = {q["id"]: q for q in json.loads(DATASET.read_text(encoding="utf-8"))["questions"]}
    pool = json.loads(POOL.read_text(encoding="utf-8"))["pool"]
    groups = []
    for group in pool:
        qid = group["id"]
        session_path = SESSIONS / f"{qid}.json"
        if qid not in dataset or not session_path.is_file():
            continue
        session = json.loads(session_path.read_text(encoding="utf-8"))
        by_id = {c["source_video_id"]: c for c in session.get("candidates", [])}
        rows = []
        candidate_list = []
        for entry in group["candidates"]:
            candidate = by_id.get(entry["source_video_id"])
            if not candidate:
                continue
            candidate_list.append(candidate)
            rows.append((entry, candidate, int(entry.get("label") or 0)))
        if not rows:
            continue
        bm25 = BM25Index(
            [f"{candidate.get('title', '')} {candidate.get('description', '')}" for candidate in candidate_list]
        )
        hits = bm25.search(dataset[qid]["question"], top_k=len(candidate_list))
        by_index = {hit.index: hit.score for hit in hits}
        max_bm25 = max(by_index.values(), default=1.0) or 1.0
        rows = [
            (entry, candidate, int(entry.get("label") or 0), by_index.get(index, 0.0) / max_bm25)
            for index, (entry, candidate, label) in enumerate(rows)
        ]
        groups.append((qid, rows))

    names = ["auto-mnp", "R-only", "A-only", "F-only", "equal", "logistic-LTR"]
    results = {name: [] for name in names}
    for test_index, (qid, rows) in enumerate(groups):
        train_rows = [row for index, (_, group) in enumerate(groups) if index != test_index for row in group]
        x_train = np.array([features(candidate, bm25_score) for _, candidate, _, bm25_score in train_rows], dtype=float)
        y_train = np.array([label for _, _, label, _ in train_rows], dtype=float)
        mean = x_train.mean(axis=0, keepdims=True)
        std = x_train.std(axis=0, keepdims=True)
        std[std == 0] = 1.0
        x_train = (x_train - mean) / std
        x_train = np.c_[np.ones(len(x_train)), x_train]
        weights = fit_logistic(x_train, y_train)

        x_test = np.array([features(candidate, bm25_score) for _, candidate, _, bm25_score in rows], dtype=float)
        x_test = (x_test - mean) / std
        x_test = np.c_[np.ones(len(x_test)), x_test]
        ltr_scores = list(x_test @ weights)
        labels = [label for _, _, label, _ in rows]
        candidates = [candidate for _, candidate, _, _ in rows]
        scores = {
            "auto-mnp": [c.get("final_score", 0) for c in candidates],
            "R-only": [c.get("relevance", 0) for c in candidates],
            "A-only": [c.get("authority", 0) for c in candidates],
            "F-only": [c.get("freshness", 0) for c in candidates],
            "equal": [
                ((c.get("relevance", 0) + c.get("authority", 0) + c.get("freshness", 0)) / 3)
                for c in candidates
            ],
            "logistic-LTR": ltr_scores,
        }
        for name in names:
            results[name].append(metrics(scores[name], labels))

    lines = [
        "# 广泛集 LTR / rerank 基线",
        "",
        f"{len(groups)} 题 × Top-10 pooled labels；留一问题交叉验证。",
        "",
        "| ranker | P@5 | nDCG@5 |",
        "|---|---:|---:|",
    ]
    for name in names:
        values = results[name]
        lines.append(
            f"| {name} | {sum(v[0] for v in values) / len(values):.4f} | "
            f"{sum(v[1] for v in values) / len(values):.4f} |"
        )
    lines += [
        "",
        "注意：这是 pointwise logistic LTR 基线，不是最终 cross-encoder；",
        "标签为 Top-10 pooled seed，仍需扩大人工复核。",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
