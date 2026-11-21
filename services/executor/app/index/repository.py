"""检索分块的持久化与向量查询。"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from app.core.config import Settings
from app.core.db import Database
from app.index.chunking import Chunk


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


class ChunkRepository:
    def __init__(self, settings: Settings) -> None:
        self.db = Database(settings)

    async def replace_chunks(
        self, task_id: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks 与 embeddings 数量不一致")
        async with self.db.connection() as conn:
            await conn.execute("DELETE FROM chunks WHERE task_id = %s", (task_id,))
            if not chunks:
                return 0
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO chunks "
                    "(id, task_id, video_id, text, start_ms, end_ms, "
                    "evidence_ids, modalities, content_hash, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector) "
                    "ON CONFLICT (task_id, content_hash) DO UPDATE SET "
                    "text = EXCLUDED.text, embedding = EXCLUDED.embedding",
                    [
                        (
                            chunk.chunk_id,
                            chunk.task_id,
                            chunk.video_id,
                            chunk.text,
                            chunk.start_ms,
                            chunk.end_ms,
                            Jsonb(list(chunk.evidence_ids)),
                            list(chunk.modalities),
                            chunk.content_hash,
                            _vector_literal(vector),
                        )
                        for chunk, vector in zip(chunks, embeddings, strict=True)
                    ],
                )
                return cursor.rowcount

    async def list_chunks(self, task_id: str) -> list[Chunk]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM chunks WHERE task_id = %s ORDER BY start_ms",
                (task_id,),
            )
            rows = await cursor.fetchall()
        return [
            Chunk(
                chunk_id=str(row["id"]),
                task_id=str(row["task_id"]),
                video_id=str(row["video_id"]),
                text=row["text"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                evidence_ids=tuple(str(item) for item in (row["evidence_ids"] or [])),
                modalities=tuple(row["modalities"] or []),
                content_hash=row["content_hash"],
            )
            for row in rows
        ]

    async def vector_search(
        self,
        task_id: str,
        query_vector: list[float],
        top_k: int = 50,
        *,
        video_id: str | None = None,
        modalities: list[str] | None = None,
        time_start_ms: int | None = None,
        time_end_ms: int | None = None,
    ) -> list[tuple[str, float]]:
        literal = _vector_literal(query_vector)
        conditions = ["task_id = %s", "embedding IS NOT NULL"]
        filter_params: list = [task_id]
        if video_id:
            conditions.append("video_id = %s")
            filter_params.append(video_id)
        if modalities:
            conditions.append("modalities && %s")
            filter_params.append(modalities)
        if time_start_ms is not None:
            conditions.append("end_ms >= %s")
            filter_params.append(time_start_ms)
        if time_end_ms is not None:
            conditions.append("start_ms <= %s")
            filter_params.append(time_end_ms)
        sql = (
            "SELECT id, 1 - (embedding <=> %s::vector) AS score "
            f"FROM chunks WHERE {' AND '.join(conditions)} "
            "ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        async with self.db.connection() as conn:
            cursor = await conn.execute(sql, (literal, *filter_params, literal, top_k))
            rows = await cursor.fetchall()
        return [(str(row["id"]), float(row["score"])) for row in rows]

    async def count_chunks(self, task_id: str) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS count FROM chunks WHERE task_id = %s",
                (task_id,),
            )
            row = await cursor.fetchone()
            return int(row["count"])

