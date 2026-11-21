"""OpenAI 兼容客户端：DeepSeek / DashScope / OpenAI / Ollama 共用。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import ProviderConfig
from app.llm.base import LLMError, LLMRole, Message


class OpenAICompatibleClient:
    def __init__(self, config: ProviderConfig, *, timeout: float = 120.0, max_retries: int = 3) -> None:
        self.config = config
        self.provider = config.name
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.api_key}"} if config.api_key else {},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    def _payload(
        self,
        role: LLMRole,
        messages: list[Message],
        *,
        json_mode: bool,
        max_tokens: int | None,
        stream: bool,
    ) -> dict:
        payload: dict = {
            "model": self.config.model_for(role.value),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens or 4096,
            "stream": stream,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(
        self,
        role: LLMRole,
        messages: list[Message],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        payload = self._payload(
            role, messages, json_mode=json_mode, max_tokens=max_tokens, stream=False
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post("/chat/completions", json=payload)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise LLMError("LLM_RATE_LIMITED", f"HTTP {response.status_code}", True)
                if response.status_code == 401:
                    raise LLMError("LLM_AUTH_FAILED", "API Key 无效或已过期", False)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"] or ""
            except LLMError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                raise LLMError("LLM_OUTPUT_INVALID", f"响应格式异常：{exc}", False) from exc
            if attempt < self.max_retries:
                await asyncio.sleep(min(2**attempt, 8))
        if isinstance(last_error, LLMError):
            raise last_error
        raise LLMError("LLM_UNAVAILABLE", f"调用失败：{last_error}", True)

    async def stream(
        self,
        role: LLMRole,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(
            role, messages, json_mode=False, max_tokens=max_tokens, stream=True
        )
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise LLMError(
                        "LLM_UNAVAILABLE",
                        f"HTTP {response.status_code}: {body[:200]!r}",
                        response.status_code in {429, 500, 502, 503, 504},
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise LLMError("LLM_UNAVAILABLE", f"流式调用失败：{exc}", True) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class MockClientError(LLMError):
    """兼容旧引用。"""
