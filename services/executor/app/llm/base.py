"""LLM 角色契约。业务只依赖角色，不依赖厂商。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class LLMRole(StrEnum):
    PLANNER = "planner"
    CRITIC = "critic"
    EXTRACT = "extract"
    WRITER = "writer"
    ROUTER = "router"
    QUERY_REWRITE = "query_rewrite"


@dataclass(frozen=True)
class Message:
    role: str  # system | user | assistant
    content: str


class LLMError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LLMClient(Protocol):
    provider: str

    async def complete(
        self,
        role: LLMRole,
        messages: list[Message],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str: ...

    def stream(
        self,
        role: LLMRole,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...
