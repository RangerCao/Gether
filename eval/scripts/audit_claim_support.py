"""Claim-evidence 支持审计：现有报告中的 claim 是否真的被 evidence 支持。"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "executor"))

from app.core.config import get_settings  # noqa: E402
from app.llm.base import LLMRole, Message  # noqa: E402
from app.llm.factory import build_llm_client  # noqa: E402

REPORT_DIR = ROOT / "eval/results/report_quality"
OUT = ROOT / "eval/results/report_quality/claim_support.json"
MD = ROOT / "eval/results/report_quality/claim_support.md"

SYSTEM = """你是研报证据审计员。给你一条 claim 和它绑定的证据原文，判断证据是否支持 claim。
relation 只能是：
- supported：证据明确支持 claim
- partial：部分支持，或存在条件/范围/时间差异
- unsupported：证据没有支持该 claim
- contradicted：证据与 claim 矛盾
只输出 JSON：
{"results":[{"claim_id":"...","relation":"supported|partial|unsupported|contradicted","reason":"不超过40字"}]}"""


def build_claim_rows(report: dict) -> list[dict]:
    citations = {item["evidence_id"]: item for item in report.get("citations", [])}
    ledger_by_claim: dict[str, list[str]] = {}
    for entry in report.get("ledger", []):
        evidence_id = entry.get("evidence_id")
        if evidence_id:
            ledger_by_claim.setdefault(entry["claim_id"], []).append(evidence_id)
    rows = []
    for claim in report.get("claims", []):
        evidence_ids = list(dict.fromkeys(ledger_by_claim.get(claim["claim_id"], [])))
        quotes = [
            (citations[evidence_id].get("quote") or "")[:700]
            for evidence_id in evidence_ids
            if evidence_id in citations
        ]
        rows.append(
            {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "evidence_ids": evidence_ids,
                "quotes": quotes,
            }
        )
    return rows


async def audit_batch(client, rows: list[dict]) -> dict[str, dict]:
    payload = [
        {
            "claim_id": row["claim_id"],
            "claim": row["text"],
            "evidence": row["quotes"],
        }
        for row in rows
    ]
    raw = await client.complete(
        LLMRole.CRITIC,
        [
            Message("system", SYSTEM),
            Message("user", json.dumps(payload, ensure_ascii=False)),
        ],
        json_mode=True,
        max_tokens=1800,
    )
    try:
        data = json.loads(raw)
        results = data.get("results", [])
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        results = json.loads(match.group(0)).get("results", []) if match else []
    by_id = {str(item.get("claim_id")): item for item in results if isinstance(item, dict)}
    return {
        row["claim_id"]: by_id.get(
            row["claim_id"],
            {"relation": "unsupported", "reason": "审计模型未返回结果"},
        )
        for row in rows
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-claims", type=int, default=0)
    args = parser.parse_args()
    settings = get_settings()
    client = build_llm_client(settings)
    reports = []
    for path in sorted(REPORT_DIR.glob("*.json")):
        if path.stem in {"summary", "claim_support"}:
            continue
        if args.task and path.stem not in args.task:
            continue
        reports.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))

    output = []
    try:
        for task_id, report in reports:
            rows = build_claim_rows(report)
            if args.max_claims:
                rows = rows[: args.max_claims]
            audited = []
            for index in range(0, len(rows), max(args.batch_size, 1)):
                batch = rows[index : index + args.batch_size]
                with_evidence = [row for row in batch if row["evidence_ids"]]
                no_evidence = [row for row in batch if not row["evidence_ids"]]
                results: dict[str, dict] = {}
                if with_evidence:
                    results.update(await audit_batch(client, with_evidence))
                for row in no_evidence:
                    results[row["claim_id"]] = {
                        "relation": "no_evidence",
                        "reason": "没有绑定 evidence",
                    }
                for row in batch:
                    item = results.get(row["claim_id"], {"relation": "unsupported", "reason": "未审计"})
                    audited.append(
                        {
                            "claim_id": row["claim_id"],
                            "claim": row["text"],
                            "evidence_ids": row["evidence_ids"],
                            "relation": item.get("relation", "unsupported"),
                            "reason": item.get("reason", ""),
                        }
                    )
            counts = {}
            for item in audited:
                counts[item["relation"]] = counts.get(item["relation"], 0) + 1
            total = len(audited)
            supported = counts.get("supported", 0)
            partial = counts.get("partial", 0)
            no_evidence = counts.get("no_evidence", 0)
            output.append(
                {
                    "task_id": task_id,
                    "claims": total,
                    "counts": counts,
                    "support_rate": (supported + partial) / total if total else 0,
                    "strict_support_rate": supported / total if total else 0,
                    "no_evidence_rate": no_evidence / total if total else 0,
                    "items": audited,
                }
            )
            print(task_id, total, counts)
    finally:
        await client.aclose()

    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Claim-Evidence 支持审计",
        "",
        "| task | claims | supported | partial | unsupported | contradicted | no evidence | support rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in output:
        c = row["counts"]
        lines.append(
            f"| {row['task_id'][:8]} | {row['claims']} | {c.get('supported', 0)} | "
            f"{c.get('partial', 0)} | {c.get('unsupported', 0)} | "
            f"{c.get('contradicted', 0)} | {c.get('no_evidence', 0)} | "
            f"{row['support_rate']:.2%} |"
        )
    lines += ["", "说明：`no_evidence` 表示 claim 没有绑定 ledger evidence；support rate 包含 partial。", ""]
    MD.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
