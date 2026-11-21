"""研报工作流的启动、HITL 恢复与持久化。"""

from __future__ import annotations

import json
import logging
import time

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.core.config import Settings
from app.core.db import build_dsn
from app.graph.repository import ReportRepository
from app.graph.workflow import ReportWorkflow
from app.repository import TaskRepository

logger = logging.getLogger(__name__)


class ReportError(RuntimeError):
    pass


class PostgresCheckpointer:
    def __init__(self, settings: Settings) -> None:
        self.dsn = build_dsn(settings)
        self._ctx = None
        self._saver = None

    async def get(self):
        if self._saver is not None:
            return self._saver
        self._ctx = AsyncPostgresSaver.from_conn_string(self.dsn)
        self._saver = await self._ctx.__aenter__()
        await self._saver.setup()
        return self._saver

    async def close(self) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None
            self._saver = None


class ReportService:
    def __init__(
        self,
        *,
        workflow: ReportWorkflow,
        task_repository: TaskRepository,
        report_repository: ReportRepository,
        checkpointer,
    ) -> None:
        self.workflow = workflow
        self.task_repository = task_repository
        self.report_repository = report_repository
        self.checkpointer = checkpointer

    async def _graph(self):
        saver = (
            await self.checkpointer.get()
            if isinstance(self.checkpointer, PostgresCheckpointer)
            else self.checkpointer
        )
        return self.workflow.build(saver)

    async def start(self, task_id: str) -> dict:
        task = await self.task_repository.get_task(task_id)
        if task is None:
            raise ReportError("任务不存在")
        if task["status"] not in {"PLANNING", "GENERATING"}:
            raise ReportError(f"任务状态为 {task['status']}，不能生成大纲")
        graph = await self._graph()
        input_json = task.get("input_json") or {}
        question = input_json.get("question", "")
        mode = str(input_json.get("mode") or "quality")
        state = await graph.ainvoke(
            {
                "task_id": task_id,
                "question": question,
                "mode": mode,
                "warnings": [],
                "outline": [],
            },
            config={"configurable": {"thread_id": task_id}},
        )
        if "__interrupt__" in state:
            return {
                "task_id": task_id,
                "status": "WAITING_APPROVAL",
                "outline": state.get("outline", []),
                "warnings": state.get("warnings", []),
            }
        return {
            "task_id": task_id,
            "status": "GENERATED",
            "outline": state.get("outline", []),
            "warnings": state.get("warnings", []),
        }

    async def resume(
        self,
        task_id: str,
        *,
        outline: list[dict] | None = None,
        approved: bool = True,
    ) -> dict:
        task = await self.task_repository.get_task(task_id)
        if task is None:
            raise ReportError("任务不存在")
        if task["status"] == "PLANNING":
            moved = await self.task_repository.transition(
                task_id, "PLANNING", "GENERATING", progress=95
            )
            if not moved:
                raise ReportError("任务状态已被其他请求修改")
        elif task["status"] != "GENERATING":
            raise ReportError(f"任务状态为 {task['status']}，不能恢复生成")
        graph = await self._graph()
        try:
            state = await graph.ainvoke(
                Command(resume={"outline": outline} if outline is not None else {"approved": approved}),
                config={"configurable": {"thread_id": task_id}},
            )
            if "__interrupt__" in state:
                return {
                    "task_id": task_id,
                    "status": "WAITING_APPROVAL",
                    "outline": state.get("outline", []),
                }
            await self.report_repository.save_report(
                task_id=task_id,
                outline=state.get("outline", []),
                drafts=state.get("drafts", {}),
                claims=state.get("claims", []),
                ledger=state.get("ledger", []),
                conflicts=state.get("conflicts", []),
            )
            await self.task_repository.transition(
                task_id, "GENERATING", "DONE", progress=100
            )
            return {
                "task_id": task_id,
                "status": "DONE",
                "report_markdown": state.get("report_markdown", ""),
                "claims": len(state.get("claims", [])),
                "ledger": len(state.get("ledger", [])),
                "conflicts": len(state.get("conflicts", [])),
                "citation_stats": state.get("citation_stats", {}),
                "warnings": state.get("warnings", []),
            }
        except Exception as exc:
            logger.exception("report_generation_failed task=%s", task_id)
            await self._safe_fail(task_id, str(exc))
            raise ReportError(f"研报生成失败：{exc}") from exc

    async def stream_resume(
        self,
        task_id: str,
        *,
        outline: list[dict] | None = None,
        approved: bool = True,
    ):
        task = await self.task_repository.get_task(task_id)
        if task is None:
            yield _sse("error", {"code": "TASK_NOT_FOUND", "message": "任务不存在", "stage": "load_task"})
            return
        if task["status"] == "PLANNING":
            moved = await self.task_repository.transition(
                task_id, "PLANNING", "GENERATING", progress=95
            )
            if not moved:
                yield _sse("error", {"message": "任务状态已被修改"})
                return
        elif task["status"] != "GENERATING":
            yield _sse(
                "error",
                {
                    "code": "REPORT_STATE",
                    "message": f"任务状态为 {task['status']}",
                    "stage": "load_task",
                },
            )
            return
        graph = await self._graph()
        config = {"configurable": {"thread_id": task_id}}
        started_at = time.monotonic()
        command = Command(
            resume={"outline": outline} if outline is not None else {"approved": approved}
        )
        try:
            snapshot = await graph.aget_state(config)
            total_sections = max(len(snapshot.values.get("outline", [])), 1)
            yield _sse(
                "state",
                {
                    "node": "start",
                    "task_id": task_id,
                    "progress": 5,
                    "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
            current_node = "start"
            async for chunk in graph.astream(command, config, stream_mode="updates"):
                for node, update in chunk.items():
                    current_node = node
                    progress = {
                        "planner": 10,
                        "retrieve": 20,
                        "critic": 30,
                        "parallel_draft": 90,
                    }.get(node)
                    if node == "writer" and isinstance(update, dict):
                        index = int(update.get("current_index", 0))
                        progress = min(90, 20 + int(70 * index / total_sections))
                        for section_id, text in (update.get("drafts") or {}).items():
                            yield _sse(
                                "token",
                                {
                                    "section_id": section_id,
                                    "delta": text,
                                    "progress": progress,
                                },
                            )
                    if node == "parallel_draft" and isinstance(update, dict):
                        drafts = update.get("drafts") or {}
                        for draft_index, (section_id, text) in enumerate(drafts.items(), start=1):
                            yield _sse(
                                "token",
                                {
                                    "section_id": section_id,
                                    "delta": text,
                                    "progress": min(
                                        90,
                                        20 + int(70 * draft_index / max(total_sections, 1)),
                                    ),
                                },
                            )
                    if node == "adjudicate":
                        progress = 92
                        if isinstance(update, dict):
                            for conflict in update.get("conflicts", []):
                                yield _sse("conflict", conflict)
                    yield _sse(
                        "state",
                        {
                            "node": node,
                            **({"progress": progress} if progress is not None else {}),
                            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                        },
                    )
            snapshot = await graph.aget_state(config)
            state = snapshot.values
            if "__interrupt__" in state:
                yield _sse("state", {"status": "WAITING_APPROVAL"})
                return
            await self.report_repository.save_report(
                task_id=task_id,
                outline=state.get("outline", []),
                drafts=state.get("drafts", {}),
                claims=state.get("claims", []),
                ledger=state.get("ledger", []),
                conflicts=state.get("conflicts", []),
            )
            await self.task_repository.transition(
                task_id, "GENERATING", "DONE", progress=100
            )
            for claim in state.get("claims", []):
                for evidence_id in claim.get("evidence_ids", []):
                    yield _sse(
                        "citation",
                        {
                            "claim_id": claim["claim_id"],
                            "evidence_id": evidence_id,
                            "verified": claim.get("verified", False),
                        },
                    )
            yield _sse(
                "done",
                {
                    "task_id": task_id,
                    "status": "DONE",
                    "progress": 100,
                    "claims": len(state.get("claims", [])),
                    "ledger": len(state.get("ledger", [])),
                    "conflicts": len(state.get("conflicts", [])),
                    "citation_stats": state.get("citation_stats", {}),
                },
            )
        except Exception as exc:
            logger.exception("report_stream_failed task=%s", task_id)
            await self._safe_fail(task_id, str(exc))
            yield _sse(
                "error",
                {
                    "code": "REPORT_FAILED",
                    "message": f"研报生成失败：{exc}",
                    "detail": str(exc),
                    "stage": locals().get("current_node", "generate"),
                    "retryable": False,
                },
            )

    async def get_report(self, task_id: str) -> dict:
        task = await self.task_repository.get_task(task_id)
        if task is None:
            raise ReportError("任务不存在")
        report = await self.report_repository.get_report(task_id)
        report["status"] = task["status"]
        return report

    async def _safe_fail(self, task_id: str, message: str) -> None:
        task = await self.task_repository.get_task(task_id)
        if not task:
            return
        status = task["status"]
        if status == "GENERATING":
            await self.task_repository.transition(
                task_id,
                "GENERATING",
                "FAILED",
                progress=100,
                error_code="REPORT_FAILED",
                error_message=message[:500],
            )

    async def aclose(self) -> None:
        if hasattr(self.checkpointer, "close"):
            await self.checkpointer.close()


def _sse(event: str, payload: dict) -> str:
    return "event: " + event + chr(10) + "data: " + json.dumps(payload, ensure_ascii=False) + chr(10) + chr(10)
