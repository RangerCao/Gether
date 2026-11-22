"""LangGraph 研报生成状态。"""

from __future__ import annotations

from typing import Any, TypedDict


class ReportState(TypedDict, total=False):
    task_id: str
    question: str
    report_question: str
    mode: str
    report_kind: str
    video_meta: dict[str, Any]
    outline: list[dict[str, Any]]
    current_index: int
    retries: dict[str, int]
    contexts: dict[str, list[dict[str, Any]]]
    critiques: dict[str, dict[str, Any]]
    drafts: dict[str, str]
    claims: list[dict[str, Any]]
    ledger: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    quality: dict[str, Any]
    warnings: list[str]
    citation_stats: dict[str, int]
    report_markdown: str
