"""端到端驱动：Discovery → 自动选片 → 解析 → 大纲 → 研报。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "eval/results/e2e"
BASE = "http://localhost:8081"
USER = "11111111-1111-1111-1111-111111111111"

QUESTIONS = [
    "AI 编程工具真的能提升开发效率吗？",
    "人生第一辆车应不应该买二手？",
    "咖啡对健康到底是有益还是有害？",
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-videos", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--question", action="append", default=[])
    args = parser.parse_args()
    questions = args.question or QUESTIONS
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180) as client:
        token = client.post(
            f"{BASE}/api/auth/token", json={"user_id": USER}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        results = []
        for question in questions:
            answer = {"question": question}
            started = time.time()
            try:
                discovery = client.post(
                    f"{BASE}/api/discovery/sessions",
                    headers=headers,
                    json={"question": question, "max_videos": args.max_videos},
                ).json()
                candidates = discovery.get("selected") or []
                answer["discovery_id"] = discovery.get("session_id")
                answer["candidate_count"] = len(candidates)
                answer["candidate_titles"] = [item.get("title") for item in candidates]
                if not candidates:
                    answer["error"] = "no selected candidates"
                    results.append(answer)
                    continue
                confirmed = client.post(
                    f"{BASE}/api/discovery/sessions/{discovery['session_id']}/confirm",
                    headers=headers,
                    json={"selected_ids": [item["candidate_id"] for item in candidates]},
                ).json()
                task_id = confirmed["task_id"]
                answer["task_id"] = task_id
                # Java confirm triggers parse.
                deadline = time.time() + args.timeout
                task = {}
                while time.time() < deadline:
                    task = client.get(f"{BASE}/api/tasks/{task_id}", headers=headers).json()
                    print(question, task.get("status"), task.get("progress"), flush=True)
                    if task.get("status") in {"PLANNING", "DONE", "FAILED"}:
                        break
                    if task.get("status") == "PARSING":
                        time.sleep(10)
                    else:
                        time.sleep(2)
                answer["parse_status"] = task.get("status")
                answer["parse_progress"] = task.get("progress")
                if task.get("status") == "FAILED":
                    answer["error"] = {
                        "code": task.get("error_code"),
                        "message": task.get("error_message"),
                    }
                    results.append(answer)
                    continue
                plan = client.post(
                    f"{BASE}/api/tasks/{task_id}/plan", headers=headers
                )
                answer["plan_status"] = plan.status_code
                if plan.status_code != 200:
                    answer["error"] = {"plan": plan.status_code, "body": plan.text[:500]}
                    results.append(answer)
                    continue
                plan_data = plan.json()
                answer["outline_count"] = len(plan_data.get("outline") or [])
                # Use the same Java stream endpoint as the frontend.
                with client.stream(
                    "POST",
                    f"{BASE}/api/tasks/{task_id}/resume/stream",
                    headers=headers,
                    json={"outline": plan_data.get("outline"), "approved": True},
                    timeout=600,
                ) as stream:
                    events = []
                    for line in stream.iter_lines():
                        if line.startswith("event: "):
                            events.append(line[7:])
                    answer["stream_status"] = stream.status_code
                    answer["stream_events"] = events[-30:]
                report = client.get(
                    f"{BASE}/api/tasks/{task_id}/report", headers=headers
                ).json()
                (OUT / f"{task_id}.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                answer["report_sections"] = len(report.get("sections") or [])
                answer["report_claims"] = len(report.get("claims") or [])
                answer["report_citations"] = len(report.get("citations") or [])
                answer["elapsed_seconds"] = round(time.time() - started, 1)
                answer["ok"] = answer["report_sections"] > 0
            except Exception as exc:
                answer["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
            results.append(answer)
            (OUT / "run.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        (OUT / "summary.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
