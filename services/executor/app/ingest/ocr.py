"""OCR 适配器：默认 PaddleOCR，可替换为其他实现。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import Settings


class OCRUnavailableError(RuntimeError):
    code = "OCR_FAILED"


@dataclass(frozen=True)
class OCRText:
    text: str
    confidence: float


class OCRProvider(Protocol):
    async def recognize(self, image_path: Path) -> list[OCRText]: ...


class PaddleOCRProvider:
    def __init__(self, *, lang: str = "ch", min_confidence: float = 0.6) -> None:
        self.lang = lang
        self.min_confidence = min_confidence
        self._engine = None

    def _load(self):
        if self._engine is not None:
            return self._engine
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRUnavailableError(
                "未安装 PaddleOCR，请执行：pip install -e '.[ocr]'"
            ) from exc
        self._engine = PaddleOCR(use_angle_cls=True, lang=self.lang, show_log=False)
        return self._engine

    def _recognize_sync(self, image_path: Path) -> list[OCRText]:
        engine = self._load()
        result = engine.ocr(str(image_path), cls=True)
        lines = result[0] if result and isinstance(result[0], list) else result
        texts: list[OCRText] = []
        for item in lines or []:
            if not item or len(item) < 2:
                continue
            payload = item[1]
            if not isinstance(payload, (list, tuple)) or len(payload) < 2:
                continue
            text, score = str(payload[0]).strip(), float(payload[1])
            if text and score >= self.min_confidence:
                texts.append(OCRText(text=text, confidence=score))
        return texts

    async def recognize(self, image_path: Path) -> list[OCRText]:
        try:
            return await asyncio.to_thread(self._recognize_sync, image_path)
        except OCRUnavailableError:
            raise
        except Exception as exc:
            raise OCRUnavailableError(f"OCR 失败：{exc}") from exc


def build_ocr(settings: Settings) -> OCRProvider | None:
    if not settings.ocr_enabled:
        return None
    if settings.ocr_provider == "paddleocr":
        return PaddleOCRProvider(
            lang=settings.ocr_lang,
            min_confidence=settings.ocr_min_confidence,
        )
    if settings.ocr_provider == "rapidocr":
        return RapidOCRProvider(min_confidence=settings.ocr_min_confidence)
    if settings.ocr_provider in {"none", "disabled"}:
        return None
    raise OCRUnavailableError(f"暂不支持的 OCR_PROVIDER：{settings.ocr_provider}")


class RapidOCRProvider:
    """轻量 PP-OCR（ONNX Runtime），无需安装 PaddlePaddle。"""

    def __init__(self, *, min_confidence: float = 0.6) -> None:
        self.min_confidence = min_confidence
        self._engine = None

    def _load(self):
        if self._engine is not None:
            return self._engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OCRUnavailableError(
                "未安装 rapidocr-onnxruntime，请执行：pip install -e '.[ocr]'"
            ) from exc
        self._engine = RapidOCR()
        return self._engine

    def _recognize_sync(self, image_path: Path) -> list[OCRText]:
        engine = self._load()
        result, _elapse = engine(str(image_path))
        texts: list[OCRText] = []
        for item in result or []:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            text = str(item[1]).strip()
            try:
                score = float(item[2])
            except (TypeError, ValueError):
                continue
            if text and score >= self.min_confidence:
                texts.append(OCRText(text=text, confidence=score))
        return texts

    async def recognize(self, image_path: Path) -> list[OCRText]:
        try:
            return await asyncio.to_thread(self._recognize_sync, image_path)
        except OCRUnavailableError:
            raise
        except Exception as exc:
            raise OCRUnavailableError(f"OCR 失败：{exc}") from exc
