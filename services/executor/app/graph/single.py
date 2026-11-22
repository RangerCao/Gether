"""单视频研报：固定大纲、时间轴窗口、证据缺口与正文装配。

单视频没有“跨来源分歧”，研报重心从冲突裁决转为：
忠于原片地复述内容 → 标注时间点 → 区分作者主张与可核实事实 → 指出证据缺口。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.contracts import EvidencePacket

# 时间轴窗口：默认按 10 段覆盖全片，单段不短于 75 秒，避免碎成一句一条。
MAX_WINDOWS = 10
MIN_WINDOW_MS = 75_000
WINDOW_MAX_CHARS = 1600

SINGLE_VIDEO_KIND = "single_video"


def is_single_video(kind: str | None) -> bool:
    return kind == SINGLE_VIDEO_KIND


def build_single_outline(question: str | None = None) -> list[dict[str, Any]]:
    """固定六段式大纲，保证不同视频的研报结构可比。

    用户带问题时追加一章，让研报直接回答该问题。
    """
    sections: list[dict[str, Any]] = [
        {
            "id": "s1",
            "title": "一句话结论",
            "points": [
                "只写 1-2 句话直接给结论，不超过 80 字",
                "标注作者给出的适用条件或前提",
            ],
        },
        {
            "id": "s2",
            "title": "内容脉络",
            "points": [
                "按时间顺序梳理视频主线，必须覆盖开头、中段、结尾",
                "每个阶段标注原片时间点，不要只写前半段",
            ],
        },
        {
            "id": "s3",
            "title": "核心观点与依据",
            "points": [
                "列出作者的主要论点",
                "说明每条论点靠什么证据或案例支撑",
            ],
        },
        {
            "id": "s4",
            "title": "关键数据与案例",
            "points": [
                "提取视频中出现的数字、对比与具体案例",
                "标注数据口径、来源与时间",
            ],
        },
        {
            "id": "s5",
            "title": "论证质量与局限",
            "points": [
                "区分事实陈述、个人判断与推测",
                "指出缺失的证据、利益立场与适用边界",
            ],
        },
        {
            "id": "s6",
            "title": "行动建议",
            "points": [
                "给出可执行的下一步",
                "说明适合谁看、什么情况下不适用",
            ],
        },
    ]
    cleaned = (question or "").strip()
    if cleaned:
        sections.append(
            {
                "id": "s7",
                "title": "针对你的问题",
                "points": [cleaned],
            }
        )
    return sections


def _modality(packet: EvidencePacket) -> str:
    return getattr(packet.modality, "value", str(packet.modality))


def _timeline_packets(evidence: list[EvidencePacket]) -> list[EvidencePacket]:
    """优先用语音转写铺时间轴；没有语音时退回 OCR 字幕/画面文字。"""
    asr = [item for item in evidence if _modality(item) == "asr"]
    if asr:
        return asr
    return [item for item in evidence if _modality(item) in {"ocr", "frame"}]


def _join_transcript(parts: list[str]) -> str:
    """ASR 片段通常没有标点，拼接时补逗号，避免连成一串。"""
    text = ""
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        if not text:
            text = chunk
        elif text[-1] in "。！？!?，,；;：:" or chunk[0] in "。！？!?，,；;：:":
            text += chunk
        else:
            text += f"，{chunk}"
    return text


def timeline_windows(
    evidence: list[EvidencePacket],
    *,
    max_windows: int = MAX_WINDOWS,
    max_chars: int = WINDOW_MAX_CHARS,
) -> list[dict[str, Any]]:
    """把证据按时间切成若干窗口，作为“内容脉络”的确定性命中。

    检索会漏时间点，这里直接用证据时间分布铺满全片，保证长视频后半段
    也能进入大纲与正文。
    """
    packets = sorted(_timeline_packets(evidence), key=lambda item: item.start_ms)
    if not packets:
        return []

    started = packets[0].start_ms
    ended = max(item.end_ms for item in packets)
    span = max(ended - started, 1)
    window_ms = max(span / max(max_windows, 1), MIN_WINDOW_MS)
    window_count = max(1, min(max_windows, math.ceil(span / window_ms)))

    windows: list[dict[str, Any]] = []
    for index in range(window_count):
        lower = started + index * window_ms
        upper = ended if index == window_count - 1 else lower + window_ms
        bucket = [
            item
            for item in packets
            if lower <= item.start_ms < upper
            or (index == window_count - 1 and item.start_ms >= lower)
        ]
        if not bucket:
            continue
        text = _join_transcript([item.text for item in bucket])
        if not text:
            continue
        truncated = text[:max_chars]
        if len(text) > max_chars:
            truncated = f"{truncated}……"
        windows.append(
            {
                "index": len(windows) + 1,
                "start_ms": int(bucket[0].start_ms),
                "end_ms": int(max(item.end_ms for item in bucket)),
                "text": truncated,
                "evidence_ids": [item.evidence_id for item in bucket],
                "video_id": str(bucket[0].video_id),
                "char_count": len(text),
            }
        )
    return windows


def window_as_context(window: dict[str, Any]) -> dict[str, Any]:
    """把时间轴窗口包装成 Writer 认识的证据块结构。"""
    return {
        "chunk_id": f"timeline-{window.get('index', 0)}",
        "video_id": window.get("video_id"),
        "text": window.get("text", ""),
        "start_ms": window.get("start_ms", 0),
        "end_ms": window.get("end_ms", 0),
        "evidence_ids": list(window.get("evidence_ids") or []),
        "modalities": ["asr"],
        "score": 1.0,
    }


def single_gaps(
    claims: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """列出没有被可验证证据支撑的结论，前端展示为“证据缺口”。"""
    verified = {
        str(entry.get("claim_id"))
        for entry in ledger
        if entry.get("evidence_id") and entry.get("verified")
    }
    has_evidence = {
        str(entry.get("claim_id")): bool(entry.get("evidence_id"))
        for entry in ledger
    }
    gaps: list[dict[str, Any]] = []
    for claim in claims:
        claim_id = str(claim.get("claim_id"))
        if claim_id in verified:
            continue
        has_ids = has_evidence.get(claim_id, bool(claim.get("evidence_ids")))
        gaps.append(
            {
                "claim_id": claim_id,
                "section_id": claim.get("section_id", ""),
                "text": claim.get("text", ""),
                "reason": (
                    "引用可追溯，但与原片措辞差异较大（疑似转述），建议回看"
                    if has_ids
                    else "未绑定原片证据"
                ),
            }
        )
        if len(gaps) >= limit:
            break
    return gaps


def single_quality(
    evidence: list[EvidencePacket],
    claims: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total = len(claims)
    verified = {
        str(entry.get("claim_id"))
        for entry in ledger
        if entry.get("evidence_id") and entry.get("verified")
    }
    verified_count = len([claim for claim in claims if str(claim.get("claim_id")) in verified])
    return {
        "evidence_count": len(evidence),
        "asr_count": len([item for item in evidence if _modality(item) == "asr"]),
        "ocr_count": len([item for item in evidence if _modality(item) == "ocr"]),
        "frame_count": len([item for item in evidence if _modality(item) == "frame"]),
        "duration_ms": max((item.end_ms for item in evidence), default=0),
        "claim_count": total,
        "verified_claim_count": verified_count,
        "support_rate": round(verified_count / total, 3) if total else 0.0,
        "timeline_windows": len(windows or []),
    }


def format_clock(ms: int) -> str:
    total = max(int(ms), 0) // 1000
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_views(views: int | None) -> str | None:
    if views is None:
        return None
    if views >= 100_000_000:
        return f"{views / 100_000_000:.1f} 亿"
    if views >= 10_000:
        return f"{views / 10_000:.1f} 万"
    return str(views)


def _format_published(published_at: int | None) -> str | None:
    if not published_at:
        return None
    try:
        return datetime.fromtimestamp(int(published_at)).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def assemble_single_markdown(
    video: dict[str, Any],
    outline: list[dict[str, Any]],
    drafts: dict[str, str],
) -> str:
    title = str(video.get("title") or "单视频研报")
    meta_parts = [
        f"UP主：{video['uploader']}" if video.get("uploader") else None,
        f"时长：{format_clock(video['duration_ms'])}" if video.get("duration_ms") else None,
        f"播放：{_format_views(video.get('views'))}" if video.get("views") is not None else None,
        f"发布：{_format_published(video.get('published_at'))}" if video.get("published_at") else None,
        f"BV：{video['source_video_id']}" if video.get("source_video_id") else None,
    ]
    header = [f"# {title}"]
    meta_line = " · ".join(part for part in meta_parts if part)
    if meta_line:
        header.append(f"> {meta_line}")
    if video.get("url"):
        header.append(f"> 原片：{video['url']}")
    parts = list(header)
    for section in outline:
        section_id = section.get("id")
        section_title = section.get("title", section_id)
        parts.append(f"## {section_title}")
        parts.append(drafts.get(section_id, "").strip())
    return "\n\n".join(part for part in parts if part).strip() + "\n"
