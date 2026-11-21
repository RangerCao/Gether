"""Embedding 适配器：本地 FastEmbed（ONNX）、DashScope、Mock。"""

from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Protocol

import httpx

from app.core.config import Settings


class EmbeddingError(RuntimeError):
    code = "EMBEDDING_FAILED"


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def aclose(self) -> None: ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class FastEmbedProvider:
    name = "fastembed"

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        dim: int = 512,
        batch_size: int = 32,
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self.batch_size = batch_size
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingError(
                "未安装 fastembed，请执行：pip install -e '.[retrieval]'"
            ) from exc
        self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = [list(map(float, vector)) for vector in model.embed(texts, batch_size=self.batch_size)]
        return [_normalize(vector) for vector in vectors]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return await asyncio.to_thread(self._embed_sync, texts)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"本地向量化失败：{exc}") from exc

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]

    async def aclose(self) -> None:
        return None


class MockEmbeddingProvider:
    name = "mock"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [((digest[index % len(digest)] / 255.0) * 2 - 1) for index in range(self.dim)]
        return _normalize(raw)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    async def aclose(self) -> None:
        return None


class DashScopeEmbeddingProvider:
    name = "dashscope"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-v3",
        dim: int = 1024,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) -> None:
        self.dim = dim
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings",
            json={"model": self.model, "input": texts, "dimensions": self.dim},
        )
        if response.status_code >= 400:
            raise EmbeddingError(f"DashScope embedding 失败：HTTP {response.status_code}")
        data = response.json().get("data", [])
        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        return [_normalize(vector) for vector in vectors]

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]

    async def aclose(self) -> None:
        await self._client.aclose()


def build_embedding(settings: Settings) -> EmbeddingProvider:
    provider = settings.embedding_provider.lower()
    if provider in {"local", "fastembed"}:
        return FastEmbedProvider(
            settings.embedding_model,
            dim=settings.embedding_dim,
            batch_size=settings.embedding_batch_size,
        )
    if provider == "mock":
        return MockEmbeddingProvider(settings.embedding_dim)
    if provider == "dashscope":
        api_key = settings.embedding_api_key or settings.dashscope_api_key
        if not api_key:
            raise EmbeddingError("EMBEDDING_PROVIDER=dashscope 需要 DASHSCOPE_API_KEY")
        return DashScopeEmbeddingProvider(
            api_key=api_key,
            model=settings.dashscope_embedding_model,
            dim=settings.dashscope_embedding_dim,
        )
    raise EmbeddingError(f"未知 EMBEDDING_PROVIDER：{settings.embedding_provider}")
