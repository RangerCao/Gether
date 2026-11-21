"""解析流水线：字幕优先 → ASR 降级 → 关键帧 OCR → 证据包。"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.contracts import EvidencePacket, EvidenceSourceType, VideoRef
from app.core.config import Settings
from app.core.storage import ObjectStorage, StorageError
from app.ingest.asr import ASRProvider, ASRUnavailableError, build_asr
from app.ingest.correction import LLMCorrector
from app.ingest.downloader import DownloadError, YtDlpDownloader
from app.ingest.evidence import OCRBlock, build_evidence, build_ocr_evidence, normalize_text
from app.ingest.frames import FfmpegFrameExtractor, FrameExtractionError, FrameExtractor
from app.ingest.fusion import fuse_ocr_asr
from app.ingest.ocr import OCRProvider, OCRUnavailableError, build_ocr
from app.ingest.subtitles import parse_subtitle
from app.llm.base import LLMClient
from app.llm.factory import build_llm_client

ProgressCallback = Callable[[str, int, int, str], Any]
logger = logging.getLogger(__name__)


class IngestError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParseResult:
    ref: VideoRef
    evidence: list[EvidencePacket]
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


class IngestPipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        downloader: YtDlpDownloader | None = None,
        asr: ASRProvider | None | object = ...,
        ocr: OCRProvider | None | object = ...,
        frames: FrameExtractor | None | object = ...,
        storage: ObjectStorage | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings
        self.downloader = downloader or YtDlpDownloader(settings)
        self.asr = build_asr(settings) if asr is ... else asr
        self.asr_fast = (
            build_asr(
                settings,
                model=settings.asr_fast_model,
                beam_size=settings.asr_fast_beam_size,
                word_timestamps=settings.asr_fast_word_timestamps,
                concurrency=settings.asr_fast_concurrency,
                batch_size=settings.asr_fast_batch_size,
            )
            if asr is ...
            else self.asr
        )
        self.ocr = build_ocr(settings) if ocr is ... else ocr
        self.frames = FfmpegFrameExtractor() if frames is ... else frames
        self.storage = storage
        self._own_llm: LLMClient | None = None
        self.corrector: LLMCorrector | None = None
        if settings.asr_correction_enabled:
            if llm is None:
                llm = build_llm_client(settings)
                self._own_llm = llm
            self.corrector = LLMCorrector(
                llm,
                batch_size=settings.asr_correction_batch_size,
                max_chars=settings.asr_correction_max_chars,
                hotwords=settings.asr_hotwords,
                min_similarity=settings.asr_correction_min_similarity,
            )

    async def parse_video(
        self,
        *,
        task_id: str,
        video_id: str,
        ref: VideoRef,
        progress: ProgressCallback | None = None,
        mode: str = "quality",
    ) -> ParseResult:
        warnings: list[str] = []
        evidence: list[EvidencePacket] = []
        stats: dict[str, Any] = {}
        asr = self.asr_fast if mode == "fast" and self.asr_fast is not None else self.asr
        corrector = None if mode == "fast" else self.corrector

        async def emit(stage: str, current: int, total: int, message: str) -> None:
            if progress is not None:
                result = progress(stage, current, total, message)
                if inspect.isawaitable(result):
                    await result

        # 1) 字幕优先
        if self.settings.prefer_native_subtitle:
            await emit("subtitle", 0, 1, "尝试获取原生字幕")
            try:
                subtitle = await self.downloader.download_subtitles(
                    ref, self._subtitle_langs(ref)
                )
            except DownloadError as exc:
                subtitle = None
                warnings.append(str(exc))
                logger.warning("subtitle_failed ref=%s: %s", ref.source_video_id, exc)
            if subtitle is not None:
                cues = parse_subtitle(subtitle.path)
                if cues:
                    evidence.extend(
                        build_evidence(
                            cues,
                            task_id=task_id,
                            video_id=video_id,
                            ref=ref,
                            source_type=EvidenceSourceType.NATIVE_SUB,
                        )
                    )
                    stats["subtitle_language"] = subtitle.language
                    stats["subtitle_cues"] = len(cues)
            await emit("subtitle", 1, 1, f"字幕证据 {len(evidence)} 条")

        # 2) ASR 降级
        if not evidence:
            if asr is None:
                warnings.append("无字幕且 ASR 未启用")
            else:
                await emit("asr", 0, 1, "无可用字幕，开始语音转写")
                try:
                    audio = await self.downloader.download_audio(ref)
                    cues = await asr.transcribe(audio, self.settings.asr_language)
                    if corrector is not None and cues:
                        cues, correction_stats = await corrector.correct(cues)
                        stats.update(correction_stats.as_dict())
                    evidence.extend(
                        build_evidence(
                            cues,
                            task_id=task_id,
                            video_id=video_id,
                            ref=ref,
                            source_type=EvidenceSourceType.WHISPER,
                        )
                    )
                    stats["asr_cues"] = len(cues)
                except (DownloadError, ASRUnavailableError) as exc:
                    warnings.append(str(exc))
                    logger.warning("asr_failed ref=%s: %s", ref.source_video_id, exc)
                await emit("asr", 1, 1, f"转写证据 {len(evidence)} 条")

        # 3) 视觉轨 OCR
        if self.ocr is not None and self.frames is not None:
            try:
                await emit("ocr", 0, 1, "抽取关键帧")
                video = await self.downloader.download_video(
                    ref, self._max_height(ref)
                )
                frames = await self.frames.extract(
                    video,
                    video.parent / "frames",
                    mode=self.settings.frame_extract_mode,
                    scene_threshold=self.settings.frame_scene_threshold,
                    interval_seconds=self.settings.frame_interval_seconds,
                    max_frames=self.settings.frame_max_per_video,
                )
                blocks: list[OCRBlock] = []
                total = max(len(frames), 1)
                upload_warned = False
                for index, frame in enumerate(frames, start=1):
                    texts = await self.ocr.recognize(frame.path)
                    text = normalize_text(" ".join(item.text for item in texts))
                    if text:
                        frame_ref = str(frame.path)
                        if self.storage is not None and not upload_warned:
                            try:
                                frame_ref = await self.storage.upload_file(
                                    frame.path,
                                    f"tasks/{task_id}/frames/{video_id}/{frame.path.name}",
                                )
                            except StorageError as exc:
                                warnings.append(f"帧上传失败，保留本地路径：{exc}")
                                upload_warned = True
                        blocks.append(
                            OCRBlock(
                                start_ms=frame.start_ms,
                                end_ms=frame.end_ms,
                                text=text,
                                confidence=max((item.confidence for item in texts), default=1.0),
                                frame_ref=frame_ref,
                            )
                        )
                    await emit("ocr", index, total, f"OCR {index}/{total}")
                evidence.extend(
                    build_ocr_evidence(
                        blocks,
                        task_id=task_id,
                        video_id=video_id,
                        ref=ref,
                        max_gap_ms=self.settings.ocr_merge_gap_ms,
                    )
                )
                stats["ocr_frames"] = len(frames)
                stats["ocr_blocks"] = len(blocks)
            except (DownloadError, FrameExtractionError, OCRUnavailableError) as exc:
                warnings.append(f"视觉轨降级：{exc}")
                logger.warning("ocr_failed ref=%s: %s", ref.source_video_id, exc)

        # 跨模态融合：高置信 OCR 字幕替代重复的 ASR 切片
        if self.settings.ocr_asr_fusion_enabled:
            evidence, fusion_stats = fuse_ocr_asr(
                evidence,
                min_similarity=self.settings.ocr_asr_fusion_min_similarity,
                min_confidence=self.settings.ocr_asr_fusion_min_confidence,
            )
            stats.update(fusion_stats.as_dict())

        # 跨模态去重
        unique: dict[str, EvidencePacket] = {}
        for item in evidence:
            unique.setdefault(item.content_hash, item)
        result = sorted(unique.values(), key=lambda item: item.start_ms)
        if not result:
            raise IngestError("NO_EVIDENCE", "未能从该视频获取任何证据")
        stats["evidence_count"] = len(result)
        return ParseResult(ref=ref, evidence=result, warnings=warnings, stats=stats)

    async def aclose(self) -> None:
        if self._own_llm is not None:
            await self._own_llm.aclose()
            self._own_llm = None

    def _subtitle_langs(self, ref: VideoRef) -> list[str]:
        raw = (
            self.settings.bilibili_sub_langs
            if ref.source.value == "bilibili"
            else self.settings.youtube_sub_langs
        )
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _max_height(self, ref: VideoRef) -> int:
        return (
            self.settings.bilibili_max_height
            if ref.source.value == "bilibili"
            else self.settings.youtube_max_height
        )
