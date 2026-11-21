"""证据包构建：时间戳、来源、去重、模态合并。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.contracts import EvidencePacket, EvidenceSourceType, Modality, VideoRef
from app.ingest.subtitles import SubtitleCue

WHITESPACE_RE = re.compile(r"\s+")
CJK_SPACE_RE = re.compile(r"(?<=[一-鿿])\s+(?=[一-鿿])")
CJK_PUNCT = "，。！？、：；（）《》"
CJK_PUNCT_AFTER_RE = re.compile(r"(?<=[" + CJK_PUNCT + r"])\s+")
CJK_PUNCT_BEFORE_RE = re.compile(r"\s+(?=[" + CJK_PUNCT + r"])")


@dataclass(frozen=True)
class OCRBlock:
    start_ms: int
    end_ms: int
    text: str
    confidence: float = 1.0
    frame_ref: str | None = None


def normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def normalize_ocr_text(text: str) -> str:
    """去掉中文之间由 OCR 换行/排版产生的空格，保留中英文之间的空格。"""
    text = CJK_SPACE_RE.sub("", normalize_text(text))
    text = CJK_PUNCT_AFTER_RE.sub("", text)
    return CJK_PUNCT_BEFORE_RE.sub("", text)


def content_hash(ref: VideoRef, start_ms: int, end_ms: int, text: str) -> str:
    payload = (
        f"{ref.source.value}|{ref.source_video_id}|{ref.page}|{start_ms}|{end_ms}|{normalize_text(text)}"
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_evidence(
    cues: list[SubtitleCue],
    *,
    task_id: str,
    video_id: str,
    ref: VideoRef,
    source_type: EvidenceSourceType,
    modality: Modality = Modality.ASR,
    confidence: float = 1.0,
) -> list[EvidencePacket]:
    seen: set[str] = set()
    result: list[EvidencePacket] = []
    for cue in cues:
        text = normalize_text(cue.text)
        if not text or cue.end_ms <= cue.start_ms:
            continue
        digest = content_hash(ref, cue.start_ms, cue.end_ms, text)
        if digest in seen:
            continue
        seen.add(digest)
        result.append(
            EvidencePacket(
                task_id=task_id,
                video_id=video_id,
                modality=modality,
                text=text,
                raw_text=cue.raw_text if cue.raw_text and cue.raw_text != text else None,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                confidence=max(0.0, min(confidence, 1.0)),
                source_type=source_type,
                content_hash=digest,
            )
        )
    return result


def merge_ocr_blocks(blocks: list[OCRBlock], max_gap_ms: int = 1500) -> list[OCRBlock]:
    """相邻且文本相同的 OCR 块合并为区间证据，避免同一页 PPT 反复入库。"""
    ordered = sorted(blocks, key=lambda item: (item.start_ms, item.end_ms))
    merged: list[OCRBlock] = []
    for block in ordered:
        text = normalize_ocr_text(block.text)
        if not text or block.end_ms <= block.start_ms:
            continue
        current = OCRBlock(block.start_ms, block.end_ms, text, block.confidence, block.frame_ref)
        if merged:
            prev = merged[-1]
            same_text = normalize_ocr_text(prev.text) == current.text
            close_enough = current.start_ms - prev.end_ms <= max_gap_ms
            if same_text and close_enough:
                merged[-1] = OCRBlock(
                    start_ms=prev.start_ms,
                    end_ms=max(prev.end_ms, current.end_ms),
                    text=prev.text,
                    confidence=max(prev.confidence, current.confidence),
                    frame_ref=prev.frame_ref or current.frame_ref,
                )
                continue
        merged.append(current)
    return merged


def build_ocr_evidence(
    blocks: list[OCRBlock],
    *,
    task_id: str,
    video_id: str,
    ref: VideoRef,
    max_gap_ms: int = 1500,
) -> list[EvidencePacket]:
    merged = merge_ocr_blocks(blocks, max_gap_ms=max_gap_ms)
    seen: set[str] = set()
    result: list[EvidencePacket] = []
    for block in merged:
        digest = content_hash(ref, block.start_ms, block.end_ms, block.text)
        if digest in seen:
            continue
        seen.add(digest)
        result.append(
            EvidencePacket(
                task_id=task_id,
                video_id=video_id,
                modality=Modality.OCR,
                text=block.text,
                start_ms=block.start_ms,
                end_ms=block.end_ms,
                frame_ref=block.frame_ref,
                confidence=block.confidence,
                source_type=EvidenceSourceType.OCR,
                content_hash=digest,
            )
        )
    return result
