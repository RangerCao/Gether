"""B站视频元数据补全。"""

from __future__ import annotations

import asyncio
import re
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import httpx

from app.discovery.models import SearchHit

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def load_netscape_cookies(path: str | None) -> dict[str, str]:
    if not path or not Path(path).is_file():
        return {}
    jar = MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        return {}
    return {cookie.name: cookie.value for cookie in jar}


class BilibiliMetadataClient:
    def __init__(self, *, cookies_file: str | None = None, concurrency: int = 4) -> None:
        self.cookies = load_netscape_cookies(cookies_file)
        self.semaphore = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
            cookies=self.cookies,
            timeout=httpx.Timeout(20.0),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def fetch_many(self, hits: list[SearchHit]) -> list[SearchHit]:
        results = await asyncio.gather(*(self.fetch(hit) for hit in hits), return_exceptions=True)
        return [item for item in results if isinstance(item, SearchHit)]

    async def fetch(self, hit: SearchHit) -> SearchHit | None:
        aid = _aid_from(hit.source_video_id, hit.url)
        if not aid:
            return None
        async with self.semaphore:
            try:
                response = await self.client.get(VIEW_API, params={"aid": aid})
                payload = response.json()
            except Exception:
                return None
        if payload.get("code") != 0:
            return None
        data = payload.get("data") or {}
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        bvid = data.get("bvid") or hit.source_video_id
        return SearchHit(
            source="bilibili",
            source_video_id=bvid,
            url=f"https://www.bilibili.com/video/{bvid}",
            title=data.get("title") or hit.title,
            duration_ms=int(data.get("duration", 0) * 1000) if data.get("duration") else None,
            uploader=owner.get("name"),
            published_at=data.get("pubdate"),
            views=int(stat.get("view") or 0),
            likes=int(stat.get("like") or 0),
            coins=int(stat.get("coin") or 0),
            favorites=int(stat.get("favorite") or 0),
            description=data.get("desc") or "",
            metrics={
                "aid": data.get("aid"),
                "bvid": bvid,
                "danmaku": stat.get("danmaku"),
                "reply": stat.get("reply"),
                "share": stat.get("share"),
                "owner_mid": owner.get("mid"),
            },
        )


def _aid_from(video_id: str, url: str) -> str | None:
    if video_id.lower().startswith("av"):
        return video_id[2:]
    if video_id.isdigit():
        return video_id
    match = re.search(r"/video/av(\d+)", url)
    return match.group(1) if match else None
