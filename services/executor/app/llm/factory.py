"""LLM 工厂：按配置注入当前供应商，未启用供应商不初始化。"""

from __future__ import annotations

from app.core.config import Settings
from app.llm.base import LLMClient
from app.llm.mock import MockLLMClient
from app.llm.openai_compatible import OpenAICompatibleClient


def build_llm_client(settings: Settings, provider: str | None = None) -> LLMClient:
    name = provider or settings.llm_provider
    config = settings.provider_config(name)
    if name == "mock":
        return MockLLMClient()
    return OpenAICompatibleClient(
        config,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def build_fallback_client(settings: Settings) -> LLMClient | None:
    if not settings.llm_fallback_provider:
        return None
    return build_llm_client(settings, settings.llm_fallback_provider)
