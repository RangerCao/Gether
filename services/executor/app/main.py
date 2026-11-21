"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import router
from app.core.callbacks import CallbackClient
from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.storage import build_storage
from app.discovery.bilibili_api import BilibiliApiSearchProvider
from app.discovery.metadata import BilibiliMetadataClient
from app.discovery.repository import DiscoveryRepository
from app.discovery.search import BilibiliSearchProvider, YouTubeSearchProvider
from app.discovery.service import DiscoveryService
from app.graph.repository import ReportRepository
from app.graph.service import PostgresCheckpointer, ReportService
from app.graph.workflow import ReportWorkflow
from app.index.embedding import build_embedding
from app.index.repository import ChunkRepository
from app.index.service import IndexService
from app.ingest.pipeline import IngestPipeline
from app.ingest.sources.registry import SourceRegistry
from app.llm.base import LLMClient
from app.llm.factory import build_fallback_client, build_llm_client
from app.repository import TaskRepository
from app.services.parse_service import ParseService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    log = get_logger("app")
    log.info("executor_started", version=__version__, **settings.effective_config())

    async def recover_stale_tasks() -> None:
        try:
            count = await app.state.repository.fail_parsing_tasks()
            if count:
                log.warning("stale_parsing_tasks_failed", count=count)
        except Exception as exc:
            log.warning("stale_task_recovery_skipped", error=str(exc))

    recovery = asyncio.create_task(recover_stale_tasks())
    yield
    await app.state.llm.aclose()
    await app.state.embedder.aclose()
    if app.state.discovery_service is not None:
        await app.state.discovery_service.aclose()
    if app.state.callback is not None:
        await app.state.callback.aclose()
    await app.state.report_service.aclose()
    fallback: LLMClient | None = app.state.fallback_llm
    if fallback is not None:
        await fallback.aclose()
    if not recovery.done():
        recovery.cancel()
    log.info("executor_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.app_log_level)

    app = FastAPI(
        title="Gether Executor",
        version=__version__,
        description="多源长视频研报生成 Agent - AI 执行层",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.llm = build_llm_client(settings)
    app.state.fallback_llm = build_fallback_client(settings)
    app.state.sources = SourceRegistry(settings)
    app.state.storage = build_storage(settings)
    app.state.repository = TaskRepository(settings)
    app.state.embedder = build_embedding(settings)
    app.state.chunk_repository = ChunkRepository(settings)
    app.state.index_service = IndexService(
        settings=settings,
        task_repository=app.state.repository,
        chunk_repository=app.state.chunk_repository,
        embedder=app.state.embedder,
    )
    app.state.ingest = IngestPipeline(
        settings, storage=app.state.storage, llm=app.state.llm
    )
    app.state.report_repository = ReportRepository(settings)
    app.state.report_service = ReportService(
        workflow=ReportWorkflow(
            llm=app.state.llm,
            index_service=app.state.index_service,
            task_repository=app.state.repository,
            settings=settings,
        ),
        task_repository=app.state.repository,
        report_repository=app.state.report_repository,
        checkpointer=PostgresCheckpointer(settings),
    )
    app.state.callback = (
        CallbackClient(settings.governor_base_url, settings.callback_hmac_secret)
        if settings.governor_base_url
        else None
    )
    app.state.parse_service = ParseService(
        repository=app.state.repository,
        pipeline=app.state.ingest,
        sources=app.state.sources,
        callback=app.state.callback,
    )
    app.state.discovery_service = None
    if settings.discovery_enabled:
        cookie_path = settings.bilibili_cookies_file
        if cookie_path and not Path(cookie_path).is_absolute():
            cookie_path = str(PROJECT_ROOT / cookie_path)
        providers = []
        enabled_sources = {item.strip() for item in settings.discovery_sources.split(",")}
        if "bilibili" in enabled_sources:
            if settings.discovery_bilibili_search_mode == "ytdlp":
                providers.append(
                    BilibiliSearchProvider(
                        cookies_file=cookie_path,
                        sleep_requests=settings.ytdlp_sleep_requests,
                        proxy=settings.ytdlp_proxy,
                        retries=settings.discovery_search_retries,
                        backoff_seconds=settings.discovery_search_backoff_seconds,
                        cooldown_seconds=settings.discovery_bilibili_cooldown_seconds,
                    )
                )
            else:
                providers.append(
                    BilibiliApiSearchProvider(
                        cookies_file=cookie_path,
                        proxy=settings.ytdlp_proxy,
                        retries=min(settings.discovery_search_retries, 2),
                        backoff_seconds=settings.discovery_search_backoff_seconds,
                        cooldown_seconds=settings.discovery_bilibili_cooldown_seconds,
                        min_interval_seconds=1.0,
                        timeout_seconds=10.0,
                    )
                )
        if "youtube" in enabled_sources:
            providers.append(
                YouTubeSearchProvider(
                    cookies_file=settings.youtube_cookies_file,
                    proxy=settings.ytdlp_proxy,
                )
            )
        app.state.discovery_service = DiscoveryService(
            settings=settings,
            repository=DiscoveryRepository(settings),
            search_providers=providers,
            metadata_client=BilibiliMetadataClient(cookies_file=cookie_path),
            embedder=app.state.embedder,
            llm=app.state.llm,
            parse_service=app.state.parse_service,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        get_logger("validation").warning(
            "request_validation_failed path=%s errors=%s",
            request.url.path,
            exc.errors(),
        )
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    return app


app = create_app()
