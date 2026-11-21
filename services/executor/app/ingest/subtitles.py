"""字幕解析：SRT / WebVTT → 统一 cue（毫秒时间戳）。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

TIMING_RE = re.compile(
    r"(?P<start>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(?P<end>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})"
)
TIME_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SubtitleCue:
    start_ms: int
    end_ms: int
    text: str
    raw_text: str | None = None


def parse_subtitle(path: str | Path) -> list[SubtitleCue]:
    file_path = Path(path)
    raw = file_path.read_text(encoding="utf-8-sig", errors="replace")
    if file_path.suffix.lower() == ".vtt":
        cues = _parse_vtt(raw)
    elif file_path.suffix.lower() == ".srt":
        cues = _parse_srt(raw)
    else:
        cues = _parse_srt(raw) or _parse_vtt(raw)
    return dedupe_cues(cues)


def _parse_srt(raw: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    for block in re.split(r"\r?\n\s*\r?\n", raw):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_index = next((i for i, line in enumerate(lines) if TIMING_RE.search(line)), None)
        if timing_index is None:
            continue
        match = TIMING_RE.search(lines[timing_index])
        if not match:
            continue
        start_ms = _time_to_ms(match.group("start"))
        end_ms = _time_to_ms(match.group("end"))
        text = _clean_text(" ".join(lines[timing_index + 1 :]))
        if text and end_ms > start_ms:
            cues.append(SubtitleCue(start_ms, end_ms, text))
    return cues


def _parse_vtt(raw: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    # 去掉 WEBVTT 头、NOTE/STYLE/REGION 块
    raw = re.sub(r"^\ufeff?WEBVTT.*?(?:\r?\n){2}", "", raw, flags=re.DOTALL)
    raw = re.sub(r"(?m)^(NOTE|STYLE|REGION)\b.*?(?:\r?\n){2}", "", raw, flags=re.DOTALL)
    for block in re.split(r"\r?\n\s*\r?\n", raw):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_index = next((i for i, line in enumerate(lines) if TIMING_RE.search(line)), None)
        if timing_index is None:
            continue
        match = TIMING_RE.search(lines[timing_index])
        if not match:
            continue
        start_ms = _time_to_ms(match.group("start"))
        end_ms = _time_to_ms(match.group("end"))
        # 跳过纯数字 cue id
        body = lines[timing_index + 1 :]
        if body and body[0].strip().isdigit():
            body = body[1:]
        text = _clean_text(" ".join(body))
        if text and end_ms > start_ms:
            cues.append(SubtitleCue(start_ms, end_ms, text))
    return cues


def _time_to_ms(value: str) -> int:
    match = TIME_RE.search(value.strip())
    if not match:
        raise ValueError(f"非法时间戳：{value}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int(match.group(4).ljust(3, "0")[:3])
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _clean_text(text: str) -> str:
    text = text.replace("\\N", " ").replace("\\n", " ")
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    return WHITESPACE_RE.sub(" ", text).strip()


def dedupe_cues(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    """合并滚动字幕产生的重复/前缀重叠 cue，保留时间覆盖与更长文本。"""
    result: list[SubtitleCue] = []
    for cue in cues:
        text = cue.text.strip()
        if cue.end_ms <= cue.start_ms or not text:
            continue
        cue = SubtitleCue(cue.start_ms, cue.end_ms, text)
        if not result:
            result.append(cue)
            continue
        prev = result[-1]
        if cue.text == prev.text:
            result[-1] = SubtitleCue(prev.start_ms, max(prev.end_ms, cue.end_ms), prev.text)
            continue
        if cue.text.startswith(prev.text) or prev.text.startswith(cue.text):
            longer = cue.text if len(cue.text) >= len(prev.text) else prev.text
            result[-1] = SubtitleCue(
                prev.start_ms,
                max(prev.end_ms, cue.end_ms),
                longer,
            )
            continue
        result.append(cue)
    return result
