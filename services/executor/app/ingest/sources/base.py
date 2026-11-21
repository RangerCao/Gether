"""视频源适配器契约。"""

from __future__ import annotations

from typing import Protocol

from app.contracts import VideoRef


class SourceError(RuntimeError):
    code = "SOURCE_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedSourceError(SourceError):
    code = "INVALID_URL"


class InvalidVideoUrlError(SourceError):
    code = "INVALID_URL"


class VideoTooLongError(SourceError):
    code = "VIDEO_TOO_LONG"


class VideoSource(Protocol):
    name: str

    def match(self, url: str) -> bool: ...

    async def normalize(self, url: str) -> VideoRef: ...
