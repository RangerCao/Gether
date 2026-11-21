"""本地解析链路演示（需要先启动 executor）。

用法：bash scripts/dev.sh demo
"""

from __future__ import annotations

import io
import shutil
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "data" / "demo"
FIXTURE = ROOT / "services" / "executor" / "tests" / "fixtures" / "sample.srt"
BASE_URL = "http://localhost:8000"


def prepare_fixture() -> str:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    video = DEMO_DIR / "demo.mp4"
    video.touch(exist_ok=True)
    shutil.copyfile(FIXTURE, DEMO_DIR / "demo.srt")
    return video.as_uri()


def main() -> int:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    url = prepare_fixture()
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        health = client.get("/health").json()
        print(f"executor: {health['status']} | llm={health['llm_provider']}")

        created = client.post("/internal/tasks", json={"videos": [url]})
        created.raise_for_status()
        task_id = created.json()["task_id"]
        print(f"task    : {task_id}")

        accepted = client.post(f"/internal/tasks/{task_id}/parse")
        accepted.raise_for_status()
        print("parse   : accepted")

        summary = {}
        for _ in range(60):
            summary = client.get(f"/internal/tasks/{task_id}").json()
            if summary["status"] in {"PLANNING", "FAILED", "DONE"}:
                break
            time.sleep(0.5)

        print(
            f"status  : {summary.get('status')} | progress={summary.get('progress')} "
            f"| evidence={summary.get('evidence_count')}"
        )

        evidence = client.get(f"/internal/tasks/{task_id}/evidence").json()
        for item in evidence["evidence"][:10]:
            print(
                f"  [{item['start_ms']:>6} - {item['end_ms']:>6}] "
                f"{item['source_type']:<10} {item['text']}"
            )
    return 0 if summary.get("status") == "PLANNING" else 1


if __name__ == "__main__":
    raise SystemExit(main())
