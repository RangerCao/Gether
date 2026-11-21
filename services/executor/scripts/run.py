"""开发启动器：Windows 下强制 SelectorEventLoop（psycopg async 不支持 Proactor）。"""

from __future__ import annotations

import asyncio
import sys

import uvicorn


def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
    if sys.platform == "win32":
        # uvicorn 默认在 Windows 使用 ProactorEventLoop，与 psycopg async 不兼容
        config.get_loop_factory = lambda: asyncio.SelectorEventLoop  # type: ignore[method-assign]
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
