"""单视频研报入口：BV/链接归一化 → 元数据补全 → 创建任务。

与多视频任务的差别只在 input_json：
- kind=single_video，研报走固定六段式 + 时间轴；
- video_meta 提供封面、UP 主、播放量等头部信息；
- single_question 保存用户真正的问题（用于额外章节）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.contracts import VideoRef, VideoSourceName
from app.core.config import PROJECT_ROOT, Settings
from app.discovery.metadata import BilibiliMetadataClient
from app.ingest.sources.base import SourceError
from app.ingest.sources.registry import SourceRegistry
from app.services.parse_service import ParseService

logger = logging.getLogger(__name__)


class SingleVideoError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SingleVideoService:
    def __init__(
        self,
        *,
        sources: SourceRegistry,
        parse_service: ParseService,
        settings: Settings,
        metadata_client: BilibiliMetadataClient | None = None,
    ) -> None:
        self.sources = sources
        self.parse_service = parse_service
        self.settings = settings
        self._metadata = metadata_client

    @property
    def metadata_client(self) -> BilibiliMetadataClient:
        if self._metadata is None:
            path = self.settings.bilibili_cookies_file
            if path and not Path(path).is_absolute():
                path = str(PROJECT_ROOT / path)
            self._metadata = BilibiliMetadataClient(cookies_file=path)
        return self._metadata

    async def create(
        self,
        *,
        input: str,
        question: str | None = None,
        mode: str = "fast",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        raw = (input or "").strip()
        if not raw:
            raise SingleVideoError("INVALID_INPUT", "请输入 B 站 BV 号或视频链接")
        try:
            ref = await self.sources.normalize(raw)
        except SourceError as exc:
            raise SingleVideoError(exc.code, exc.message) from exc
        if ref.source != VideoSourceName.BILIBILI:
            raise SingleVideoError(
                "UNSUPPORTED_SOURCE", "单视频研报目前只支持 B 站视频"
            )

        meta = await self._fetch_meta(ref.source_video_id)
        if meta:
            ref = ref.model_copy(
                update={
                    "title": meta.get("title") or ref.title,
                    "duration_ms": meta.get("duration_ms") or ref.duration_ms,
                }
            )
        video_meta = meta or self._fallback_meta(ref)
        video_meta.setdefault("page", ref.page)
        if not video_meta.get("url"):
            video_meta["url"] = ref.url
        if not video_meta.get("source_video_id"):
            video_meta["source_video_id"] = ref.source_video_id

        user_question = (question or "").strip()
        label = (
            user_question
            or (f"单视频研报 · 《{video_meta.get('title')}》" if video_meta.get("title") else "")
        )
        # av / 短链统一落到 bvid，前端播放器与引用都依赖 bvid。
        target_url = ref.url
        canonical_id = str(video_meta.get("source_video_id") or "")
        if canonical_id.lower().startswith("bv"):
            target_url = str(video_meta.get("url") or "").strip() or (
                f"https://www.bilibili.com/video/{canonical_id}"
            )
            if ref.page > 1:
                target_url = f"{target_url}?p={ref.page}"
        task_id = await self.parse_service.create_task(
            [target_url],
            user_id=user_id,
            question=label or None,
            mode=mode,
            extra={
                "kind": "single_video",
                "video_meta": video_meta,
                "single_question": user_question,
            },
        )
        return {
            "task_id": task_id,
            "status": "PENDING",
            "kind": "single_video",
            "video": video_meta,
        }

    async def _fetch_meta(self, video_id: str) -> dict[str, Any] | None:
        if video_id.lower().startswith("ep"):
            # 番剧接口与普通稿件不同，暂不补全元数据，解析仍可继续。
            return None
        try:
            return await self.metadata_client.fetch_video(video_id)
        except Exception as exc:
            logger.warning("single_video_metadata_failed id=%s: %s", video_id, exc)
            return None

    @staticmethod
    def _fallback_meta(ref: VideoRef) -> dict[str, Any]:
        return {
            "source": "bilibili",
            "source_video_id": ref.source_video_id,
            "url": ref.url,
            "title": ref.title or "",
            "duration_ms": ref.duration_ms,
            "uploader": None,
            "published_at": None,
            "views": None,
            "description": None,
            "cover": None,
        }

    async def aclose(self) -> None:
        if self._metadata is not None:
            await self._metadata.aclose()
            self._metadata = None
