"""混合检索调参：分块 → 向量 + BM25 → RRF，网格搜索 Recall/MRR/nDCG。"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))

from app.contracts import EvidencePacket  # noqa: E402
from app.index.bm25 import BM25Index, rrf_fuse  # noqa: E402
from app.index.chunking import Chunk, chunk_evidence  # noqa: E402
from app.index.embedding import FastEmbedProvider  # noqa: E402


@dataclass(frozen=True)
class ChunkConfig:
    name: str
    target: int
    max_chars: int
    overlap: int


ROUND1_CHUNKS = [
    ChunkConfig("small", 220, 320, 60),
    ChunkConfig("medium", 360, 500, 80),
    ChunkConfig("large", 500, 700, 100),
]
ROUND3_CHUNKS = [
    ChunkConfig("330", 330, 440, 60),
    ChunkConfig("360", 360, 480, 60),
    ChunkConfig("390", 390, 520, 70),
]
ROUND2_CHUNKS = [
    ChunkConfig("280", 280, 400, 60),
    ChunkConfig("360", 360, 480, 60),
    ChunkConfig("400", 400, 540, 80),
    ChunkConfig("460", 460, 620, 80),
]

ROUND1_WEIGHTS = [
    ("vector_only", 1.0, 0.0),
    ("bm25_only", 0.0, 1.0),
    ("1:1", 1.0, 1.0),
    ("1.5:1", 1.5, 1.0),
    ("1:0.5", 1.0, 0.5),
    ("0.5:1", 0.5, 1.0),
    ("1:1.5", 1.0, 1.5),
]
ROUND3_WEIGHTS = [
    ("0.9:1", 0.9, 1.0),
    ("1:1", 1.0, 1.0),
    ("1.1:1", 1.1, 1.0),
]
ROUND2_WEIGHTS = [
    ("1:1", 1.0, 1.0),
    ("1.2:1", 1.2, 1.0),
    ("0.8:1", 0.8, 1.0),
    ("1:0.8", 1.0, 0.8),
    ("1:1.2", 1.0, 1.2),
    ("1.5:1", 1.5, 1.0),
    ("1:1.5", 1.0, 1.5),
]


def load_evidence(path: Path) -> list[EvidencePacket]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [EvidencePacket.model_validate(item) for item in payload["evidence"]]


def load_queries(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["queries"]


def relevant_indices(chunks: list[Chunk], phrases: list[str]) -> set[int]:
    return {
        index
        for index, chunk in enumerate(chunks)
        if any(phrase in chunk.text for phrase in phrases)
    }


def metrics(ranked: list[int], relevant: set[int]) -> tuple[float, float, float, float]:
    top = ranked[:10]
    hits = [index for index in top if index in relevant]
    hit_rate = 1.0 if hits else 0.0
    recall = len(set(hits)) / len(relevant) if relevant else 0.0
    mrr = 1.0 / (top.index(hits[0]) + 1) if hits else 0.0
    dcg = sum(1.0 / math.log2(rank + 2) for rank, index in enumerate(top) if index in relevant)
    ideal = sum(1.0 / math.log2(rank + 2) for rank in range(min(len(relevant), 10)))
    ndcg = dcg / ideal if ideal else 0.0
    return hit_rate, recall, mrr, ndcg


async def evaluate_config(
    chunks: list[Chunk],
    embeddings: np.ndarray,
    bm25: BM25Index,
    queries: list[dict],
    query_vectors: list[np.ndarray],
    weights: list[tuple[str, float, float]],
    candidate_ks: tuple[int, ...],
    rrf_ks: tuple[int, ...],
) -> list[dict]:
    rows: list[dict] = []
    for candidate_k in candidate_ks:
        for rrf_k in rrf_ks:
            for name, vw, bw in weights:
                scores = []
                for item, query_vector in zip(queries, query_vectors, strict=True):
                    relevant = relevant_indices(chunks, item["relevant_phrases"])
                    vector_scores = embeddings @ query_vector
                    vector_order = np.argsort(-vector_scores)[:candidate_k].tolist()
                    bm25_hits = bm25.search(item["query"], top_k=candidate_k)
                    rankings = [
                        [chunks[index].chunk_id for index in vector_order],
                        [chunks[hit.index].chunk_id for hit in bm25_hits],
                    ]
                    fused = rrf_fuse(rankings, k=rrf_k, weights=[vw, bw])
                    by_id = {chunk.chunk_id: index for index, chunk in enumerate(chunks)}
                    ranked = [by_id[chunk_id] for chunk_id, _ in fused]
                    scores.append(metrics(ranked, relevant))
                avg = np.mean(np.array(scores), axis=0)
                rows.append(
                    {
                        "candidate_k": candidate_k,
                        "rrf_k": rrf_k,
                        "weights": name,
                        "vector_weight": vw,
                        "bm25_weight": bw,
                        "hit_rate": round(float(avg[0]), 4),
                        "recall@10": round(float(avg[1]), 4),
                        "mrr": round(float(avg[2]), 4),
                        "ndcg@10": round(float(avg[3]), 4),
                    }
                )
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence",
        default=str(ROOT / "data/output_v2/bilibili/BV1gehE6DEJ8/evidence.json"),
    )
    parser.add_argument(
        "--queries",
        default=str(ROOT / "eval/datasets/BV1gehE6DEJ8_queries.json"),
    )
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--round", type=int, default=1, choices=[1, 2, 3])
    parser.add_argument("--out", default=str(ROOT / "eval/results"))
    args = parser.parse_args()

    evidence = load_evidence(Path(args.evidence))
    queries = load_queries(Path(args.queries))
    chunk_configs = {1: ROUND1_CHUNKS, 2: ROUND2_CHUNKS, 3: ROUND3_CHUNKS}[args.round]
    weights = {1: ROUND1_WEIGHTS, 2: ROUND2_WEIGHTS, 3: ROUND3_WEIGHTS}[args.round]
    candidate_ks = {1: (20, 50, 100), 2: (15, 20, 30, 50), 3: (20, 50)}[args.round]
    rrf_ks = {1: (20, 40, 60, 80), 2: (10, 20, 30, 40, 60), 3: (20, 30, 40, 50, 60)}[args.round]
    embedder = FastEmbedProvider(args.model, dim=512)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    for config in chunk_configs:
        chunks = chunk_evidence(
            evidence,
            target_chars=config.target,
            max_chars=config.max_chars,
            overlap_chars=config.overlap,
        )
        texts = [chunk.text for chunk in chunks]
        embeddings = np.array(await embedder.embed_documents(texts), dtype=np.float32)
        query_vectors = [np.array(await embedder.embed_query(item["query"]), dtype=np.float32) for item in queries]
        bm25 = BM25Index(texts)
        rows = await evaluate_config(
            chunks,
            embeddings,
            bm25,
            queries,
            query_vectors,
            weights,
            candidate_ks,
            rrf_ks,
        )
        for row in rows:
            row["chunk_config"] = config.name
            row["chunk_target"] = config.target
            row["chunk_max"] = config.max_chars
            row["chunk_overlap"] = config.overlap
            row["chunk_count"] = len(chunks)
        all_rows.extend(rows)
        best = max(rows, key=lambda row: (row["recall@10"], row["ndcg@10"], row["mrr"]))
        print(
            f"[{config.name}] chunks={len(chunks)} "
            f"best recall@10={best['recall@10']} ndcg={best['ndcg@10']} mrr={best['mrr']} "
            f"cfg={best['weights']} cand={best['candidate_k']} rrf={best['rrf_k']}"
        )

    all_rows.sort(
        key=lambda row: (row["recall@10"], row["ndcg@10"], row["mrr"], row["hit_rate"]),
        reverse=True,
    )
    with (out_dir / f"retrieval_grid_round{args.round}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    lines = [
        "# 混合检索网格搜索结果",
        "",
        f"- 证据：`{args.evidence}`",
        f"- 查询数：{len(queries)}",
        f"- 模型：`{args.model}`",
        "",
        "| rank | chunk | cand | rrf | weights | hit | recall@10 | mrr | ndcg@10 | chunks |",
        "|---:|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(all_rows[:30], start=1):
        lines.append(
            f"| {rank} | {row['chunk_config']} | {row['candidate_k']} | {row['rrf_k']} | "
            f"{row['weights']} | {row['hit_rate']} | {row['recall@10']} | {row['mrr']} | "
            f"{row['ndcg@10']} | {row['chunk_count']} |"
        )
    (out_dir / f"retrieval_grid_round{args.round}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / f"best_retrieval_config_round{args.round}.json").write_text(
        json.dumps(all_rows[0], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("")
    print("top 5:")
    for row in all_rows[:5]:
        print(json.dumps(row, ensure_ascii=False))
    print(f"output: {out_dir}")
    await embedder.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
