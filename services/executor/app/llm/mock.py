"""Mock LLM：无 Key 联调与 CI 使用，输出确定、结构合法。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from app.llm.base import LLMRole, Message


class MockLLMClient:
    provider = "mock"

    async def complete(
        self,
        role: LLMRole,
        messages: list[Message],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        if role == LLMRole.PLANNER:
            return json.dumps(
                {
                    "sections": [
                        {"id": "s1", "title": "行业现状", "points": ["市场规模", "增速变化"]},
                        {"id": "s2", "title": "观点分歧", "points": ["乐观方论据", "谨慎方论据"]},
                        {"id": "s3", "title": "结论与展望", "points": ["条件化结论"]},
                    ]
                },
                ensure_ascii=False,
            )
        if role == LLMRole.EXTRACT:
            return json.dumps(
                {
                    "claims": [
                        {
                            "text": "示例结论：该行业明年增速将回落至 5%",
                            "qualifiers": {"time": "2026 年", "scope": "中国市场"},
                            "evidence_ids": ["mock-evidence-1"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if role == LLMRole.CRITIC:
            return json.dumps(
                {"supported": True, "reason": "mock 认为证据充分", "missing": []},
                ensure_ascii=False,
            )
        if role == LLMRole.ROUTER:
            return json.dumps({"route": "writer", "reason": "mock"}, ensure_ascii=False)
        return "这是 mock 生成的占位正文。用于无 API Key 时验证链路连通性。"

    async def stream(
        self,
        role: LLMRole,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        text = await self.complete(role, messages)
        for chunk in (text[i : i + 8] for i in range(0, len(text), 8)):
            await asyncio.sleep(0)
            yield chunk

    async def aclose(self) -> None:
        return None
