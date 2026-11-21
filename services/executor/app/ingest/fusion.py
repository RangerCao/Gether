"""跨模态融合：用高置信 OCR 字幕修正/替换重复的 ASR 切片。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.contracts import EvidencePacket, Modality

PUNCT_RE = re.compile(r"[\s，。！？、,.!?“”\"：:；;（）()《》<>-]")


def _normalize(text: str) -> str:
    return PUNCT_RE.sub("", text)


def _overlap(a: EvidencePacket, b: EvidencePacket) -> int:
    return max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))


@dataclass(frozen=True)
class FusionStats:
    fused_ocr_blocks: int = 0
    consumed_asr_cues: int = 0
    kept_ocr_blocks: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "fused_ocr_blocks": self.fused_ocr_blocks,
            "consumed_asr_cues": self.consumed_asr_cues,
            "kept_ocr_blocks": self.kept_ocr_blocks,
        }


def _best_subset(
    candidates: list[tuple[int, EvidencePacket]], block_text: str
) -> tuple[float, list[tuple[int, EvidencePacket]]]:
    """在候选 ASR 中找与 OCR 文本最相似的连续子序列，避免邻居干扰。"""
    from difflib import SequenceMatcher

    target = _normalize(block_text)
    best_score = 0.0
    best: list[tuple[int, EvidencePacket]] = []
    limit = min(len(candidates), 8)
    for start in range(limit):
        for end in range(start + 1, min(start + 6, limit) + 1):
            joined = "".join(cue.text for _, cue in candidates[start:end])
            score = SequenceMatcher(None, _normalize(joined), target).ratio()
            if score > best_score:
                best_score = score
                best = candidates[start:end]
    return best_score, best


def fuse_ocr_asr(
    evidence: list[EvidencePacket],
    *,
    min_similarity: float = 0.55,
    min_confidence: float = 0.7,
) -> tuple[list[EvidencePacket], FusionStats]:
    """OCR 字幕与 ASR 文本高度重合时，保留 OCR、消费 ASR。

    仅当 OCR 置信度足够且文本相似度达标时融合；幻灯片/画面文字与语音
    不相似时保持两条证据，避免误删。
    """

    asr = [item for item in evidence if item.modality == Modality.ASR]
    ocr = [item for item in evidence if item.modality == Modality.OCR]
    if not asr or not ocr:
        return evidence, FusionStats()

    consumed: set[int] = set()
    merged: list[EvidencePacket] = []
    stats = FusionStats()

    for block in ocr:
        candidates: list[tuple[int, EvidencePacket]] = []
        for index, cue in enumerate(asr):
            if index in consumed:
                continue
            overlap = _overlap(cue, block)
            midpoint = (cue.start_ms + cue.end_ms) / 2
            cue_duration = max(cue.end_ms - cue.start_ms, 1)
            block_duration = max(block.end_ms - block.start_ms, 1)
            if overlap >= 0.2 * min(cue_duration, block_duration) or (
                block.start_ms <= midpoint <= block.end_ms
            ):
                candidates.append((index, cue))

        if candidates and block.confidence >= min_confidence:
            similarity, best = _best_subset(candidates, block.text)
            if similarity >= min_similarity and len(_normalize(block.text)) >= 2:
                consumed.update(index for index, _ in best)
                stats = FusionStats(
                    fused_ocr_blocks=stats.fused_ocr_blocks + 1,
                    consumed_asr_cues=stats.consumed_asr_cues + len(best),
                    kept_ocr_blocks=stats.kept_ocr_blocks,
                )
                merged.append(block)
                continue

        stats = FusionStats(
            fused_ocr_blocks=stats.fused_ocr_blocks,
            consumed_asr_cues=stats.consumed_asr_cues,
            kept_ocr_blocks=stats.kept_ocr_blocks + 1,
        )
        merged.append(block)

    merged.extend(cue for index, cue in enumerate(asr) if index not in consumed)
    merged.sort(key=lambda item: (item.start_ms, item.end_ms))
    return merged, stats
