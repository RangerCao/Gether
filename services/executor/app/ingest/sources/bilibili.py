"""Bilibili 源适配器：BV / av / ep / 多P / b23.tv 短链。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx

from app.contracts import VideoRef, VideoSourceName
from app.ingest.sources.base import InvalidVideoUrlError

BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"/video/av(\d+)", re.IGNORECASE)
EP_RE = re.compile(r"/bangumi/play/ep(\d+)", re.IGNORECASE)
SHORT_RE = re.compile(r"^https?://b23\.tv/([0-9A-Za-z]+)", re.IGNORECASE)
BARE_RE = re.compile(r"^(?:BV[0-9A-Za-z]{10}|av\d+|ep\d+)$", re.IGNORECASE)

BILIBILI_HOSTS = {
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "b23.tv",
    "www.b23.tv",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class BilibiliSource:
    name = "bilibili"

    def __init__(self, cookies_file: str | None = None, sub_langs: str = "zh-Hans,zh-CN,zh") -> None:
        self.cookies_file = cookies_file
        self.sub_langs = sub_langs

    def match(self, url: str) -> bool:
        raw = url.strip()
        if BARE_RE.fullmatch(raw):
            return True
        try:
            host = (urlparse(raw).hostname or "").lower()
        except ValueError:
            return False
        return host in BILIBILI_HOSTS

    async def normalize(self, url: str) -> VideoRef:
        raw = url.strip()
        if not self.match(raw):
            raise InvalidVideoUrlError(f"不是有效的 Bilibili 链接：{url}")

        if BARE_RE.fullmatch(raw):
            if raw.lower().startswith("ep"):
                raw = f"https://www.bilibili.com/bangumi/play/{raw}"
            else:
                raw = f"https://www.bilibili.com/video/{raw}"

        if SHORT_RE.match(raw):
            raw = await self._resolve_short(raw)

        parsed = urlparse(raw)
        page = self._parse_page(parsed.query)

        bvid = BVID_RE.search(parsed.path)
        if bvid:
            video_id = bvid.group(1)
            canonical = f"https://www.bilibili.com/video/{video_id}"
            if page > 1:
                canonical = f"{canonical}?p={page}"
            return VideoRef(
                source=VideoSourceName.BILIBILI,
                source_video_id=video_id,
                page=page,
                url=canonical,
            )

        av = AV_RE.search(parsed.path)
        if av:
            video_id = f"av{av.group(1)}"
            canonical = f"https://www.bilibili.com/video/{video_id}"
            if page > 1:
                canonical = f"{canonical}?p={page}"
            return VideoRef(
                source=VideoSourceName.BILIBILI,
                source_video_id=video_id,
                page=page,
                url=canonical,
            )

        ep = EP_RE.search(parsed.path)
        if ep:
            video_id = f"ep{ep.group(1)}"
            return VideoRef(
                source=VideoSourceName.BILIBILI,
                source_video_id=video_id,
                page=1,
                url=f"https://www.bilibili.com/bangumi/play/{video_id}",
            )

        raise InvalidVideoUrlError(f"无法从链接中解析视频 ID：{url}")

    @staticmethod
    def _parse_page(query: str) -> int:
        values = parse_qs(query).get("p", ["1"])
        try:
            page = int(values[0])
        except (TypeError, ValueError):
            return 1
        return max(page, 1)

    async def _resolve_short(self, url: str) -> str:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(10.0),
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return str(response.url)
        except httpx.HTTPError as exc:
            raise InvalidVideoUrlError(f"b23 短链解析失败：{exc}") from exc
