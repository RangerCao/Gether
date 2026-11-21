"""ffmpeg 定位：优先系统安装，否则使用 imageio-ffmpeg 自带二进制。"""

from __future__ import annotations

import shutil


class FfmpegNotFound(RuntimeError):
    pass


def find_ffmpeg() -> str:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise FfmpegNotFound(
            "未找到 ffmpeg，请安装 ffmpeg 或执行：pip install imageio-ffmpeg"
        ) from exc
