"""HTTP 路由：健康检查、源归一化、任务与证据。"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import __version__
from app.discovery.service import DiscoveryError
from app.graph.service import ReportError
from app.index.service import IndexNotFound
from app.ingest.sources.base import SourceError
from app.services.parse_service import TaskNotFound

router = APIRouter()
logger = logging.getLogger(__name__)


class NormalizeRequest(BaseModel):
    url: str = Field(min_length=1)


class CreateTaskRequest(BaseModel):
    videos: list[str] = Field(min_length=1)
    user_id: str | None = None
    question: str | None = None


class DiscoveryRequest(BaseModel):
    question: str = Field(min_length=2)
    max_videos: int | None = Field(default=None, ge=1, le=8)
    duration_budget_min: int | None = Field(default=None, ge=10, le=600)
    min_views: int | None = Field(default=None, ge=0, le=100_000_000)
    mode: Literal["fast", "quality"] = "quality"


class DiscoveryConfirmRequest(BaseModel):
    selected_ids: list[str] | None = None


class ResumeReportRequest(BaseModel):
    outline: list[dict] | None = None
    approved: bool = True


class RetrieveRequest(BaseModel):
    task_id: str
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    candidate_k: int = Field(default=50, ge=1, le=500)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    vector_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    bm25_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    video_id: str | None = None
    modalities: list[Literal["asr", "ocr", "frame"]] | None = None
    time_start_ms: int | None = Field(default=None, ge=0)
    time_end_ms: int | None = Field(default=None, ge=0)


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "version": __version__,
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
        "sources": settings.enabled_sources,
    }


@router.get("/ready")
async def ready(request: Request) -> dict:
    repository = request.app.state.repository
    try:
        async with repository.db.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:  # 依赖不可用时返回 503
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DB_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return {"status": "ready"}


@router.get("/internal/sources")
async def list_sources(request: Request) -> dict:
    return {"sources": request.app.state.sources.describe()}


@router.post("/internal/sources/normalize")
async def normalize_source(payload: NormalizeRequest, request: Request) -> dict:
    try:
        ref = await request.app.state.sources.normalize(payload.url)
    except SourceError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": exc.message}
        ) from exc
    return ref.model_dump(mode="json")


@router.get("/internal/config/effective")
async def effective_config(request: Request) -> dict:
    settings = request.app.state.settings
    if settings.app_env != "local":
        raise HTTPException(status_code=404, detail="not found")
    return settings.effective_config()


@router.post("/internal/discovery/sessions")
async def discover(payload: DiscoveryRequest, request: Request) -> dict:
    service = request.app.state.discovery_service
    if service is None:
        raise HTTPException(status_code=404, detail={"code": "DISCOVERY_DISABLED"})
    try:
        return await service.discover(
            question=payload.question,
            max_videos=payload.max_videos,
            duration_budget_min=payload.duration_budget_min,
            min_views=payload.min_views,
            mode=payload.mode,
        )
    except DiscoveryError as exc:
        raise HTTPException(status_code=422, detail={"code": "DISCOVERY_FAILED", "message": str(exc)}) from exc


@router.get("/internal/discovery/sessions/{session_id}")
async def get_discovery(session_id: str, request: Request) -> dict:
    service = request.app.state.discovery_service
    if service is None:
        raise HTTPException(status_code=404, detail={"code": "DISCOVERY_DISABLED"})
    try:
        return await service.get_session(session_id)
    except DiscoveryError as exc:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": str(exc)}) from exc


@router.post("/internal/discovery/sessions/{session_id}/confirm")
async def confirm_discovery(
    session_id: str, payload: DiscoveryConfirmRequest, request: Request
) -> dict:
    service = request.app.state.discovery_service
    if service is None:
        raise HTTPException(status_code=404, detail={"code": "DISCOVERY_DISABLED"})
    try:
        task_id = await service.confirm(session_id, payload.selected_ids)
    except DiscoveryError as exc:
        raise HTTPException(status_code=422, detail={"code": "DISCOVERY_CONFIRM_FAILED", "message": str(exc)}) from exc
    return {"session_id": session_id, "task_id": task_id, "status": "PENDING"}


@router.post("/internal/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(payload: CreateTaskRequest, request: Request) -> dict:
    service = request.app.state.parse_service
    try:
        task_id = await service.create_task(
            payload.videos, user_id=payload.user_id, question=payload.question
        )
    except SourceError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": exc.message}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_INPUT", "message": str(exc)}) from exc
    return {"task_id": task_id, "status": "PENDING"}


@router.post("/internal/tasks/{task_id}/parse", status_code=status.HTTP_202_ACCEPTED)
async def parse_task(
    task_id: str,
    request: Request,
    background: BackgroundTasks,
) -> dict:
    service = request.app.state.parse_service
    try:
        task = await service.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND"}) from exc
    if task["status"] not in ("PENDING", "FAILED"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INVALID_TASK_STATE",
                "message": f"当前状态 {task['status']} 不能开始解析",
            },
        )
    background.add_task(
        _parse_and_index,
        service,
        request.app.state.index_service,
        task_id,
        request.app.state.settings.auto_index_after_parse,
    )
    return {"task_id": task_id, "status": "PARSING", "accepted": True}


async def _parse_and_index(service, index_service, task_id: str, auto_index: bool) -> None:
    try:
        await service.parse_task(task_id)
    except Exception:
        logger.exception("parse_task_failed task=%s", task_id)
        return
    if not auto_index:
        return
    task = await service.get_task(task_id)
    if task.get("status") != "PLANNING":
        return
    try:
        await index_service.index_task(task_id)
    except Exception:
        logger.exception("auto_index_failed task=%s", task_id)


@router.post("/internal/tasks/{task_id}/plan")
async def plan_report(task_id: str, request: Request) -> dict:
    service = request.app.state.report_service
    try:
        return await service.start(task_id)
    except ReportError as exc:
        raise HTTPException(status_code=409, detail={"code": "REPORT_STATE", "message": str(exc)}) from exc


@router.post("/internal/tasks/{task_id}/resume")
async def resume_report(
    task_id: str, payload: ResumeReportRequest, request: Request
) -> dict:
    service = request.app.state.report_service
    try:
        return await service.resume(
            task_id, outline=payload.outline, approved=payload.approved
        )
    except ReportError as exc:
        raise HTTPException(status_code=409, detail={"code": "REPORT_FAILED", "message": str(exc)}) from exc


@router.post("/internal/tasks/{task_id}/resume/stream")
async def resume_report_stream(
    task_id: str, payload: ResumeReportRequest, request: Request
) -> StreamingResponse:
    service = request.app.state.report_service
    return StreamingResponse(
        service.stream_resume(task_id, outline=payload.outline, approved=payload.approved),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/internal/tasks/{task_id}/report")
async def get_report(task_id: str, request: Request) -> dict:
    service = request.app.state.report_service
    try:
        return await service.get_report(task_id)
    except ReportError as exc:
        raise HTTPException(status_code=404, detail={"code": "REPORT_NOT_FOUND", "message": str(exc)}) from exc


@router.get("/internal/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict:
    service = request.app.state.parse_service
    try:
        return await service.get_task_summary(task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND"}) from exc


@router.post("/internal/tasks/{task_id}/index")
async def index_task(task_id: str, request: Request) -> dict:
    service = request.app.state.parse_service
    index_service = request.app.state.index_service
    try:
        await service.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND"}) from exc
    return await index_service.index_task(task_id)


@router.get("/internal/tasks/{task_id}/chunks")
async def list_chunks(task_id: str, request: Request) -> dict:
    service = request.app.state.parse_service
    try:
        await service.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND"}) from exc
    chunks = await request.app.state.chunk_repository.list_chunks(task_id)
    return {"task_id": task_id, "count": len(chunks), "chunks": [chunk.as_dict() for chunk in chunks]}


@router.post("/internal/retrieve")
async def retrieve(payload: RetrieveRequest, request: Request) -> dict:
    try:
        hits = await request.app.state.index_service.search(
            payload.task_id,
            payload.query,
            top_k=payload.top_k,
            candidate_k=payload.candidate_k,
            rrf_k=payload.rrf_k,
            vector_weight=payload.vector_weight,
            bm25_weight=payload.bm25_weight,
            video_id=payload.video_id,
            modalities=list(payload.modalities) if payload.modalities else None,
            time_start_ms=payload.time_start_ms,
            time_end_ms=payload.time_end_ms,
        )
    except IndexNotFound as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "INDEX_NOT_FOUND", "message": str(exc)},
        ) from exc
    return {
        "task_id": payload.task_id,
        "query": payload.query,
        "filters": {
            "video_id": payload.video_id,
            "modalities": payload.modalities,
            "time_start_ms": payload.time_start_ms,
            "time_end_ms": payload.time_end_ms,
        },
        "count": len(hits),
        "hits": [hit.as_dict() for hit in hits],
    }


@router.get("/internal/tasks/{task_id}/evidence")
async def get_evidence(
    task_id: str,
    request: Request,
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    service = request.app.state.parse_service
    try:
        packets = await service.list_evidence(task_id, limit=limit)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND"}) from exc
    return {
        "task_id": task_id,
        "count": len(packets),
        "evidence": [packet.model_dump(mode="json") for packet in packets],
    }
