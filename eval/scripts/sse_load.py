"""Java SSE 并发连接压测（轻量版）。

用法：
    python eval/scripts/sse_load.py --task-id <UUID> --connections 500 --duration 10
"""

from __future__ import annotations

import argparse
import asyncio

import httpx


async def client(base: str, task_id: str, duration: float, stats: dict, lock: asyncio.Lock) -> None:
    headers = {"Last-Event-ID": "0", "Accept": "text/event-stream"}

    async def consume(response: httpx.Response) -> None:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                async with lock:
                    stats["events"] += 1

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=10.0)) as http:
            async with http.stream(
                "GET", f"{base}/api/tasks/{task_id}/events", headers=headers
            ) as response:
                if response.status_code != 200:
                    async with lock:
                        stats["errors"] += 1
                    return
                async with lock:
                    stats["connected"] += 1
                try:
                    await asyncio.wait_for(consume(response), timeout=duration)
                except (asyncio.TimeoutError, httpx.ReadTimeout):
                    pass
    except Exception:
        async with lock:
            stats["errors"] += 1
    finally:
        async with lock:
            stats["closed"] += 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8081")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--connections", type=int, default=500)
    parser.add_argument("--duration", type=float, default=10.0)
    args = parser.parse_args()

    stats = {"connected": 0, "errors": 0, "closed": 0, "events": 0}
    lock = asyncio.Lock()
    await asyncio.gather(
        *[
            client(args.base, args.task_id, args.duration, stats, lock)
            for _ in range(args.connections)
        ]
    )
    print(
        {
            **stats,
            "connections": args.connections,
            "events_per_connection": round(stats["events"] / max(args.connections, 1), 2),
        }
    )
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
