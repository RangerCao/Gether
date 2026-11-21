"""任务创建与解析编排。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.contracts import TaskStatus, VideoRef
from app.core.callbacks import CallbackClient
from app.ingest.pipeline import IngestPipeline
from app.ingest.sources.registry import SourceRegistry
from app.repository import InvalidTaskTransition, TaskRepository

logger = logging.getLogger(__name__)


class TaskNotFound(RuntimeError):
    pass


class InvalidTaskState(RuntimeError):
    pass


class ParseService:
    def __init__(
        self,
        *,
        repository: TaskRepository,
        pipeline: IngestPipeline,
        sources: SourceRegistry,
        callback: CallbackClient | None = None,
    ) -> None:
        self.repository = repository
        self.pipeline = pipeline
        self.sources = sources
        self.callback = callback

    async def create_task(
        self,
        urls: list[str],
        user_id: str | None = None,
        *,
        question: str | None = None,
        mode: str = "quality",
    ) -> str:
        cleaned = [url.strip() for url in urls if url and url.strip()]
        if not cleaned:
            raise ValueError("至少提交一个视频链接")
        # 创建前先归一化，尽早拒绝非法/不支持的链接
        refs = [await self.sources.normalize(url) for url in cleaned]
        payload = {"videos": [ref.model_dump(mode="json") for ref in refs]}
        if question:
            payload["question"] = question.strip()
        payload["mode"] = mode if mode in {"fast", "quality"} else "quality"
        return await self.repository.create_task(payload, user_id=user_id)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        task = await self.repository.get_task(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        return task

    async def parse_task(self, task_id: str) -> dict[str, Any]:
        task = await self.get_task(task_id)
        status = task["status"]
        if status == TaskStatus.FAILED.value:
            await self.repository.transition(task_id, status, TaskStatus.PENDING.value)
            status = TaskStatus.PENDING.value
        if status != TaskStatus.PENDING.value:
            raise InvalidTaskState(f"任务当前状态为 {status}，不能开始解析")

        started = await self.repository.transition(
            task_id,
            TaskStatus.PENDING.value,
            TaskStatus.PARSING.value,
            progress=1,
        )
        if not started:
            raise InvalidTaskState("任务状态已被其他请求修改")

        videos = task.get("input_json", {}).get("videos", [])
        mode = str(task.get("input_json", {}).get("mode") or "quality")
        warnings: list[str] = []
        failures: list[dict[str, str]] = []
        total = max(len(videos), 1)
        callback_seq = 0
        callback_lock = asyncio.Lock()
        progress_lock = asyncio.Lock()
        last_progress = 1
        stage_started: dict[tuple[int, str], float] = {}

        async def notify(event: str, payload: dict[str, Any]) -> None:
            nonlocal callback_seq
            if self.callback is None:
                return
            async with callback_lock:
                callback_seq += 1
                seq = callback_seq
            await self.callback.send(task_id, event, seq, payload)

        async def on_progress(
            stage: str,
            current: int,
            step_total: int,
            message: str,
            index: int,
        ) -> None:
            nonlocal last_progress
            key = (index, stage)
            if current == 0 or key not in stage_started:
                stage_started[key] = time.monotonic()
            elapsed_ms = None
            if current >= step_total:
                elapsed_ms = int(
                    (time.monotonic() - stage_started.pop(key, time.monotonic())) * 1000
                )
            fraction = (current / step_total) if step_total else 0.0
            overall = 5 + int(((index + fraction) / total) * 80)
            async with progress_lock:
                last_progress = max(last_progress, min(overall, 85))
                progress = last_progress
            await self.repository.update_progress(task_id, progress)
            payload: dict[str, Any] = {
                "status": "PARSING",
                "stage": stage,
                "message": message,
                "progress": progress,
                "video_index": index + 1,
                "video_count": total,
            }
            if elapsed_ms is not None:
                payload["elapsed_ms"] = elapsed_ms
            await notify("stage", payload)

        async def parse_one(index: int, item: Any) -> None:
            try:
                ref = await self._coerce_ref(item)
                video_id = await self.repository.upsert_video(task_id, ref)
                if await self.repository.video_evidence_count(video_id) > 0:
                    warnings.append(f"跳过已完成视频：{ref.source_video_id}")
                    return
                result = await self.pipeline.parse_video(
                    task_id=task_id,
                    video_id=video_id,
                    ref=ref,
                    progress=lambda stage, current, step_total, message: on_progress(
                        stage, current, step_total, message, index
                    ),
                    mode=mode,
                )
                inserted = await self.repository.insert_evidence(result.evidence)
                warnings.extend(result.warnings)
                await notify(
                    "video_parsed",
                    {
                        "status": "PARSING",
                        "stage": "video_done",
                        "message": f"视频 {index + 1}/{total} 解析完成",
                        "progress": min(90, 5 + int(((index + 1) / total) * 80)),
                        "video_index": index + 1,
                        "video_count": total,
                        "evidence_count": len(result.evidence),
                        "source_video_id": ref.source_video_id,
                    },
                )
                logger.info(
                    "video_parsed task=%s video=%s evidence=%s inserted=%s",
                    task_id,
                    ref.source_video_id,
                    len(result.evidence),
                    inserted,
                )
            except Exception as exc:
                code = getattr(exc, "code", "PARSE_FAILED")
                message = getattr(exc, "message", str(exc))
                failures.append(
                    {"url": self._url_of(item), "code": code, "message": message}
                )
                logger.warning(
                    "video_parse_failed task=%s code=%s msg=%s", task_id, code, message
                )
                await notify(
                    "video_failed",
                    {
                        "status": "PARSING",
                        "stage": "video_failed",
                        "message": f"第 {index + 1}/{total} 个视频解析失败：{message}",
                        "video_index": index + 1,
                        "video_count": total,
                        "code": code,
                        "detail": message,
                        "url": self._url_of(item),
                    },
                )

        try:
            await notify(
                "stage",
                {
                    "status": "PARSING",
                    "stage": "parse_started",
                    "message": "开始解析视频集合",
                    "progress": 1,
                    "video_count": total,
                },
            )

            pipeline_settings = getattr(self.pipeline, "settings", None)
            concurrency = getattr(
                pipeline_settings,
                "parse_concurrency_fast" if mode == "fast" else "parse_concurrency_quality",
                2 if mode == "fast" else 1,
            )
            semaphore = asyncio.Semaphore(max(concurrency, 1))

            async def bounded_parse(index: int, item: Any) -> None:
                async with semaphore:
                    await parse_one(index, item)

            await asyncio.gather(
                *(bounded_parse(index, item) for index, item in enumerate(videos))
            )

            evidence_count = await self.repository.count_evidence(task_id)
            if evidence_count == 0:
                reason = failures[0]["message"] if failures else "没有可用的视频证据"
                await self.repository.transition(
                    task_id,
                    TaskStatus.PARSING.value,
                    TaskStatus.FAILED.value,
                    progress=100,
                    error_code="NO_EVIDENCE",
                    error_message=reason,
                )
                await notify(
                    "parse_failed",
                    {
                        "status": "FAILED",
                        "stage": "no_evidence",
                        "code": "NO_EVIDENCE",
                        "message": reason,
                        "detail": reason,
                        "failures": failures,
                    },
                )
                return {
                    "task_id": task_id,
                    "status": TaskStatus.FAILED.value,
                    "evidence_count": 0,
                    "warnings": warnings,
                    "failures": failures,
                }

            await self.repository.transition(
                task_id,
                TaskStatus.PARSING.value,
                TaskStatus.PLANNING.value,
                progress=90,
            )
            await notify(
                "parse_finished",
                {
                    "status": "PLANNING",
                    "stage": "parse_finished",
                    "message": f"解析完成，共 {evidence_count} 条证据",
                    "progress": 90,
                    "evidence_count": evidence_count,
                },
            )
            return {
                "task_id": task_id,
                "status": TaskStatus.PLANNING.value,
                "evidence_count": evidence_count,
                "warnings": warnings,
                "failures": failures,
            }
        except Exception as exc:
            logger.exception("parse_task_crashed task=%s", task_id)
            await self._safe_fail(task_id, "INTERNAL_ERROR", str(exc))
            await notify(
                "parse_failed",
                {
                    "status": "FAILED",
                    "stage": "internal_error",
                    "code": "INTERNAL_ERROR",
                    "message": f"解析内部错误：{exc}",
                    "detail": str(exc),
                },
            )
            raise

    async def get_task_summary(self, task_id: str) -> dict[str, Any]:
        task = await self.get_task(task_id)
        videos = await self.repository.list_videos(task_id)
        return {
            "task_id": str(task["id"]),
            "status": task["status"],
            "progress": task["progress"],
            "error_code": task["error_code"],
            "error_message": task["error_message"],
            "evidence_count": task["evidence_count"],
            "videos": videos,
            "created_at": task["created_at"].isoformat() if task["created_at"] else None,
            "updated_at": task["updated_at"].isoformat() if task["updated_at"] else None,
        }

    async def list_evidence(self, task_id: str, limit: int = 500):
        await self.get_task(task_id)
        return await self.repository.list_evidence(task_id, limit=limit)

    async def _coerce_ref(self, item: Any) -> VideoRef:
        if isinstance(item, VideoRef):
            return item
        if isinstance(item, dict):
            return VideoRef.model_validate(item)
        if isinstance(item, str):
            return await self.sources.normalize(item)
        raise ValueError(f"无法识别的视频输入：{item!r}")

    @staticmethod
    def _url_of(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("url", ""))
        return str(item)

    async def _safe_fail(self, task_id: str, code: str, message: str) -> None:
        task = await self.repository.get_task(task_id)
        if not task:
            return
        status = task["status"]
        try:
            await self.repository.transition(
                task_id,
                status,
                TaskStatus.FAILED.value,
                progress=100,
                error_code=code,
                error_message=message,
            )
        except InvalidTaskTransition:
            logger.warning("cannot_mark_failed task=%s status=%s", task_id, status)
