"""研报质量审计：引用有效性、claim 支持、覆盖与冲突。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "eval/results/report_quality"
BASE = "http://localhost:8081"
USER = "11111111-1111-1111-1111-111111111111"
CITE_RE = re.compile(r"\[ev:([0-9a-fA-F-]{8,})\]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120) as client:
        token = client.post(
            f"{BASE}/api/auth/token", json={"user_id": USER}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        tasks = client.get(f"{BASE}/api/tasks?limit=20", headers=headers).json()
        ids = args.task or [
            item["id"] for item in tasks if item["status"] == "DONE"
        ][: args.limit]
        rows = []
        for task_id in ids:
            report = client.get(
                f"{BASE}/api/tasks/{task_id}/report", headers=headers
            ).json()
            (OUT / f"{task_id}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            rows.append(audit(task_id, report))
    (OUT / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 研报质量审计",
        "",
        "| task | sections | claims | ledger | cites | valid cite | timed | evidence claims | unsupported | sources | conflicts |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['task_id'][:8]} | {row['sections']} | {row['claims']} | "
            f"{row['ledger']} | {row['citations_in_text']} | "
            f"{row['citation_valid_rate']:.2f} | {row['timestamp_valid_rate']:.2f} | "
            f"{row['claims_with_evidence']}/{row['claims']} | {row['unsupported']} | "
            f"{row['sources']} | {row['conflicts']} |"
        )
    lines += [
        "",
        "## 指标说明",
        "",
        "- `valid cite`：正文 `[ev:id]` 能在 citations 中找到的比例；",
        "- `timed`：引用 start/end 时间戳结构合法的比例；",
        "- `evidence claims`：ledger 中有非空 evidence_id 的 claim 数；",
        "- `unsupported`：没有非空 ledger evidence 的 claim 数；",
        "- 该脚本不判断 evidence 是否真的支持 claim，需 LLM/人工审计。",
        "",
    ]
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


def audit(task_id: str, report: dict) -> dict:
    sections = report.get("sections") or []
    claims = report.get("claims") or []
    ledger = report.get("ledger") or []
    citations = report.get("citations") or []
    conflicts = report.get("conflicts") or []
    text = "\n".join(section.get("content_md", "") for section in sections)
    cited_ids = CITE_RE.findall(text)
    citation_ids = {item.get("evidence_id") for item in citations}
    valid = [item for item in cited_ids if item in citation_ids]
    citation_pairs = {
        (item.get("evidence_id"), item.get("start_ms"), item.get("end_ms"))
        for item in citations
    }
    timed = [
        item for item in citations
        if item.get("start_ms") is not None
        and item.get("end_ms") is not None
        and item["end_ms"] > item["start_ms"]
    ]
    evidence_claim_ids = {
        item.get("claim_id") for item in ledger if item.get("evidence_id")
    }
    unsupported = [claim for claim in claims if claim.get("claim_id") not in evidence_claim_ids]
    return {
        "task_id": task_id,
        "sections": len(sections),
        "claims": len(claims),
        "ledger": len(ledger),
        "citations": len(citations),
        "citations_in_text": len(cited_ids),
        "citation_valid_rate": len(valid) / len(cited_ids) if cited_ids else 0.0,
        "timestamp_valid_rate": len(timed) / len(citations) if citations else 0.0,
        "claims_with_evidence": len(claims) - len(unsupported),
        "unsupported": len(unsupported),
        "sources": len({item.get("video_id") for item in citations if item.get("video_id")}),
        "conflicts": len(conflicts),
        "unsupported_claim_ids": [claim.get("claim_id") for claim in unsupported],
    }


if __name__ == "__main__":
    raise SystemExit(main())
