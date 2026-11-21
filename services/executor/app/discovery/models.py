"""视频发现阶段的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Facet:
    id: str
    label: str
    query: str
    stance: str = "neutral"
    required_any: list[str] = field(default_factory=list)
    exclude_any: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "query": self.query,
            "stance": self.stance,
            "required_any": list(self.required_any),
            "exclude_any": list(self.exclude_any),
        }


@dataclass
class SearchHit:
    source: str
    source_video_id: str
    url: str
    title: str = ""
    duration_ms: int | None = None
    uploader: str | None = None
    published_at: int | None = None
    views: int | None = None
    likes: int | None = None
    coins: int | None = None
    favorites: int | None = None
    description: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    source: str
    source_video_id: str
    url: str
    title: str
    duration_ms: int | None = None
    uploader: str | None = None
    published_at: int | None = None
    views: int | None = None
    description: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    matched_facets: list[str] = field(default_factory=list)
    relevance: float = 0.0
    authority: float = 0.0
    freshness: float = 0.0
    redundancy: float = 0.0
    final_score: float = 0.0
    topic_score: float = 0.0
    topic_gate: bool = True
    rejection_reason: str | None = None
    facet_confidence: dict[str, float] = field(default_factory=dict)
    cluster_id: str | None = None
    selected: bool = False
    reason: str | None = None
    candidate_id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.description}".strip()

    def as_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "source_video_id": self.source_video_id,
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "duration_ms": self.duration_ms,
            "uploader": self.uploader,
            "published_at": self.published_at,
            "views": self.views,
            "metrics": self.metrics,
            "matched_facets": self.matched_facets,
            "relevance": round(self.relevance, 4),
            "authority": round(self.authority, 4),
            "freshness": round(self.freshness, 4),
            "redundancy": round(self.redundancy, 4),
            "final_score": round(self.final_score, 4),
            "topic_score": round(self.topic_score, 4),
            "topic_gate": self.topic_gate,
            "rejection_reason": self.rejection_reason,
            "facet_confidence": {
                key: round(value, 4) for key, value in self.facet_confidence.items()
            },
            "cluster_id": self.cluster_id,
            "selected": self.selected,
            "reason": self.reason,
        }
