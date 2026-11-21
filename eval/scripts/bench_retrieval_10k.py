"""10k 分块检索延迟压测（真实 pgvector + BM25 + 真实 query embedding）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))

from app.contracts import EvidencePacket, VideoRef, VideoSourceName  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.index.chunking import Chunk  # noqa: E402
from app.index.embedding import FastEmbedProvider  # noqa: E402
from app.index.repository import ChunkRepository  # noqa: E402
from app.index.service import IndexService  # noqa: E402
from app.repository import TaskRepository  # noqa: E402


def _chunks(task_id: str, video_id: str, texts: list[str], size: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for index in range(size):
        text = (
            texts[index % len(texts)]
            + " "
            + texts[(index * 7) % len(texts)]
            + f" 编号{index}"
        )
        start = index * 5000
        chunks.append(
            Chunk(
                chunk_id=str(uuid4()),
                task_id=task_id,
                video_id=video_id,
                text=text,
                start_ms=start,
                end_ms=start + 4500,
                evidence_ids=(f"ev-{index}",),
                modalities=("asr",) if index % 2 == 0 else ("ocr",),
                content_hash=f"sha256:bench-{index}",
            )
        )
    return chunks


def _random_vectors(size: int, dim: int) -> list[list[float]]:
    rng = np.random.default_rng(42)
    vectors = rng.normal(size=(size, dim)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors.tolist()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10000)
    parser.add_argument("--queries", type=int, default=40)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--out", default=str(ROOT / "eval/results"))
    parser.add_argument("--database-url", default=os.getenv("GETHER_TEST_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        print("需要 GETHER_TEST_DATABASE_URL", file=sys.stderr)
        return 1

    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        database_url=args.database_url,
        embedding_provider="local",
        embedding_model="BAAI/bge-small-zh-v1.5",
        embedding_dim=512,
        retrieval_candidates=50,
        retrieval_top_k=args.top_k,
    )
    task_repository = TaskRepository(settings)
    chunk_repository = ChunkRepository(settings)
    embedder = FastEmbedProvider(settings.embedding_model, dim=512)
    index_service = IndexService(
        settings=settings,
        task_repository=task_repository,
        chunk_repository=chunk_repository,
        embedder=embedder,
    )

    evidence_path = ROOT / "data/output_v2/bilibili/BV1gehE6DEJ8/evidence.json"
    evidence = [
        EvidencePacket.model_validate(item)
        for item in json.loads(evidence_path.read_text(encoding="utf-8"))["evidence"]
    ]
    texts = [item.text for item in evidence if item.text]

    task_id = await task_repository.create_task({"bench": True})
    video_id = await task_repository.upsert_video(
        task_id,
        VideoRef(
            source=VideoSourceName.LOCAL,
            source_video_id="bench-video",
            page=1,
            url="file:///bench-video",
        ),
    )
    print(f"task={task_id} video={video_id} size={args.size}")
    started = time.perf_counter()
    chunks = _chunks(task_id, video_id, texts, args.size)
    vectors = _random_vectors(args.size, 512)
    inserted = await chunk_repository.replace_chunks(task_id, chunks, vectors)
    index_seconds = time.perf_counter() - started
    print(f"inserted={inserted} index_seconds={index_seconds:.1f}")

    queries_payload = json.loads(
        (ROOT / "eval/datasets/BV1gehE6DEJ8_queries.json").read_text(encoding="utf-8")
    )["queries"]
    query_texts = [item["query"] for item in queries_payload]
    while len(query_texts) < args.queries:
        query_texts.append(query_texts[len(query_texts) % len(queries_payload)])
    query_texts = query_texts[: args.queries]

    for query in query_texts[:3]:
        await index_service.search(task_id, query, top_k=args.top_k)

    timings: list[float] = []
    vector_hits = 0
    for query in query_texts:
        started = time.perf_counter()
        hits = await index_service.search(task_id, query, top_k=args.top_k)
        timings.append((time.perf_counter() - started) * 1000)
        vector_hits += sum(1 for hit in hits if hit.vector_rank is not None)

    values = np.array(timings)
    payload = {
        "size": args.size,
        "queries": len(timings),
        "top_k": args.top_k,
        "index_seconds": round(index_seconds, 1),
        "p50_ms": round(float(np.percentile(values, 50)), 2),
        "p95_ms": round(float(np.percentile(values, 95)), 2),
        "p99_ms": round(float(np.percentile(values, 99)), 2),
        "avg_ms": round(float(values.mean()), 2),
        "max_ms": round(float(values.max()), 2),
        "avg_hits": round(vector_hits / max(len(timings), 1), 2),
        "embedding": "BAAI/bge-small-zh-v1.5",
        "chunk_vectors": "random normalized (latency-only)",
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bench_10k.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 10k 分块检索延迟",
        "",
        f"- 分块数：{args.size}",
        f"- 查询数：{len(timings)}",
        f"- 建索引耗时：{index_seconds:.1f}s",
        f"- P50：{payload['p50_ms']} ms",
        f"- P95：{payload['p95_ms']} ms",
        f"- P99：{payload['p99_ms']} ms",
        f"- 平均：{payload['avg_ms']} ms",
        "",
        "> chunk 向量为随机归一化向量，仅用于测量 pgvector HNSW + BM25 + RRF 的检索延迟；query 使用真实 embedding。",
    ]
    (out_dir / "bench_10k.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    await embedder.aclose()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
