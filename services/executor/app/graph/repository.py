"""研报、Claim、Ledger 与冲突持久化。"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from app.core.config import Settings
from app.core.db import Database


class ReportRepository:
    def __init__(self, settings: Settings) -> None:
        self.db = Database(settings)

    async def save_report(
        self,
        *,
        task_id: str,
        outline: list[dict],
        drafts: dict[str, str],
        claims: list[dict],
        ledger: list[dict],
        conflicts: list[dict],
    ) -> None:
        async with self.db.connection() as conn:
            await conn.execute("DELETE FROM conflicts WHERE task_id = %s", (task_id,))
            await conn.execute("DELETE FROM claims WHERE task_id = %s", (task_id,))
            await conn.execute("DELETE FROM report_sections WHERE task_id = %s", (task_id,))
            for order, section in enumerate(outline, start=1):
                section_id = str(section.get("id") or f"s{order}")
                await conn.execute(
                    "INSERT INTO report_sections "
                    "(task_id, order_no, title, content_md, status) "
                    "VALUES (%s, %s, %s, %s, 'DONE')",
                    (
                        task_id,
                        order,
                        section.get("title") or section_id,
                        drafts.get(section_id, ""),
                    ),
                )
            for claim in claims:
                await conn.execute(
                    "INSERT INTO claims "
                    "(id, task_id, section_id, text, qualifiers_json) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (
                        claim["claim_id"],
                        task_id,
                        claim.get("section_id", ""),
                        claim["text"],
                        Jsonb(claim.get("qualifiers") or {}),
                    ),
                )
            for entry in ledger:
                await conn.execute(
                    "INSERT INTO ledger_entries "
                    "(claim_id, evidence_id, support_relation, trust_score, "
                    "conflict_type, rationale, verified) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        entry["claim_id"],
                        entry.get("evidence_id"),
                        entry.get("support_relation", "support"),
                        entry.get("trust_score", 0.5),
                        entry.get("conflict_type", "none"),
                        entry.get("rationale"),
                        bool(entry.get("verified", False)),
                    ),
                )
            for conflict in conflicts:
                await conn.execute(
                    "INSERT INTO conflicts "
                    "(task_id, type, claim_ids, evidence_ids, summary, similarity) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        task_id,
                        conflict["type"],
                        Jsonb(conflict.get("claim_ids") or []),
                        Jsonb(conflict.get("evidence_ids") or []),
                        conflict.get("summary"),
                        conflict.get("similarity"),
                    ),
                )

    async def get_report(self, task_id: str) -> dict:
        async with self.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT order_no, title, content_md, status FROM report_sections "
                "WHERE task_id = %s ORDER BY order_no",
                (task_id,),
            )
            sections = await cursor.fetchall()
            cursor = await conn.execute(
                "SELECT id, section_id, text, qualifiers_json FROM claims "
                "WHERE task_id = %s ORDER BY created_at",
                (task_id,),
            )
            claims = await cursor.fetchall()
            cursor = await conn.execute(
                "SELECT l.* FROM ledger_entries l "
                "JOIN claims c ON c.id = l.claim_id WHERE c.task_id = %s",
                (task_id,),
            )
            ledger = await cursor.fetchall()
            cursor = await conn.execute(
                "SELECT * FROM conflicts WHERE task_id = %s ORDER BY created_at",
                (task_id,),
            )
            conflicts = await cursor.fetchall()
            cursor = await conn.execute(
                "SELECT DISTINCT e.id, e.video_id, e.start_ms, e.end_ms, e.modality, e.text, "
                "v.url, v.source, v.source_video_id "
                "FROM evidence_packets e JOIN videos v ON v.id = e.video_id "
                "WHERE e.task_id = %s AND e.id IN ("
                "  SELECT l.evidence_id FROM ledger_entries l "
                "  JOIN claims c ON c.id = l.claim_id "
                "  WHERE c.task_id = %s AND l.evidence_id IS NOT NULL"
                ") ORDER BY e.start_ms",
                (task_id, task_id),
            )
            evidence_rows = await cursor.fetchall()
        return {
            "task_id": task_id,
            "sections": [
                {
                    "order_no": row["order_no"],
                    "title": row["title"],
                    "content_md": row["content_md"],
                    "status": row["status"],
                }
                for row in sections
            ],
            "claims": [
                {
                    "claim_id": str(row["id"]),
                    "section_id": row["section_id"],
                    "text": row["text"],
                    "qualifiers": row["qualifiers_json"],
                }
                for row in claims
            ],
            "ledger": [
                {
                    "claim_id": str(row["claim_id"]),
                    "evidence_id": str(row["evidence_id"]) if row["evidence_id"] else None,
                    "support_relation": row["support_relation"],
                    "trust_score": row["trust_score"],
                    "conflict_type": row["conflict_type"],
                    "rationale": row["rationale"],
                    "verified": row["verified"],
                }
                for row in ledger
            ],
            "citations": [
                {
                    "evidence_id": str(row["id"]),
                    "video_id": str(row["video_id"]),
                    "start_ms": row["start_ms"],
                    "end_ms": row["end_ms"],
                    "modality": row["modality"],
                    "quote": row["text"],
                    "url": row["url"],
                    "source": row["source"],
                    "source_video_id": row["source_video_id"],
                }
                for row in evidence_rows
            ],
            "conflicts": [
                {
                    "conflict_id": str(row["id"]),
                    "type": row["type"],
                    "claim_ids": row["claim_ids"],
                    "evidence_ids": row["evidence_ids"],
                    "summary": row["summary"],
                    "similarity": row["similarity"],
                }
                for row in conflicts
            ],
        }
