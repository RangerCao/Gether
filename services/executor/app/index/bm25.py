"""中文 BM25 与 RRF 融合。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import jieba
from rank_bm25 import BM25Okapi

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z]+|\d+")
STOPWORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "我们", "你们",
    "这个", "那个", "就是", "然后", "因为", "所以", "但是", "而且", "以及",
}


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in jieba.lcut(text):
        word = word.strip().lower()
        if not word or word in STOPWORDS:
            continue
        if TOKEN_RE.fullmatch(word):
            tokens.append(word)
    return tokens


@dataclass(frozen=True)
class BM25Hit:
    index: int
    score: float


class BM25Index:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.tokens = [tokenize(text) for text in texts]
        self._bm25 = BM25Okapi(self.tokens) if self.tokens else None

    def search(self, query: str, top_k: int = 50) -> list[BM25Hit]:
        if self._bm25 is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        return [BM25Hit(index=index, score=float(score)) for index, score in ranked[:top_k] if score > 0]


def rrf_fuse(
    rankings: list[list[str]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：score = Σ w_i / (k + rank_i)。"""
    if weights is None:
        weights = [1.0] * len(rankings)
    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights, strict=False):
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
