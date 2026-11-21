"""关键帧抽取：ffmpeg 场景检测 / 固定间隔。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.ffmpeg import FfmpegNotFound, find_ffmpeg

PTS_RE = re.compile(r"pts_time:(?P<pts>\d+(?:\.\d+)?)")


class FrameExtractionError(RuntimeError):
    code = "FRAME_EXTRACTION_FAILED"


@dataclass(frozen=True)
class Frame:
    path: Path
    start_ms: int
    end_ms: int


class FrameExtractor(Protocol):
    async def extract(
        self,
        video_path: Path,
        output_dir: Path,
        *,
        mode: str = "scene",
        scene_threshold: float = 0.3,
        interval_seconds: int = 5,
        max_frames: int = 300,
    ) -> list[Frame]: ...


class FfmpegFrameExtractor:
    async def extract(
        self,
        video_path: Path,
        output_dir: Path,
        *,
        mode: str = "scene",
        scene_threshold: float = 0.3,
        interval_seconds: int = 5,
        max_frames: int = 300,
    ) -> list[Frame]:
        if not self._ffmpeg_available():
            raise FrameExtractionError("未找到 ffmpeg，请先安装或 pip install imageio-ffmpeg")
        output_dir.mkdir(parents=True, exist_ok=True)
        for old in output_dir.glob("frame_*.jpg"):
            old.unlink(missing_ok=True)

        if mode == "interval":
            frames = await asyncio.to_thread(
                self._interval,
                video_path,
                output_dir,
                interval_seconds,
                max_frames,
            )
        else:
            try:
                frames = await asyncio.to_thread(
                    self._scene,
                    video_path,
                    output_dir,
                    scene_threshold,
                    max_frames,
                )
            except FrameExtractionError:
                # 无场景切换/短视频可能一帧都选不出来，降级到固定间隔
                frames = []
            if not frames:
                frames = await asyncio.to_thread(
                    self._interval,
                    video_path,
                    output_dir,
                    interval_seconds,
                    max_frames,
                )
        return frames

    def _interval(
        self,
        video_path: Path,
        output_dir: Path,
        interval_seconds: int,
        max_frames: int,
    ) -> list[Frame]:
        pattern = output_dir / "frame_%06d.jpg"
        cmd = [
            find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{max(interval_seconds, 1)},scale=1280:-2",
            "-frames:v",
            str(max_frames),
            str(pattern),
        ]
        self._run(cmd)
        files = sorted(output_dir.glob("frame_*.jpg"))
        step_ms = max(interval_seconds, 1) * 1000
        return [
            Frame(path=file, start_ms=index * step_ms, end_ms=(index + 1) * step_ms)
            for index, file in enumerate(files)
        ]

    def _scene(
        self,
        video_path: Path,
        output_dir: Path,
        threshold: float,
        max_frames: int,
    ) -> list[Frame]:
        pattern = output_dir / "frame_%06d.jpg"
        cmd = [
            find_ffmpeg(),
            "-hide_banner",
            "-i",
            str(video_path),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo,scale=1280:-2",
            "-fps_mode",
            "vfr",
            "-frames:v",
            str(max_frames),
            str(pattern),
        ]
        stderr = self._run(cmd)
        timestamps = [int(float(match) * 1000) for match in PTS_RE.findall(stderr)]
        files = sorted(output_dir.glob("frame_*.jpg"))
        frames: list[Frame] = []
        for index, file in enumerate(files):
            start_ms = timestamps[index] if index < len(timestamps) else index * 1000
            end_ms = (
                timestamps[index + 1]
                if index + 1 < len(timestamps)
                else start_ms + 1000
            )
            frames.append(Frame(path=file, start_ms=start_ms, end_ms=max(end_ms, start_ms + 1)))
        return frames

    @staticmethod
    def _ffmpeg_available() -> bool:
        try:
            find_ffmpeg()
            return True
        except FfmpegNotFound:
            return False

    @staticmethod
    def _run(cmd: list[str]) -> str:
        import subprocess

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise FrameExtractionError(completed.stderr.strip()[-500:] or "ffmpeg 执行失败")
        return completed.stderr
