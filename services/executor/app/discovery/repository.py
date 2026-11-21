"""发现会话与候选视频持久化。"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from app.core.config import Settings
from app.core.db import Database
from app.discovery.models import Candidate, Facet


class DiscoveryRepository:
    def __init__(self, settings: Settings) -> None:
        self.db = Database(settings)

    async def create_session(
        self,
        *,
        question: str,
        constraints: dict,
        queries: list[str],
        facets: list[Facet],
    ) -> str:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO discovery_sessions "
                "(question, constraints_json, queries_json, facets_json, status) "
                "VALUES (%s, %s, %s, %s, 'DISCOVERING') RETURNING id",
                (
                    question,
                    Jsonb(constraints),
                    Jsonb(queries),
                    Jsonb([facet.as_dict() for facet in facets]),
                ),
            )
            row = await cursor.fetchone()
            return str(row["id"])

    async def save_candidates(self, session_id: str, candidates: list[Candidate]) -> int:
        if not candidates:
            return 0
        async with self.db.connection() as conn:
            await conn.execute(
                "DELETE FROM discovery_candidates WHERE session_id = %s", (session_id,)
            )
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO discovery_candidates "
                    "(id, session_id, source, source_video_id, title, url, duration_ms, "
                    "uploader, published_at, views, metrics_json, matched_facets, relevance, "
                    "authority, freshness, redundancy, final_score, cluster_id, selected, reason) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (
                            item.candidate_id,
                            session_id,
                            item.source,
                            item.source_video_id,
                            item.title,
                            item.url,
                            item.duration_ms,
                            item.uploader,
                            item.published_at,
                            item.views,
                            Jsonb(item.metrics),
                            Jsonb(item.matched_facets),
                            item.relevance,
                            item.authority,
                            item.freshness,
                            item.redundancy,
                            item.final_score,
                            item.cluster_id,
                            item.selected,
                            item.reason,
                        )
                        for item in candidates
                    ],
                )
            await conn.execute(
                "UPDATE discovery_sessions "
                "SET status = 'WAITING_VIDEO_SELECTION' WHERE id = %s",
                (session_id,),
            )
            return len(candidates)

    async def get_session(self, session_id: str) -> dict | None:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM discovery_sessions WHERE id = %s", (session_id,)
            )
            session = await cursor.fetchone()
            if session is None:
                return None
            cursor = await conn.execute(
                "SELECT * FROM discovery_candidates WHERE session_id = %s "
                "ORDER BY final_score DESC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        constraints = session["constraints_json"] or {}
        candidates = [_row_to_candidate(row) for row in rows]
        return {
            "session_id": str(session["id"]),
            "task_id": str(session["task_id"]) if session["task_id"] else None,
            "question": session["question"],
            "constraints": constraints,
            "ranking": constraints.get("ranking"),
            "queries": session["queries_json"],
            "facets": session["facets_json"],
            "status": session["status"],
            "warnings": session["warnings_json"],
            "candidates": candidates,
            "selected": [item for item in candidates if item["selected"]],
        }

    async def set_warnings(self, session_id: str, warnings: list[str]) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE discovery_sessions SET warnings_json = %s WHERE id = %s",
                (Jsonb(warnings), session_id),
            )

    async def confirm(self, session_id: str, task_id: str, selected_ids: list[str]) -> None:
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE discovery_candidates SET selected = FALSE WHERE session_id = %s",
                (session_id,),
            )
            if selected_ids:
                await conn.execute(
                    "UPDATE discovery_candidates SET selected = TRUE "
                    "WHERE session_id = %s AND id = ANY(%s::uuid[])",
                    (session_id, selected_ids),
                )
            await conn.execute(
                "UPDATE discovery_sessions SET task_id = %s, status = 'CONFIRMED' WHERE id = %s",
                (task_id, session_id),
            )


def _row_to_candidate(row: dict) -> dict:
    metrics = row["metrics_json"] or {}
    return {
        "candidate_id": str(row["id"]),
        "source": row["source"],
        "source_video_id": row["source_video_id"],
        "title": row["title"],
        "url": row["url"],
        "duration_ms": row["duration_ms"],
        "uploader": row["uploader"],
        "published_at": row["published_at"],
        "views": row["views"],
        "metrics": metrics,
        "matched_facets": row["matched_facets"],
        "relevance": row["relevance"],
        "authority": row["authority"],
        "freshness": row["freshness"],
        "redundancy": row["redundancy"],
        "final_score": row["final_score"],
        "topic_score": metrics.get("topic_score", 0.0),
        "topic_gate": metrics.get("topic_gate", True),
        "rejection_reason": metrics.get("rejection_reason"),
        "facet_confidence": metrics.get("facet_confidence", {}),
        "cluster_id": row["cluster_id"],
        "selected": row["selected"],
        "reason": row["reason"],
    }
