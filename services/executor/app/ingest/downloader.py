"""yt-dlp 下载适配器：字幕优先，音频/视频按需下载。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import httpx

from app.contracts import VideoRef
from app.core.config import PROJECT_ROOT, Settings
from app.core.ffmpeg import FfmpegNotFound, find_ffmpeg

logger = logging.getLogger(__name__)


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _parse_rate_limit(value: str) -> int | None:
    raw = (value or "").strip().upper()
    if not raw:
        return None
    units = {"K": 1024, "M": 1024**2, "G": 1024**3}
    if raw[-1] in units:
        try:
            return int(float(raw[:-1]) * units[raw[-1]])
        except ValueError:
            return None
    try:
        return int(raw)
    except ValueError:
        return None



class DownloadError(RuntimeError):
    code = "DOWNLOAD_FAILED"


@dataclass(frozen=True)
class SubtitleDownload:
    path: Path
    language: str
    is_auto: bool = False


class YtDlpDownloader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_root = _resolve_path(settings.data_dir)

    def _cookie_file(self, ref: VideoRef) -> str | None:
        candidates = []
        if ref.source.value == "bilibili":
            candidates.append(self.settings.bilibili_cookies_file)
        elif ref.source.value == "youtube":
            candidates.append(self.settings.youtube_cookies_file)
        candidates.append(self.settings.ytdlp_cookies_file or "")
        for candidate in candidates:
            if candidate and _resolve_path(candidate).is_file():
                return str(_resolve_path(candidate))
        return None

    def _base_opts(self, ref: VideoRef) -> dict:
        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "sleep_interval_requests": self.settings.ytdlp_sleep_requests,
            "retries": 3,
        }
        if self.settings.ytdlp_proxy:
            opts["proxy"] = self.settings.ytdlp_proxy
        with contextlib.suppress(FfmpegNotFound):
            opts["ffmpeg_location"] = find_ffmpeg()
        rate_limit = _parse_rate_limit(self.settings.ytdlp_rate_limit)
        if rate_limit:
            opts["ratelimit"] = rate_limit
        cookies = self._cookie_file(ref)
        if cookies:
            opts["cookiefile"] = cookies
        return opts

    def _output_dir(self, ref: VideoRef) -> Path:
        safe_id = ref.source_video_id.replace("/", "_")
        path = self.data_root / "raw" / ref.source.value / safe_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _run(self, opts: dict, url: str) -> None:
        def _download() -> None:
            from yt_dlp import YoutubeDL

            with YoutubeDL(opts) as ydl:
                ydl.download([url])

        try:
            await asyncio.to_thread(_download)
        except Exception as exc:  # yt-dlp 的异常类型很多，统一转换为可重试错误
            raise DownloadError(f"下载失败：{exc}") from exc

    async def _bilibili_subtitles(
        self, ref: VideoRef, langs: list[str]
    ) -> SubtitleDownload | None:
        """B站 player/v2 暴露原生/AI 字幕时直接取字幕，完全绕过 ASR。"""
        if ref.source.value != "bilibili":
            return None
        source_id = ref.source_video_id
        try:
            cookies: dict[str, str] = {}
            cookie_file = self._cookie_file(ref)
            if cookie_file:
                jar = MozillaCookieJar(cookie_file)
                jar.load(ignore_discard=True, ignore_expires=True)
                cookies = {cookie.name: cookie.value for cookie in jar}
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
                "Referer": f"https://www.bilibili.com/video/{source_id}",
            }
            async with httpx.AsyncClient(
                cookies=cookies,
                headers=headers,
                proxy=self.settings.ytdlp_proxy or None,
                follow_redirects=True,
                timeout=httpx.Timeout(20.0, connect=10.0),
            ) as client:
                params = {"bvid": source_id} if source_id.startswith("BV") else {"aid": source_id[2:]}
                view = await client.get(
                    "https://api.bilibili.com/x/web-interface/view", params=params
                )
                view.raise_for_status()
                data = view.json().get("data") or {}
                pages = data.get("pages") or []
                cid = data.get("cid")
                for page in pages:
                    if int(page.get("page") or 1) == ref.page:
                        cid = page.get("cid") or cid
                        break
                if not cid:
                    return None
                player = await client.get(
                    "https://api.bilibili.com/x/player/v2",
                    params={"bvid": data.get("bvid") or source_id, "cid": cid},
                )
                player.raise_for_status()
                subtitles = (
                    ((player.json().get("data") or {}).get("subtitle") or {})
                    .get("subtitles")
                    or []
                )
                selected = self._pick_bilibili_subtitle(subtitles, langs)
                if not selected:
                    return None
                url = str(selected.get("subtitle_url") or "")
                if url.startswith("//"):
                    url = "https:" + url
                if not url:
                    return None
                response = await client.get(url)
                response.raise_for_status()
                body = response.json().get("body") or []
                cues = [
                    (float(item.get("from", 0)), float(item.get("to", 0)), str(item.get("content") or ""))
                    for item in body
                    if item.get("content") and float(item.get("to", 0)) > float(item.get("from", 0))
                ]
                if not cues:
                    return None
                outdir = self._output_dir(ref)
                language = str(selected.get("lan") or "unknown")
                path = outdir / f"bilibili.{language}.vtt"
                path.write_text(self._cues_to_vtt(cues), encoding="utf-8")
                return SubtitleDownload(path=path, language=language)
        except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning("bilibili_direct_subtitle_failed video=%s: %s", source_id, exc)
            return None

    @staticmethod
    def _pick_bilibili_subtitle(
        subtitles: list[dict], langs: list[str]
    ) -> dict | None:
        by_lang = {
            str(item.get("lan") or "").lower(): item
            for item in subtitles
            if isinstance(item, dict)
        }
        for lang in langs:
            key = lang.lower().replace("_", "-")
            if key in by_lang:
                return by_lang[key]
        # 请求中文但接口返回 zh-Hans/zh-CN/ai-zh 等变体时，优先选中文。
        for key, item in by_lang.items():
            if key.startswith("zh") or key.startswith("ai-zh"):
                return item
        return None

    @staticmethod
    def _cues_to_vtt(cues: list[tuple[float, float, str]]) -> str:
        def stamp(seconds: float) -> str:
            millis = int(round(seconds * 1000))
            hours, rem = divmod(millis, 3_600_000)
            minutes, rem = divmod(rem, 60_000)
            seconds, millis = divmod(rem, 1000)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

        lines = ["WEBVTT", ""]
        for start, end, text in cues:
            lines.append(f"{stamp(start)} --> {stamp(end)}")
            lines.append(text.replace("\n", " ").strip())
            lines.append("")
        return "\n".join(lines)

    async def download_subtitles(
        self, ref: VideoRef, langs: list[str]
    ) -> SubtitleDownload | None:
        if ref.source.value == "local":
            return self._local_subtitle(ref, langs)

        outdir = self._output_dir(ref)
        existing = self._find_subtitle(outdir, langs)
        if existing is not None:
            return existing
        direct = await self._bilibili_subtitles(ref, langs)
        if direct is not None:
            return direct
        opts = self._base_opts(ref)
        opts.update(
            {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": langs,
                "subtitlesformat": "vtt/srt/best",
                "outtmpl": str(outdir / "%(id)s.%(ext)s"),
            }
        )
        await self._run(opts, ref.url)

        found = self._find_subtitle(outdir, langs)
        if found is not None:
            return found
        return None

    async def _bilibili_audio(self, ref: VideoRef, outdir: Path) -> Path | None:
        """用 B站 playurl 直接拿音频流，绕过 yt-dlp 的页面解析和合并。"""
        if ref.source.value != "bilibili":
            return None
        source_id = ref.source_video_id
        try:
            cookies: dict[str, str] = {}
            cookie_file = self._cookie_file(ref)
            if cookie_file:
                jar = MozillaCookieJar(cookie_file)
                jar.load(ignore_discard=True, ignore_expires=True)
                cookies = {cookie.name: cookie.value for cookie in jar}
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
                "Referer": f"https://www.bilibili.com/video/{source_id}",
            }
            async with httpx.AsyncClient(
                cookies=cookies,
                headers=headers,
                proxy=self.settings.ytdlp_proxy or None,
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, connect=10.0),
            ) as client:
                params = {"bvid": source_id} if source_id.startswith("BV") else {"aid": source_id[2:]}
                view = await client.get(
                    "https://api.bilibili.com/x/web-interface/view", params=params
                )
                view.raise_for_status()
                data = view.json().get("data") or {}
                pages = data.get("pages") or []
                cid = data.get("cid")
                for page in pages:
                    if int(page.get("page") or 1) == ref.page:
                        cid = page.get("cid") or cid
                        break
                if not cid:
                    return None
                play = await client.get(
                    "https://api.bilibili.com/x/player/playurl",
                    params={
                        "bvid": data.get("bvid") or source_id,
                        "cid": cid,
                        "fnval": 16,
                        "fnver": 0,
                        "fourk": 0,
                    },
                )
                play.raise_for_status()
                dash = (play.json().get("data") or {}).get("dash") or {}
                audios = dash.get("audio") or []
                if not audios:
                    return None
                # 30216 是低码率音频，转写足够，下载体积最小。
                audios = sorted(audios, key=lambda item: int(item.get("id") or 0))
                audio_url = audios[0].get("baseUrl") or audios[0].get("base_url")
                if not audio_url:
                    return None
                raw = outdir / "audio.m4s"
                part = outdir / "audio.m4s.part"
                async with client.stream("GET", audio_url, headers=headers) as response:
                    response.raise_for_status()
                    with part.open("wb") as file:
                        async for chunk in response.aiter_bytes():
                            file.write(chunk)
                part.replace(raw)
            wav = outdir / "audio.wav"
            ffmpeg = find_ffmpeg()
            process = await asyncio.create_subprocess_exec(
                str(ffmpeg), "-y", "-i", str(raw), "-vn", "-ac", "1", "-ar", "16000",
                str(wav),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            returncode = await process.wait()
            if returncode != 0 or not wav.is_file() or wav.stat().st_size == 0:
                return None
            return wav
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            logger.warning("bilibili_direct_audio_failed video=%s: %s", source_id, exc)
            return None

    async def download_audio(self, ref: VideoRef) -> Path:
        if ref.source.value == "local":
            return self._local_media(ref, "音频")
        outdir = self._output_dir(ref)
        cached = outdir / "audio.wav"
        if cached.is_file() and cached.stat().st_size > 0:
            return cached
        direct = await self._bilibili_audio(ref, outdir)
        if direct is not None:
            return direct
        opts = self._base_opts(ref)
        opts.update(
            {
                "format": "bestaudio/best",
                "outtmpl": str(outdir / "audio.%(ext)s"),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "wav",
                        "preferredquality": "192",
                    }
                ],
            }
        )
        await self._run(opts, ref.url)
        audio = next(iter(sorted(outdir.glob("audio.wav"))), None)
        if audio is None:
            raise DownloadError("音频下载完成但未找到 wav 文件（请确认已安装 ffmpeg）")
        return audio

    async def download_video(self, ref: VideoRef, max_height: int = 720) -> Path:
        if ref.source.value == "local":
            return self._local_media(ref, "视频")
        outdir = self._output_dir(ref)
        cached = outdir / "video.mp4"
        if cached.is_file() and cached.stat().st_size > 0:
            return cached
        opts = self._base_opts(ref)
        opts.update(
            {
                "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
                "merge_output_format": "mp4",
                "outtmpl": str(outdir / "video.%(ext)s"),
            }
        )
        await self._run(opts, ref.url)
        video = next(iter(sorted(outdir.glob("video.mp4"))), None)
        if video is None:
            raise DownloadError("视频下载完成但未找到 mp4 文件")
        return video

    @staticmethod
    def _find_subtitle(outdir: Path, langs: list[str]) -> SubtitleDownload | None:
        files = sorted(outdir.glob("*.vtt")) + sorted(outdir.glob("*.srt"))
        files = [file for file in files if file.stat().st_size > 0]
        if not files:
            return None
        for lang in langs:
            for file in files:
                if f".{lang}." in file.name:
                    return SubtitleDownload(path=file, language=lang)
        return SubtitleDownload(path=files[0], language="unknown")

    @staticmethod
    def _local_path(ref: VideoRef) -> Path:
        raw = ref.url[7:] if ref.url.startswith("file://") else ref.url
        # Windows: file:///D:/x -> /D:/x -> D:/x
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        return Path(raw)

    def _local_subtitle(self, ref: VideoRef, langs: list[str]) -> SubtitleDownload | None:
        media = self._local_path(ref)
        for lang in langs:
            for suffix in (".vtt", ".srt"):
                candidate = media.parent / f"{media.stem}.{lang}{suffix}"
                if candidate.is_file():
                    return SubtitleDownload(path=candidate, language=lang)
        for suffix in (".vtt", ".srt"):
            candidate = media.with_suffix(suffix)
            if candidate.is_file():
                return SubtitleDownload(path=candidate, language="unknown")
        return None

    def _local_media(self, ref: VideoRef, label: str) -> Path:
        media = self._local_path(ref)
        if not media.is_file():
            raise DownloadError(f"本地{label}不存在：{media}")
        return media
