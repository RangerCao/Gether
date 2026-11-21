"""B站 Web 搜索 API provider（优先于 yt-dlp）。

yt-dlp 的 bilisearch 走搜索页，容易触发 WAF 412；
官方 search/type 接口直接返回视频元数据，通常更稳定。
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import httpx

from app.discovery.models import SearchHit
from app.discovery.search import SearchError


class BilibiliApiSearchProvider:
    name = "bilibili"
    endpoint = "https://api.bilibili.com/x/web-interface/search/type"
    spi_endpoint = "https://api.bilibili.com/x/frontend/finger/spi"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )

    def __init__(
        self,
        *,
        cookies_file: str | None = None,
        proxy: str | None = None,
        retries: int = 3,
        backoff_seconds: float = 3.0,
        cooldown_seconds: float = 15.0,
        min_interval_seconds: float = 1.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.cookies_file = cookies_file
        self.proxy = proxy or None
        self.retries = max(retries, 1)
        self.backoff_seconds = max(backoff_seconds, 0.5)
        self.cooldown_seconds = max(cooldown_seconds, 0.0)
        self.min_interval_seconds = max(min_interval_seconds, 0.0)
        self.timeout_seconds = max(timeout_seconds, 2.0)
        self._last_request = 0.0
        self._cooldown_until = 0.0

    async def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        return await asyncio.to_thread(self._search_sync, query, limit)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": "https://search.bilibili.com/",
            "Origin": "https://search.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def _cookies(self) -> dict[str, str]:
        if not self.cookies_file or not Path(self.cookies_file).is_file():
            return {}
        jar = MozillaCookieJar(self.cookies_file)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            return {}
        return {cookie.name: cookie.value for cookie in jar}

    def _ensure_buvid(self, client: httpx.Client, cookies: dict[str, str]) -> None:
        if cookies.get("buvid3"):
            return
        try:
            response = client.get(self.spi_endpoint)
            data = response.json().get("data") or {}
            if data.get("b_3"):
                cookies["buvid3"] = str(data["b_3"])
            if data.get("b_4"):
                cookies["buvid4"] = str(data["b_4"])
        except Exception:
            return

    def _search_sync(self, query: str, limit: int) -> list[SearchHit]:
        now = time.monotonic()
        wait_for = max(self._cooldown_until - now, 0.0)
        wait_for = max(wait_for, self._last_request + self.min_interval_seconds - now)
        if wait_for > 0:
            time.sleep(wait_for)
        cookies = self._cookies()
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with httpx.Client(
                    cookies=cookies,
                    headers=self._headers(),
                    proxy=self.proxy or None,
                    follow_redirects=True,
                    timeout=httpx.Timeout(self.timeout_seconds),
                ) as client:
                    self._ensure_buvid(client, cookies)
                    response = client.get(
                        self.endpoint,
                        params={
                            "search_type": "video",
                            "keyword": query,
                            "page": 1,
                            "page_size": min(max(limit, 1), 50),
                        },
                    )
                self._last_request = time.monotonic()
                if response.status_code in {412, 429}:
                    self._cooldown_until = time.monotonic() + self.cooldown_seconds
                    last_error = SearchError(
                        f"B站搜索接口限流：HTTP {response.status_code}"
                    )
                    time.sleep(self.backoff_seconds * (2**attempt))
                    continue
                if response.status_code >= 400:
                    last_error = SearchError(
                        f"B站搜索接口失败：HTTP {response.status_code}"
                    )
                    time.sleep(self.backoff_seconds * (2**attempt))
                    continue
                payload = response.json()
                code = payload.get("code")
                if code in {-412, -429, 412, 429}:
                    self._cooldown_until = time.monotonic() + self.cooldown_seconds
                    last_error = SearchError(f"B站搜索接口限流：code={code}")
                    time.sleep(self.backoff_seconds * (2**attempt))
                    continue
                if code != 0:
                    raise SearchError(f"B站搜索接口返回错误：code={code}")
                results = payload.get("data", {}).get("result") or []
                return parse_api_results(results, limit)
            except SearchError:
                raise
            except Exception as exc:
                last_error = exc
                time.sleep(self.backoff_seconds * (2**attempt))
        raise SearchError(f"B站搜索接口失败：{last_error}")


def parse_api_results(results: list, limit: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        bvid = str(item.get("bvid") or "").strip()
        url = str(item.get("arcurl") or item.get("url") or "")
        if not bvid:
            match = re.search(r"/video/(BV[0-9A-Za-z]{10})", url)
            bvid = match.group(1) if match else ""
        if not bvid:
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", str(item.get("title") or ""))).strip()
        if not title:
            continue
        hits.append(
            SearchHit(
                source="bilibili",
                source_video_id=bvid,
                url=f"https://www.bilibili.com/video/{bvid}",
                title=title,
                duration_ms=duration_ms(item.get("duration")),
                uploader=str(item.get("author") or "").strip() or None,
                published_at=int(item["pubdate"]) if item.get("pubdate") else None,
                views=int(item["play"]) if item.get("play") is not None else None,
                likes=0,
                coins=0,
                favorites=int(item["favorites"]) if item.get("favorites") is not None else 0,
                description=str(item.get("description") or ""),
                metrics={
                    "aid": item.get("aid"),
                    "bilibili_api": True,
                    "tag": item.get("tag"),
                    "search_id": item.get("search_id"),
                },
            )
        )
        if len(hits) >= limit:
            break
    return hits


def duration_ms(value) -> int | None:
    if not value:
        return None
    parts = str(value).split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        minutes, seconds = numbers
        return (minutes * 60 + seconds) * 1000
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
        return (hours * 3600 + minutes * 60 + seconds) * 1000
    return None
