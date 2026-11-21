"""ASR 后处理纠错：LLM 只改错别字/标点，不改变切片数量与时间戳。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.ingest.subtitles import SubtitleCue
from app.llm.base import LLMClient, LLMRole, Message

logger = logging.getLogger(__name__)

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class CorrectionStats:
    batches: int = 0
    changed: int = 0
    failed: int = 0
    rejected: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "correction_batches": self.batches,
            "correction_changed": self.changed,
            "correction_failed": self.failed,
            "correction_rejected": self.rejected,
        }


class LLMCorrector:
    def __init__(
        self,
        client: LLMClient,
        *,
        batch_size: int = 30,
        max_chars: int = 3000,
        hotwords: str | None = None,
        min_similarity: float = 0.2,
    ) -> None:
        self.client = client
        self.batch_size = max(batch_size, 1)
        self.max_chars = max(max_chars, 200)
        self.hotwords = hotwords or ""
        self.min_similarity = min_similarity

    async def correct(self, cues: list[SubtitleCue]) -> tuple[list[SubtitleCue], CorrectionStats]:
        result: list[SubtitleCue] = []
        stats = CorrectionStats()
        for batch in self._batches(cues):
            corrected, batch_stats = await self._correct_batch(batch)
            result.extend(corrected)
            stats = CorrectionStats(
                batches=stats.batches + 1,
                changed=stats.changed + batch_stats.changed,
                failed=stats.failed + batch_stats.failed,
                rejected=stats.rejected + batch_stats.rejected,
            )
        return result, stats

    def _batches(self, cues: list[SubtitleCue]):
        batch: list[SubtitleCue] = []
        size = 0
        for cue in cues:
            length = len(cue.text)
            if batch and (len(batch) >= self.batch_size or size + length > self.max_chars):
                yield batch
                batch, size = [], 0
            batch.append(cue)
            size += length
        if batch:
            yield batch

    async def _correct_batch(
        self, batch: list[SubtitleCue]
    ) -> tuple[list[SubtitleCue], CorrectionStats]:
        numbered = "\n".join(f"{index}. {cue.text}" for index, cue in enumerate(batch, start=1))
        system = (
            "你是中文视频 ASR 校对器。只修正同音字、错别字、专有名词和标点；"
            "不得改写句意、不得增删内容、不得合并或拆分条目。"
            "必须返回与输入完全相同数量的条目，编号从 1 开始。"
            '只输出 JSON：{"items":[{"i":1,"text":"校对后的文本"}]}'
        )
        if self.hotwords:
            system += f"\n领域词（优先采用）：{self.hotwords}"
        messages = [
            Message("system", system),
            Message("user", f"待校对条目：\n{numbered}"),
        ]
        try:
            raw = await self.client.complete(
                LLMRole.EXTRACT,
                messages,
                json_mode=True,
                max_tokens=min(4096, max(512, len(batch) * 80)),
            )
            data = self._parse_json(raw)
        except Exception as exc:
            logger.warning("asr_correction_failed: %s", exc)
            return batch, CorrectionStats(failed=1)

        items = data.get("items")
        if not isinstance(items, list):
            return batch, CorrectionStats(failed=1)
        by_index: dict[int, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item["i"])
                text = str(item["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if text:
                by_index[index] = text

        if len(by_index) != len(batch):
            logger.warning(
                "asr_correction_count_mismatch expected=%s got=%s", len(batch), len(by_index)
            )
            return batch, CorrectionStats(failed=1)

        output: list[SubtitleCue] = []
        changed = rejected = 0
        for index, cue in enumerate(batch, start=1):
            candidate = by_index.get(index)
            if not candidate or not self._accept(cue.text, candidate):
                if candidate and candidate != cue.text:
                    rejected += 1
                output.append(cue)
                continue
            if candidate != cue.text:
                changed += 1
            output.append(
                SubtitleCue(
                    cue.start_ms,
                    cue.end_ms,
                    candidate,
                    raw_text=cue.raw_text or cue.text,
                )
            )
        return output, CorrectionStats(changed=changed, rejected=rejected)

    def _accept(self, original: str, candidate: str) -> bool:
        if not candidate or len(candidate) > max(len(original) * 2, len(original) + 20):
            return False
        if len(candidate) < max(1, int(len(original) * 0.5)):
            return False
        similarity = SequenceMatcher(None, original, candidate).ratio()
        return similarity >= self.min_similarity

    @staticmethod
    def _parse_json(raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = JSON_RE.search(raw)
            if not match:
                raise
            return json.loads(match.group(0))
