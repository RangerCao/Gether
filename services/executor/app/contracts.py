"""跨服务数据契约（与 docs/protocol.md 保持一致）。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SSEEventType(StrEnum):
    STATE = "state"
    PROGRESS = "progress"
    OUTLINE = "outline"
    TOKEN = "token"
    CITATION = "citation"
    CONFLICT = "conflict"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    DONE = "done"


class VideoSourceName(StrEnum):
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    LOCAL = "local"


class Modality(StrEnum):
    ASR = "asr"
    OCR = "ocr"
    FRAME = "frame"


class EvidenceSourceType(StrEnum):
    NATIVE_SUB = "native_sub"
    AUTO_SUB = "auto_sub"
    WHISPER = "whisper"
    OCR = "ocr"


class SupportRelation(StrEnum):
    SUPPORT = "support"
    REBUT = "rebut"
    MIXED = "mixed"
    NO_EVIDENCE = "no_evidence"


class ConflictType(StrEnum):
    DIRECT_CONTRADICTION = "direct_contradiction"
    NUMERIC_MISMATCH = "numeric_mismatch"
    CONDITION_DIFF = "condition_diff"
    TIME_DIFF = "time_diff"
    NONE = "none"


class VideoRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: VideoSourceName
    source_video_id: str = Field(min_length=1)
    page: int = Field(default=1, ge=1)
    url: str = Field(min_length=1)
    title: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    video_id: str
    modality: Modality
    text: str = Field(min_length=1)
    raw_text: str | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    frame_ref: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_type: EvidenceSourceType
    content_hash: str
    created_at: str | None = None

    @model_validator(mode="after")
    def _check_range(self) -> EvidencePacket:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms 必须大于 start_ms")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    section_id: str
    text: str = Field(min_length=1)
    qualifiers: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ledger_id: str = Field(default_factory=lambda: str(uuid4()))
    claim_id: str
    evidence_id: str
    support_relation: SupportRelation
    trust_score: float = Field(default=0.5, ge=0.0, le=1.0)
    conflict_type: ConflictType = ConflictType.NONE
    rationale: str | None = None
    verified: bool = False
    created_at: str | None = None


class OutlineSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    points: list[str] = Field(default_factory=list)


class Outline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline_id: str = Field(default_factory=lambda: str(uuid4()))
    sections: list[OutlineSection] = Field(default_factory=list)


class SSEEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    seq: int = Field(ge=0)
    ts: int
    payload: dict[str, Any] = Field(default_factory=dict)
