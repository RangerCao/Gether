"""YouTube 源适配器：watch / youtu.be / shorts / embed / live。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from app.contracts import VideoRef, VideoSourceName
from app.ingest.sources.base import InvalidVideoUrlError

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PATH_PREFIXES = ("shorts", "embed", "live", "v")


class YouTubeSource:
    name = "youtube"

    def __init__(self, cookies_file: str | None = None, sub_langs: str = "zh-Hans,zh,en") -> None:
        self.cookies_file = cookies_file
        self.sub_langs = sub_langs

    def match(self, url: str) -> bool:
        try:
            host = (urlparse(url.strip()).hostname or "").lower()
        except ValueError:
            return False
        return host in YOUTUBE_HOSTS

    async def normalize(self, url: str) -> VideoRef:
        raw = url.strip()
        if not self.match(raw):
            raise InvalidVideoUrlError(f"不是有效的 YouTube 链接：{url}")

        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        video_id = ""

        if host.endswith("youtu.be"):
            video_id = parsed.path.lstrip("/").split("/")[0]
        else:
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if not video_id:
                parts = [part for part in parsed.path.split("/") if part]
                if len(parts) >= 2 and parts[0] in PATH_PREFIXES:
                    video_id = parts[1]

        if not VIDEO_ID_RE.match(video_id or ""):
            if "list" in parse_qs(parsed.query):
                raise InvalidVideoUrlError("不支持播放列表，请提交单个视频链接")
            raise InvalidVideoUrlError(f"无法从链接中解析视频 ID：{url}")

        return VideoRef(
            source=VideoSourceName.YOUTUBE,
            source_video_id=video_id,
            page=1,
            url=f"https://www.youtube.com/watch?v={video_id}",
        )
