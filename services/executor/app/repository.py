"""任务与证据持久化。"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from app.contracts import EvidencePacket, TaskStatus, VideoRef
from app.core.config import Settings
from app.core.db import Database

TASK_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.PENDING.value: {
        TaskStatus.PARSING.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.PARSING.value: {
        TaskStatus.PLANNING.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.PLANNING.value: {
        TaskStatus.GENERATING.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.GENERATING.value: {
        TaskStatus.DONE.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.FAILED.value: {TaskStatus.PENDING.value, TaskStatus.CANCELLED.value},
    TaskStatus.DONE.value: set(),
    TaskStatus.CANCELLED.value: set(),
}


class InvalidTaskTransition(RuntimeError):
    def __init__(self, task_id: str, from_status: str, to_status: str) -> None:
        super().__init__(f"非法状态迁移：{from_status} -> {to_status}（task={task_id}）")
        self.task_id = task_id
        self.from_status = from_status
        self.to_status = to_status


class TaskRepository:
    def __init__(self, settings: Settings) -> None:
        self.db = Database(settings)

    async def create_task(self, input_json: dict[str, Any], user_id: str | None = None) -> str:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO tasks (user_id, status, input_json) "
                "VALUES (%s, 'PENDING', %s) RETURNING id",
                (user_id, Jsonb(input_json)),
            )
            row = await cursor.fetchone()
            return str(row["id"])

    async def fail_parsing_tasks(self) -> int:
        """服务重启后，进程内的解析/研报任务已丢失；标记为可重试失败。"""
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE tasks SET status = 'FAILED', progress = 100, "
                "error_code = CASE WHEN status = 'PARSING' "
                "THEN 'PARSE_INTERRUPTED' ELSE 'REPORT_INTERRUPTED' END, "
                "error_message = CASE WHEN status = 'PARSING' "
                "THEN '执行层重启，解析任务已中断，请重新开始解析' "
                "ELSE '执行层重启，研报任务已中断，请重新生成研报' END, "
                "version = version + 1, updated_at = now() "
                "WHERE status IN ('PARSING','GENERATING') RETURNING id"
            )
            rows = await cursor.fetchall()
            return len(rows)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT t.*, "
                "(SELECT count(*) FROM evidence_packets e WHERE e.task_id = t.id) AS evidence_count, "
                "(SELECT count(*) FROM videos v WHERE v.task_id = t.id) AS video_count "
                "FROM tasks t WHERE t.id = %s",
                (task_id,),
            )
            return await cursor.fetchone()

    async def transition(
        self,
        task_id: str,
        from_status: str,
        to_status: str,
        *,
        progress: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        if to_status not in TASK_TRANSITIONS.get(from_status, set()):
            raise InvalidTaskTransition(task_id, from_status, to_status)
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE tasks SET status = %s, "
                "progress = COALESCE(%s, progress), "
                "error_code = %s, error_message = %s, "
                "version = version + 1, updated_at = now() "
                "WHERE id = %s AND status = %s RETURNING id",
                (
                    to_status,
                    progress,
                    error_code,
                    error_message,
                    task_id,
                    from_status,
                ),
            )
            return await cursor.fetchone() is not None

    async def update_progress(self, task_id: str, progress: int) -> None:
        clamped = max(0, min(progress, 100))
        async with self.db.connection() as conn:
            await conn.execute(
                "UPDATE tasks SET progress = %s, updated_at = now() "
                "WHERE id = %s AND status NOT IN ('DONE','CANCELLED')",
                (clamped, task_id),
            )

    async def upsert_video(self, task_id: str, ref: VideoRef) -> str:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO videos "
                "(task_id, source, source_video_id, page, url, title, duration_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (task_id, source, source_video_id, page) DO UPDATE SET "
                "url = EXCLUDED.url, "
                "title = COALESCE(EXCLUDED.title, videos.title), "
                "duration_ms = COALESCE(EXCLUDED.duration_ms, videos.duration_ms) "
                "RETURNING id",
                (
                    task_id,
                    ref.source.value,
                    ref.source_video_id,
                    ref.page,
                    ref.url,
                    ref.title,
                    ref.duration_ms,
                ),
            )
            row = await cursor.fetchone()
            return str(row["id"])

    async def insert_evidence(self, packets: list[EvidencePacket]) -> int:
        if not packets:
            return 0
        params = [
            (
                packet.evidence_id,
                packet.task_id,
                packet.video_id,
                packet.modality.value,
                packet.text,
                packet.raw_text,
                packet.start_ms,
                packet.end_ms,
                packet.frame_ref,
                packet.confidence,
                packet.source_type.value,
                packet.content_hash,
            )
            for packet in packets
        ]
        async with self.db.connection() as conn, conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO evidence_packets "
                "(id, task_id, video_id, modality, text, raw_text, start_ms, end_ms, "
                "frame_ref, confidence, source_type, content_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (video_id, content_hash) DO NOTHING",
                params,
            )
            return cursor.rowcount

    async def video_evidence_count(self, video_id: str) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS count FROM evidence_packets WHERE video_id = %s",
                (video_id,),
            )
            row = await cursor.fetchone()
            return int(row["count"])

    async def count_evidence(self, task_id: str) -> int:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS count FROM evidence_packets WHERE task_id = %s",
                (task_id,),
            )
            row = await cursor.fetchone()
            return int(row["count"])

    async def list_evidence(self, task_id: str, limit: int = 500) -> list[EvidencePacket]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM evidence_packets WHERE task_id = %s "
                "ORDER BY start_ms LIMIT %s",
                (task_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            EvidencePacket(
                evidence_id=str(row["id"]),
                task_id=str(row["task_id"]),
                video_id=str(row["video_id"]),
                modality=row["modality"],
                text=row["text"],
                raw_text=row.get("raw_text"),
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                frame_ref=row["frame_ref"],
                confidence=row["confidence"],
                source_type=row["source_type"],
                content_hash=row["content_hash"],
                created_at=row["created_at"].isoformat() if row["created_at"] else None,
            )
            for row in rows
        ]

    async def list_videos(self, task_id: str) -> list[dict[str, Any]]:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, source, source_video_id, page, url, title, duration_ms "
                "FROM videos WHERE task_id = %s ORDER BY created_at",
                (task_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]
