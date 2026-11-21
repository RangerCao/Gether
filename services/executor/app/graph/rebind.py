"""Writer 生成后的 claim-evidence 二次绑定。

只负责把正文句子的引用重绑到章节上下文里最匹配的 evidence；
优先级：本地 cross-encoder > 词面相似度 > 保留原引用。
"""

from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher

from app.graph.guard import CITATION_RE, SENTENCE_RE

_CITATION_ONLY = re.compile(r"^\s*[#*\-]+\s*")


def _plain(sentence: str) -> str:
    return CITATION_RE.sub("", sentence).strip()


def _lexical_score(claim: str, evidence: str) -> float:
    return SequenceMatcher(None, claim, evidence).ratio()


class EvidenceRebinder:
    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3-int8",
        cache_dir: str | None = None,
        threshold: float = 0.0,
        lexical_threshold: float = 0.45,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.threshold = threshold
        self.lexical_threshold = lexical_threshold
        self._model = None
        self._failed = False

    def _load(self):
        if self._model is not None or self._failed:
            return self._model
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(
                model_name=self.model_name,
                cache_dir=self.cache_dir,
            )
        except Exception:
            self._failed = True
        return self._model

    async def rebind(self, drafts: dict[str, str], contexts: dict[str, list[dict]]) -> dict[str, str]:
        model = await asyncio.to_thread(self._load)
        result: dict[str, str] = {}
        for section_id, text in drafts.items():
            candidates: list[tuple[str, str]] = []
            for chunk in contexts.get(section_id, []):
                for evidence_id in chunk.get("evidence_ids", []):
                    candidates.append((str(evidence_id), str(chunk.get("text") or "")))
            if not candidates:
                result[section_id] = text
                continue
            result[section_id] = await asyncio.to_thread(
                self._rebind_section, text, candidates, model
            )
        return result

    def _rebind_section(
        self,
        text: str,
        candidates: list[tuple[str, str]],
        model,
    ) -> str:
        output: list[str] = []
        for paragraph in text.split("\n"):
            sentences = SENTENCE_RE.findall(paragraph)
            if not sentences:
                output.append(paragraph)
                continue
            rewritten: list[str] = []
            for sentence in sentences:
                stripped = sentence.strip()
                claim = _plain(stripped)
                if not claim or stripped.startswith("#"):
                    rewritten.append(sentence)
                    continue
                best_id, best_score = self._best(claim, candidates, model)
                current_ids = [
                    item for item in CITATION_RE.findall(stripped) if item
                ]
                required = self.threshold if model is not None else self.lexical_threshold
                if best_score < required:
                    # 保留原引用；后续 enforce_citations 会处理无效句。
                    rewritten.append(sentence)
                    continue
                if current_ids and best_id in current_ids:
                    rewritten.append(sentence)
                    continue
                new_sentence = CITATION_RE.sub("", sentence).rstrip()
                if not new_sentence.endswith(("。", "！", "？", ".", "!", "?")):
                    new_sentence += "。"
                rewritten.append(f"{new_sentence} [ev:{best_id}] ")
            output.append("".join(rewritten).strip())
        return "\n".join(output).strip()

    def _best(self, claim: str, candidates: list[tuple[str, str]], model) -> tuple[str, float]:
        if model is not None:
            try:
                scores = list(
                    model.rerank(
                        claim,
                        [evidence for _, evidence in candidates],
                    )
                )
                index = max(range(len(scores)), key=lambda i: scores[i])
                return candidates[index][0], float(scores[index])
            except Exception:
                pass
        scored = [
            (evidence_id, _lexical_score(claim, evidence))
            for evidence_id, evidence in candidates
        ]
        return max(scored, key=lambda item: item[1], default=("", 0.0))
