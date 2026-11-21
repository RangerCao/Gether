"""混合检索排名：向量 + BM25 + RRF。"""

from __future__ import annotations

from dataclasses import dataclass

from app.index.bm25 import BM25Index, rrf_fuse
from app.index.chunking import Chunk


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Chunk
    score: float
    vector_rank: int | None = None
    bm25_rank: int | None = None
    vector_score: float | None = None
    bm25_score: float | None = None

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk.chunk_id,
            "video_id": self.chunk.video_id,
            "text": self.chunk.text,
            "start_ms": self.chunk.start_ms,
            "end_ms": self.chunk.end_ms,
            "evidence_ids": list(self.chunk.evidence_ids),
            "modalities": list(self.chunk.modalities),
            "score": self.score,
            "vector_rank": self.vector_rank,
            "bm25_rank": self.bm25_rank,
            "vector_score": self.vector_score,
            "bm25_score": self.bm25_score,
        }


def rank_hybrid(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    *,
    query: str,
    query_vector: list[float],
    candidate_k: int = 50,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
    top_k: int = 10,
) -> list[RetrievalHit]:
    if not chunks:
        return []
    candidate_k = max(candidate_k, top_k)

    vector_scores = [
        sum(left * right for left, right in zip(query_vector, vector, strict=False))
        for vector in embeddings
    ]
    vector_order = sorted(range(len(chunks)), key=lambda index: vector_scores[index], reverse=True)
    vector_order = vector_order[:candidate_k]
    vector_rank = {index: rank for rank, index in enumerate(vector_order, start=1)}

    bm25 = BM25Index([chunk.text for chunk in chunks])
    bm25_hits = bm25.search(query, top_k=candidate_k)
    bm25_rank = {hit.index: rank for rank, hit in enumerate(bm25_hits, start=1)}
    bm25_score = {hit.index: hit.score for hit in bm25_hits}

    rankings = [
        [chunks[index].chunk_id for index in vector_order],
        [chunks[hit.index].chunk_id for hit in bm25_hits],
    ]
    fused = rrf_fuse(
        rankings,
        k=rrf_k,
        weights=[vector_weight, bm25_weight],
    )
    by_id = {chunk.chunk_id: index for index, chunk in enumerate(chunks)}
    hits: list[RetrievalHit] = []
    for chunk_id, score in fused[:top_k]:
        index = by_id[chunk_id]
        hits.append(
            RetrievalHit(
                chunk=chunks[index],
                score=score,
                vector_rank=vector_rank.get(index),
                bm25_rank=bm25_rank.get(index),
                vector_score=vector_scores[index],
                bm25_score=bm25_score.get(index),
            )
        )
    return hits
