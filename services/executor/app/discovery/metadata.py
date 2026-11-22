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

    async def fetch_video(self, video_id: str) -> dict | None:
        """按 BV / av 号取单个视频的完整元数据（单视频研报头部用）。"""
        params: dict[str, str] = {}
        lowered = video_id.strip().lower()
        if lowered.startswith("bv"):
            params["bvid"] = video_id.strip()
        elif lowered.startswith("av"):
            params["aid"] = video_id.strip()[2:]
        elif video_id.strip().isdigit():
            params["aid"] = video_id.strip()
        else:
            return None
        async with self.semaphore:
            try:
                response = await self.client.get(VIEW_API, params=params)
                payload = response.json()
            except Exception:
                return None
        if payload.get("code") != 0:
            return None
        return video_meta(payload.get("data") or {})

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


def video_meta(data: dict) -> dict:
    """把 B 站 view 接口的 data 归一化为研报头部字段。"""
    stat = data.get("stat") or {}
    owner = data.get("owner") or {}
    bvid = data.get("bvid") or ""
    return {
        "source": "bilibili",
        "source_video_id": bvid,
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "aid": data.get("aid"),
        "title": data.get("title") or "",
        "duration_ms": int(data.get("duration") or 0) * 1000 or None,
        "uploader": owner.get("name"),
        "owner_mid": owner.get("mid"),
        "published_at": data.get("pubdate"),
        "views": int(stat.get("view") or 0),
        "likes": int(stat.get("like") or 0),
        "coins": int(stat.get("coin") or 0),
        "favorites": int(stat.get("favorite") or 0),
        "danmaku": stat.get("danmaku"),
        "reply": stat.get("reply"),
        "share": stat.get("share"),
        "description": data.get("desc") or "",
        "cover": data.get("pic") or None,
        "pages": len(data.get("pages") or []) or None,
    }


def _aid_from(video_id: str, url: str) -> str | None:
    if video_id.lower().startswith("av"):
        return video_id[2:]
    if video_id.isdigit():
        return video_id
    match = re.search(r"/video/av(\d+)", url)
    return match.group(1) if match else None
