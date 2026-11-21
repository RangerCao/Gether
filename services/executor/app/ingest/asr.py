"""ASR 适配器：默认 faster-whisper，可选云端实现。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

from app.core.config import Settings
from app.core.cuda import enable_cuda_dlls
from app.ingest.subtitles import SubtitleCue, dedupe_cues


class ASRUnavailableError(RuntimeError):
    code = "ASR_FAILED"


logger = logging.getLogger(__name__)


class ASRProvider(Protocol):
    async def transcribe(self, audio_path: Path, language: str | None) -> list[SubtitleCue]: ...


class FasterWhisperProvider:
    def __init__(
        self,
        *,
        model: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        beam_size: int = 5,
        vad: bool = True,
        word_timestamps: bool = True,
        initial_prompt: str | None = None,
        hotwords: str | None = None,
        condition_on_previous_text: bool = False,
        vad_min_silence_ms: int = 500,
        vad_speech_pad_ms: int = 200,
        concurrency: int = 1,
        batch_size: int = 1,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        if self.device == "cpu" and "float16" in self.compute_type:
            # CPU 不支持 float16 / int8_float16，自动降为 int8
            self.compute_type = "int8"
        self.beam_size = beam_size
        self.vad = vad
        self.word_timestamps = word_timestamps
        self.initial_prompt = initial_prompt
        self.hotwords = hotwords
        self.condition_on_previous_text = condition_on_previous_text
        self.vad_min_silence_ms = vad_min_silence_ms
        self.vad_speech_pad_ms = vad_speech_pad_ms
        self._model = None
        self._batch_pipeline = None
        self.batch_size = max(batch_size, 1)
        self._semaphore = asyncio.Semaphore(max(concurrency, 1))

    def _load(self):
        if self._model is not None:
            return self._model
        if self.device in {"auto", "cuda"}:
            enable_cuda_dlls()
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ASRUnavailableError(
                "未安装 faster-whisper，请执行：pip install -e '.[asr]'"
            ) from exc
        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )
        return self._model

    def _transcribe_sync(self, audio_path: Path, language: str | None) -> list[SubtitleCue]:
        try:
            return self._run_transcribe(audio_path, language)
        except RuntimeError as exc:
            # Windows / 无 CUDA 运行时常见：cublas/cudnn 缺失，自动退回 CPU
            message = str(exc).lower()
            if self.device != "cpu" and any(
                token in message for token in ("cublas", "cudnn", "cuda", "gpu")
            ):
                self.device = "cpu"
                self.compute_type = "int8"
                self._model = None
                return self._run_transcribe(audio_path, language)
            raise

    @staticmethod
    def _segments_to_cues(segments) -> list[SubtitleCue]:
        cues: list[SubtitleCue] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            start_ms = int(segment.start * 1000)
            end_ms = int(segment.end * 1000)
            if end_ms > start_ms:
                cues.append(SubtitleCue(start_ms, end_ms, text))
        return dedupe_cues(cues)

    def _run_transcribe(self, audio_path: Path, language: str | None) -> list[SubtitleCue]:
        model = self._load()
        common = {
            "language": language,
            "beam_size": self.beam_size,
            "vad_filter": self.vad,
            "condition_on_previous_text": self.condition_on_previous_text,
            "initial_prompt": self.initial_prompt,
            "hotwords": self.hotwords or None,
            "vad_parameters": {
                "min_silence_duration_ms": self.vad_min_silence_ms,
                "speech_pad_ms": self.vad_speech_pad_ms,
            },
        }
        # 快速模式使用 BatchedInferencePipeline：GPU 上可显著提升长音频吞吐。
        if self.batch_size > 1 and not self.word_timestamps:
            try:
                if self._batch_pipeline is None:
                    from faster_whisper import BatchedInferencePipeline

                    self._batch_pipeline = BatchedInferencePipeline(model=model)
                segments, _info = self._batch_pipeline.transcribe(
                    str(audio_path),
                    batch_size=self.batch_size,
                    without_timestamps=False,
                    **common,
                )
                return self._segments_to_cues(segments)
            except Exception as exc:
                logger.warning(
                    "batched_asr_failed fallback_to_normal batch_size=%s error=%s",
                    self.batch_size,
                    exc,
                )
        segments, _info = model.transcribe(
            str(audio_path),
            word_timestamps=self.word_timestamps,
            **common,
        )
        return self._segments_to_cues(segments)

    async def transcribe(self, audio_path: Path, language: str | None) -> list[SubtitleCue]:
        try:
            async with self._semaphore:
                return await asyncio.to_thread(
                    self._transcribe_sync, audio_path, language
                )
        except ASRUnavailableError:
            raise
        except Exception as exc:
            raise ASRUnavailableError(f"语音转写失败：{exc}") from exc


def build_asr(
    settings: Settings,
    *,
    model: str | None = None,
    beam_size: int | None = None,
    word_timestamps: bool | None = None,
    concurrency: int = 1,
    batch_size: int = 1,
) -> ASRProvider | None:
    if settings.asr_provider == "faster_whisper":
        return FasterWhisperProvider(
            model=model or settings.asr_model,
            device=settings.asr_device,
            compute_type=settings.asr_compute_type,
            beam_size=settings.asr_beam_size if beam_size is None else beam_size,
            vad=settings.asr_vad,
            word_timestamps=settings.asr_word_timestamps if word_timestamps is None else word_timestamps,
            initial_prompt=settings.asr_initial_prompt,
            hotwords=settings.asr_hotwords,
            condition_on_previous_text=settings.asr_condition_on_previous_text,
            vad_min_silence_ms=settings.asr_vad_min_silence_ms,
            vad_speech_pad_ms=settings.asr_vad_speech_pad_ms,
            concurrency=concurrency,
            batch_size=batch_size,
        )
    if settings.asr_provider in {"none", "disabled"}:
        return None
    raise ASRUnavailableError(f"暂不支持的 ASR_PROVIDER：{settings.asr_provider}")
