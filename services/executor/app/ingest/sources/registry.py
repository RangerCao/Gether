"""源注册表：按 URL 选择适配器，统一输出 VideoRef。"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import VideoRef
from app.core.config import Settings
from app.ingest.sources.base import UnsupportedSourceError, VideoSource
from app.ingest.sources.bilibili import BilibiliSource
from app.ingest.sources.local import LocalSource
from app.ingest.sources.youtube import YouTubeSource


@dataclass
class SourceInfo:
    name: str
    enabled: bool
    formal: bool


class SourceRegistry:
    def __init__(self, settings: Settings) -> None:
        enabled = set(settings.enabled_sources)
        self._sources: list[VideoSource] = []
        if "bilibili" in enabled:
            self._sources.append(
                BilibiliSource(
                    cookies_file=settings.bilibili_cookies_file,
                    sub_langs=settings.bilibili_sub_langs,
                )
            )
        if "youtube" in enabled:
            self._sources.append(
                YouTubeSource(
                    cookies_file=settings.youtube_cookies_file,
                    sub_langs=settings.youtube_sub_langs,
                )
            )
        # 本地源只在 local 环境启用，作为 Demo / 测试兜底
        self._local_enabled = settings.app_env == "local"
        if self._local_enabled:
            self._sources.append(LocalSource())
        self._enabled_names = enabled

    def get(self, url: str) -> VideoSource:
        for source in self._sources:
            if source.match(url):
                return source
        raise UnsupportedSourceError(
            f"不支持的视频来源：{url}。当前仅支持 B站、YouTube"
            + (" 与本地文件" if self._local_enabled else "")
        )

    async def normalize(self, url: str) -> VideoRef:
        return await self.get(url).normalize(url)

    def describe(self) -> list[dict]:
        return [
            {"name": "bilibili", "enabled": "bilibili" in self._enabled_names},
            {"name": "youtube", "enabled": "youtube" in self._enabled_names},
            {"name": "local", "enabled": self._local_enabled},
        ]
