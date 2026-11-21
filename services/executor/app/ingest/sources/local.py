"""本地视频源：仅用于 Demo 与测试 fixture，不作为正式输入。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.contracts import VideoRef, VideoSourceName
from app.ingest.sources.base import InvalidVideoUrlError

LOCAL_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


class LocalSource:
    name = "local"

    def match(self, url: str) -> bool:
        raw = url.strip()
        if raw.startswith("file://"):
            return True
        return Path(raw).suffix.lower() in LOCAL_SUFFIXES

    async def normalize(self, url: str) -> VideoRef:
        raw = url.strip()
        path = Path(raw[7:] if raw.startswith("file://") else raw)
        if path.suffix.lower() not in LOCAL_SUFFIXES:
            raise InvalidVideoUrlError(f"不支持的本地视频格式：{url}")
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        return VideoRef(
            source=VideoSourceName.LOCAL,
            source_video_id=f"{path.stem}-{digest}",
            page=1,
            url=raw,
            title=path.name,
        )
