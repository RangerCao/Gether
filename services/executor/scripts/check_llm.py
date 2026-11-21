"""检查当前 LLM 供应商是否可用（不打印密钥）。

用法：
    cd services/executor
    .venv/Scripts/python scripts/check_llm.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import time

from app.core.config import Settings
from app.llm.base import LLMError, LLMRole, Message
from app.llm.factory import build_llm_client


def mask(value: str | None) -> str:
    if not value:
        return "(未配置)"
    return f"{'*' * 8}{value[-4:]}" if len(value) > 4 else "****"


async def main() -> int:
    settings = Settings()
    provider = settings.effective_provider
    print(f"provider : {settings.llm_provider}")
    print(f"base_url : {provider.base_url}")
    print(f"model    : {provider.model_for('router')}")
    print(f"api_key  : {mask(provider.api_key)}")

    client = build_llm_client(settings)
    started = time.perf_counter()
    try:
        output = await client.complete(
            LLMRole.ROUTER,
            [Message("user", "只回复两个字：连通")],
            max_tokens=16,
        )
        elapsed = time.perf_counter() - started
        print(f"latency  : {elapsed:.2f}s")
        print(f"output   : {output.strip()[:120]}")
        print("result   : OK")
        return 0
    except LLMError as exc:
        print(f"result   : FAILED [{exc.code}] {exc.message}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
