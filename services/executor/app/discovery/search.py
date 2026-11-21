"""视频搜索 provider。MVP 先支持 B站，YouTube 保留适配器。"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Protocol

from app.discovery.models import SearchHit

AV_RE = re.compile(r"/video/(av\d+)", re.IGNORECASE)
BV_RE = re.compile(r"/video/(BV[0-9A-Za-z]{10})")


class SearchError(RuntimeError):
    code = "SEARCH_FAILED"


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int = 20) -> list[SearchHit]: ...


class BilibiliSearchProvider:
    name = "bilibili"

    def __init__(
        self,
        *,
        cookies_file: str | None = None,
        sleep_requests: int = 1,
        proxy: str | None = None,
        retries: int = 3,
        backoff_seconds: float = 4.0,
        cooldown_seconds: float = 15.0,
    ) -> None:
        self.cookies_file = cookies_file
        self.sleep_requests = sleep_requests
        self.proxy = proxy
        self.retries = max(retries, 1)
        self.backoff_seconds = max(backoff_seconds, 0.5)
        self.cooldown_seconds = max(cooldown_seconds, 0.0)
        self._cooldown_until = 0.0

    async def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        try:
            return await asyncio.to_thread(self._search_sync, query, limit)
        except SearchError:
            raise
        except Exception as exc:
            raise SearchError(f"B站搜索失败：{exc}") from exc

    def _search_sync(self, query: str, limit: int) -> list[SearchHit]:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError

        wait_for = self._cooldown_until - time.monotonic()
        if wait_for > 0:
            time.sleep(wait_for)
        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "socket_timeout": 30,
            "sleep_interval_requests": self.sleep_requests,
            "playlistend": limit,
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        if self.proxy:
            opts["proxy"] = self.proxy
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"bilisearch{limit}:{query}", download=False)
                break
            except DownloadError as exc:
                last_error = exc
                message = str(exc)
                retryable = any(
                    token in message
                    for token in ("412", "429", "timed out", "Connection", "Remote end")
                )
                if not retryable or attempt + 1 >= self.retries:
                    if retryable and self.cooldown_seconds:
                        self._cooldown_until = time.monotonic() + self.cooldown_seconds
                    raise SearchError(f"B站搜索被拒绝或网络失败：{exc}") from exc
                delay = self.backoff_seconds * (2**attempt)
                time.sleep(delay)
        else:
            raise SearchError(f"B站搜索失败：{last_error}")

        hits: list[SearchHit] = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            url = entry.get("url") or entry.get("webpage_url") or ""
            if "/video/" not in url:
                continue
            av_match = AV_RE.search(url)
            bv_match = BV_RE.search(url)
            video_id = (
                bv_match.group(1)
                if bv_match
                else av_match.group(1)
                if av_match
                else str(entry.get("id") or "")
            )
            if not video_id:
                continue
            hits.append(
                SearchHit(
                    source="bilibili",
                    source_video_id=video_id,
                    url=url,
                    title=str(entry.get("title") or ""),
                    metrics={"search_id": entry.get("id")},
                )
            )
        return hits


class YouTubeSearchProvider:
    name = "youtube"

    def __init__(self, *, cookies_file: str | None = None, proxy: str | None = None) -> None:
        self.cookies_file = cookies_file
        self.proxy = proxy

    async def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        try:
            return await asyncio.to_thread(self._search_sync, query, limit)
        except Exception as exc:
            raise SearchError(f"YouTube 搜索失败：{exc}") from exc

    def _search_sync(self, query: str, limit: int) -> list[SearchHit]:
        from yt_dlp import YoutubeDL

        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "socket_timeout": 30,
            "playlistend": limit,
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        if self.proxy:
            opts["proxy"] = self.proxy
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        hits = []
        for entry in info.get("entries") or []:
            if not entry or not entry.get("id"):
                continue
            hits.append(
                SearchHit(
                    source="youtube",
                    source_video_id=str(entry["id"]),
                    url=f"https://www.youtube.com/watch?v={entry['id']}",
                    title=str(entry.get("title") or ""),
                    duration_ms=int(entry["duration"] * 1000) if entry.get("duration") else None,
                    uploader=entry.get("uploader"),
                    views=entry.get("view_count"),
                )
            )
        return hits


class MockSearchProvider:
    name = "mock"

    def __init__(self, hits: list[SearchHit] | None = None) -> None:
        self.hits = hits or []

    async def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        return self.hits[:limit]
