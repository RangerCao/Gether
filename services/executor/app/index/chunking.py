"""证据分块：把细粒度 ASR/OCR 切片聚合成适合检索的上下文块。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from uuid import uuid4

from app.contracts import EvidencePacket
from app.ingest.evidence import normalize_text


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    task_id: str
    video_id: str
    text: str
    start_ms: int
    end_ms: int
    evidence_ids: tuple[str, ...]
    modalities: tuple[str, ...] = field(default_factory=tuple)
    content_hash: str = ""

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "task_id": self.task_id,
            "video_id": self.video_id,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "evidence_ids": list(self.evidence_ids),
            "modalities": list(self.modalities),
            "content_hash": self.content_hash,
        }


def _make_chunk(
    *,
    task_id: str,
    video_id: str,
    items: list[EvidencePacket],
    text: str,
    start_ms: int,
    end_ms: int,
) -> Chunk:
    digest = hashlib.sha256(
        f"{video_id}|{start_ms}|{end_ms}|{text}".encode()
    ).hexdigest()
    return Chunk(
        chunk_id=str(uuid4()),
        task_id=task_id,
        video_id=video_id,
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        evidence_ids=tuple(item.evidence_id for item in items),
        modalities=tuple(sorted({item.modality.value for item in items})),
        content_hash="sha256:" + digest,
    )


def _split_long(
    item: EvidencePacket,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[Chunk]:
    text = normalize_text(item.text)
    if len(text) <= max_chars:
        return [
            _make_chunk(
                task_id=item.task_id,
                video_id=item.video_id,
                items=[item],
                text=text,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
            )
        ]
    chunks: list[Chunk] = []
    step = max(max_chars - overlap_chars, 1)
    total = len(text)
    duration = max(item.end_ms - item.start_ms, 1)
    for offset in range(0, total, step):
        piece = text[offset : offset + max_chars]
        if not piece:
            break
        start = item.start_ms + int(duration * offset / total)
        end = item.start_ms + int(duration * min(offset + len(piece), total) / total)
        chunks.append(
            _make_chunk(
                task_id=item.task_id,
                video_id=item.video_id,
                items=[item],
                text=piece,
                start_ms=start,
                end_ms=max(end, start + 1),
            )
        )
        if offset + max_chars >= total:
            break
    return chunks


def chunk_evidence(
    evidence: list[EvidencePacket],
    *,
    target_chars: int = 360,
    max_chars: int = 500,
    overlap_chars: int = 80,
) -> list[Chunk]:
    """按视频与时间顺序聚合，块间保留一个尾部证据作为重叠。"""
    target_chars = max(target_chars, 20)
    max_chars = max(max_chars, target_chars)
    overlap_chars = max(0, min(overlap_chars, max_chars // 3))

    by_video: dict[str, list[EvidencePacket]] = {}
    for item in evidence:
        if not normalize_text(item.text):
            continue
        by_video.setdefault(item.video_id, []).append(item)

    chunks: list[Chunk] = []
    for video_id, items in by_video.items():
        items.sort(key=lambda item: (item.start_ms, item.end_ms))
        current: list[EvidencePacket] = []
        current_len = 0
        carry: EvidencePacket | None = None

        def flush(video_id=video_id) -> None:
            nonlocal current, current_len, carry
            if not current:
                return
            if len(current) == 1 and carry is not None and current[0] is carry:
                return
            text = "\n".join(normalize_text(item.text) for item in current)
            chunks.append(
                _make_chunk(
                    task_id=current[0].task_id,
                    video_id=video_id,
                    items=list(current),
                    text=text,
                    start_ms=current[0].start_ms,
                    end_ms=current[-1].end_ms,
                )
            )
            carry = None
            if overlap_chars > 0 and len(normalize_text(current[-1].text)) <= overlap_chars * 2:
                carry = current[-1]
            current = [carry] if carry is not None else []
            current_len = len(normalize_text(carry.text)) if carry is not None else 0

        for item in items:
            text = normalize_text(item.text)
            if len(text) > max_chars:
                flush()
                current, current_len = [], 0
                chunks.extend(_split_long(item, max_chars=max_chars, overlap_chars=overlap_chars))
                continue
            if current and (
                current_len + len(text) > max_chars
                or (current_len >= target_chars and current_len + len(text) > target_chars)
            ):
                flush()
                if current and current_len + len(text) > max_chars:
                    current, current_len = [], 0
            current.append(item)
            current_len += len(text)
        flush()

    chunks.sort(key=lambda chunk: (chunk.video_id, chunk.start_ms))
    return chunks
