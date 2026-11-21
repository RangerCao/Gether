"""索引构建与混合检索服务。"""

from __future__ import annotations

from app.core.config import Settings
from app.index.bm25 import BM25Index, rrf_fuse
from app.index.chunking import Chunk, chunk_evidence
from app.index.embedding import EmbeddingProvider
from app.index.repository import ChunkRepository
from app.index.retrieval import RetrievalHit
from app.repository import TaskRepository


class IndexNotFound(RuntimeError):
    pass


class IndexService:
    def __init__(
        self,
        *,
        settings: Settings,
        task_repository: TaskRepository,
        chunk_repository: ChunkRepository,
        embedder: EmbeddingProvider,
    ) -> None:
        self.settings = settings
        self.task_repository = task_repository
        self.chunk_repository = chunk_repository
        self.embedder = embedder
        self._cache: dict[str, tuple[int, list[Chunk], BM25Index]] = {}

    async def index_task(self, task_id: str) -> dict:
        evidence = await self.task_repository.list_evidence(task_id, limit=100000)
        chunks = chunk_evidence(
            evidence,
            target_chars=self.settings.chunk_target_chars,
            max_chars=self.settings.chunk_max_chars,
            overlap_chars=self.settings.chunk_overlap_chars,
        )
        embeddings = await self.embedder.embed_documents([chunk.text for chunk in chunks])
        inserted = await self.chunk_repository.replace_chunks(task_id, chunks, embeddings)
        self._cache.pop(task_id, None)
        return {
            "task_id": task_id,
            "evidence_count": len(evidence),
            "chunk_count": len(chunks),
            "inserted": inserted,
            "embedding_model": self.settings.embedding_model,
            "embedding_dim": self.settings.embedding_dim,
        }

    async def search(
        self,
        task_id: str,
        query: str,
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
        rrf_k: int | None = None,
        vector_weight: float | None = None,
        bm25_weight: float | None = None,
        video_id: str | None = None,
        modalities: list[str] | None = None,
        time_start_ms: int | None = None,
        time_end_ms: int | None = None,
    ) -> list[RetrievalHit]:
        top_k = top_k or self.settings.retrieval_top_k
        candidate_k = candidate_k or self.settings.retrieval_candidates
        rrf_k = rrf_k or self.settings.rrf_k
        vector_weight = self.settings.vector_weight if vector_weight is None else vector_weight
        bm25_weight = self.settings.bm25_weight if bm25_weight is None else bm25_weight

        cached = self._cache.get(task_id)
        if cached is not None:
            _, chunks, bm25_all = cached
        else:
            chunks = await self.chunk_repository.list_chunks(task_id)
            if not chunks:
                raise IndexNotFound(f"任务 {task_id} 尚未建立索引")
            bm25_all = BM25Index([chunk.text for chunk in chunks])
            self._cache[task_id] = (len(chunks), chunks, bm25_all)

        filtered = _filter_chunks(
            chunks,
            video_id=video_id,
            modalities=modalities,
            time_start_ms=time_start_ms,
            time_end_ms=time_end_ms,
        )
        if not filtered:
            return []

        query_vector = await self.embedder.embed_query(query)
        vector_results = await self.chunk_repository.vector_search(
            task_id,
            query_vector,
            top_k=max(candidate_k, top_k),
            video_id=video_id,
            modalities=modalities,
            time_start_ms=time_start_ms,
            time_end_ms=time_end_ms,
        )
        bm25 = bm25_all if len(filtered) == len(chunks) else BM25Index(
            [chunk.text for chunk in filtered]
        )
        bm25_hits = bm25.search(query, top_k=max(candidate_k, top_k))
        by_id = {chunk.chunk_id: chunk for chunk in filtered}

        vector_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(vector_results, start=1)}
        vector_score = {chunk_id: score for chunk_id, score in vector_results}
        bm25_rank = {
            filtered[hit.index].chunk_id: rank for rank, hit in enumerate(bm25_hits, start=1)
        }
        bm25_score = {filtered[hit.index].chunk_id: hit.score for hit in bm25_hits}

        fused = rrf_fuse(
            [
                [chunk_id for chunk_id, _ in vector_results],
                [filtered[hit.index].chunk_id for hit in bm25_hits],
            ],
            k=rrf_k,
            weights=[vector_weight, bm25_weight],
        )
        hits: list[RetrievalHit] = []
        for chunk_id, score in fused[:top_k]:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                continue
            hits.append(
                RetrievalHit(
                    chunk=chunk,
                    score=score,
                    vector_rank=vector_rank.get(chunk_id),
                    bm25_rank=bm25_rank.get(chunk_id),
                    vector_score=vector_score.get(chunk_id),
                    bm25_score=bm25_score.get(chunk_id),
                )
            )
        return hits



def _filter_chunks(
    chunks: list[Chunk],
    *,
    video_id: str | None = None,
    modalities: list[str] | None = None,
    time_start_ms: int | None = None,
    time_end_ms: int | None = None,
) -> list[Chunk]:
    return [
        chunk
        for chunk in chunks
        if (not video_id or chunk.video_id == video_id)
        and (not modalities or any(item in chunk.modalities for item in modalities))
        and (time_start_ms is None or chunk.end_ms >= time_start_ms)
        and (time_end_ms is None or chunk.start_ms <= time_end_ms)
    ]
