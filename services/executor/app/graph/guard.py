"""Claim 抽取、引用校验、Evidence Ledger 与确定性冲突规则。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

CITATION_RE = re.compile(r"\[ev:([0-9a-fA-F-]{8,})\]", re.IGNORECASE)
CITATION_BRACKET_RE = re.compile(r"\[ev:([^\]]+)\]", re.IGNORECASE)
ID_RE = re.compile(r"[0-9a-fA-F-]{8,}")
SENTENCE_RE = re.compile(r"[^。！？\n]+[。！？]?")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
NEGATIONS = ("不是", "不会", "没有", "并未", "不再", "不能", "无法", "否认", "不同意")
TIME_RE = re.compile(r"(20\d{2}年|今年|明年|去年|Q[1-4]|第[一二三四]季度|\d+月)")
CONDITION_RE = re.compile(r"(如果.{0,20}|若.{0,20}|前提是.{0,20}|在.{0,12}条件下)")
SCOPE_RE = re.compile(r"(中国|全球|国内|海外|该行业|行业|市场|公司)")


@dataclass(frozen=True)
class CitationStats:
    total: int = 0
    valid: int = 0
    invalid: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "citations_total": self.total,
            "citations_valid": self.valid,
            "citations_invalid": self.invalid,
        }


def expand_citation_brackets(text: str) -> str:
    """把模型常见的 `[ev:a, ev:b]` 拆成两个合法引用。

    模型经常在一个方括号里塞多个 id；CITATION_RE 只认单 id，
    不拆会让整句因“没有引用”被删除。
    """
    def replace(match: re.Match) -> str:
        parts = [
            re.sub(r"^ev:", "", part.strip(), flags=re.IGNORECASE)
            for part in re.split(r"[,，、;；\s]+", match.group(1))
            if part.strip()
        ]
        expanded = [f"[ev:{part}]" for part in parts if ID_RE.fullmatch(part)]
        return "".join(expanded) if expanded else match.group(0)

    return CITATION_BRACKET_RE.sub(replace, text)


def strip_invalid_citations(text: str, valid_ids: set[str]) -> tuple[str, CitationStats]:
    stats = CitationStats()
    text = expand_citation_brackets(text)

    def replace(match: re.Match) -> str:
        nonlocal stats
        stats = CitationStats(stats.total + 1, stats.valid, stats.invalid)
        evidence_id = match.group(1)
        if evidence_id in valid_ids:
            stats = CitationStats(stats.total, stats.valid + 1, stats.invalid)
            return match.group(0)
        stats = CitationStats(stats.total, stats.valid, stats.invalid + 1)
        return ""

    cleaned = CITATION_RE.sub(replace, text)
    return cleaned, stats


def extract_qualifiers(text: str) -> dict[str, str]:
    qualifiers: dict[str, str] = {}
    for key, pattern in (
        ("time", TIME_RE),
        ("condition", CONDITION_RE),
        ("scope", SCOPE_RE),
    ):
        match = pattern.search(text)
        if match:
            qualifiers[key] = match.group(1).strip()
    return qualifiers


def canonical_claim_key(claim: dict) -> str:
    qualifiers = claim.get("qualifiers") or {}
    parts = [f"{key}={qualifiers[key]}" for key in sorted(qualifiers)]
    return f"{_normalize(claim.get('text', ''))}|" + "&".join(parts)


def qualifier_diff(left: dict, right: dict) -> str | None:
    left_q = left.get("qualifiers") or {}
    right_q = right.get("qualifiers") or {}
    if left_q.get("time") and right_q.get("time") and left_q["time"] != right_q["time"]:
        return "time_diff"
    if (
        left_q.get("condition")
        and right_q.get("condition")
        and left_q["condition"] != right_q["condition"]
    ):
        return "condition_diff"
    return None


def _support_similarity(claim_text: str, evidence_text: str) -> float:
    return SequenceMatcher(None, _normalize(claim_text), _normalize(evidence_text)).ratio()


def claims_from_drafts(
    drafts: dict[str, str],
    evidence_map: dict[str, dict],
) -> tuple[list[dict], dict[str, int]]:
    claims: list[dict] = []
    totals = {"citations_total": 0, "citations_valid": 0, "citations_invalid": 0}
    valid_ids = set(evidence_map)
    for section_id, text in drafts.items():
        cleaned, stats = strip_invalid_citations(text, valid_ids)
        totals["citations_total"] += stats.total
        totals["citations_valid"] += stats.valid
        totals["citations_invalid"] += stats.invalid
        for segment in SENTENCE_RE.findall(cleaned):
            segment = segment.strip()
            if not segment:
                continue
            ids = [item for item in CITATION_RE.findall(segment) if item in valid_ids]
            plain = CITATION_RE.sub("", segment).strip()
            if not plain:
                continue
            support_scores = [
                _support_similarity(plain, evidence_map[evidence_id].get("text", ""))
                for evidence_id in ids
            ]
            support_score = max(support_scores, default=0.0)
            claims.append(
                {
                    "claim_id": str(uuid.uuid4()),
                    "section_id": section_id,
                    "text": plain,
                    "qualifiers": extract_qualifiers(plain),
                    "evidence_ids": list(dict.fromkeys(ids)),
                    "support_score": round(support_score, 4),
                    "verified": bool(ids) and support_score >= 0.25,
                }
            )
    return claims, totals


def enforce_citations(
    drafts: dict[str, str],
    valid_ids: set[str],
) -> tuple[dict[str, str], dict[str, int]]:
    """移除没有有效引用的正文句，避免无证据结论进入研报。

    保留 Markdown 标题/短过渡句；事实句必须有 [ev:id]。
    """
    cleaned_drafts: dict[str, str] = {}
    removed = 0
    kept = 0
    for section_id, text in drafts.items():
        cleaned, _ = strip_invalid_citations(text, valid_ids)
        output: list[str] = []
        for paragraph in cleaned.split(chr(10)):
            sentences = SENTENCE_RE.findall(paragraph)
            if not sentences:
                continue
            keep_sentences: list[str] = []
            for sentence in sentences:
                stripped = sentence.strip()
                if not stripped:
                    continue
                is_heading = stripped.startswith("#") or (
                    stripped.startswith("**") and stripped.endswith("**")
                )
                has_citation = bool(CITATION_RE.search(stripped))
                if is_heading or has_citation:
                    keep_sentences.append(sentence)
                    kept += 1
                else:
                    removed += 1
            if keep_sentences:
                output.append("".join(keep_sentences).strip())
        body = chr(10).join(output).strip()
        cleaned_drafts[section_id] = (
            body or "现有证据不足，无法形成有依据的结论。"
        )
    return cleaned_drafts, {"sentences_kept": kept, "sentences_removed": removed}


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?；;：:（）()\[\]【】]", "", text)


def _evidence_videos(claim: dict, evidence_map: dict[str, dict]) -> set[str]:
    return {
        str(evidence_map[evidence_id].get("video_id"))
        for evidence_id in claim.get("evidence_ids", [])
        if evidence_id in evidence_map
    }


def build_ledger(claims: list[dict], evidence_map: dict[str, dict]) -> list[dict]:
    entries: list[dict] = []
    for claim in claims:
        ids = [item for item in claim.get("evidence_ids", []) if item in evidence_map]
        if not ids:
            entries.append(
                {
                    "claim_id": claim["claim_id"],
                    "evidence_id": None,
                    "support_relation": "no_evidence",
                    "trust_score": 0.0,
                    "conflict_type": "none",
                    "verified": False,
                    "rationale": "未绑定有效证据",
                }
            )
            continue
        for evidence_id in ids:
            entries.append(
                {
                    "claim_id": claim["claim_id"],
                    "evidence_id": evidence_id,
                    "support_relation": "support",
                    "trust_score": float(claim.get("support_score", 0.8)),
                    "conflict_type": "none",
                    "verified": bool(claim.get("verified", False)),
                    "rationale": "引用存在，文本相似度校验通过"
                    if claim.get("verified")
                    else "引用存在，但 claim 与证据文本相似度偏低",
                }
            )
    return entries


def detect_conflicts(claims: list[dict], evidence_map: dict[str, dict]) -> list[dict]:
    conflicts: list[dict] = []
    for left_index, left in enumerate(claims):
        for right in claims[left_index + 1 :]:
            left_videos = _evidence_videos(left, evidence_map)
            right_videos = _evidence_videos(right, evidence_map)
            if not left_videos or not right_videos or left_videos & right_videos:
                continue
            similarity = SequenceMatcher(
                None, _normalize(left["text"]), _normalize(right["text"])
            ).ratio()
            if similarity < 0.55:
                continue
            left_numbers = set(NUMBER_RE.findall(left["text"]))
            right_numbers = set(NUMBER_RE.findall(right["text"]))
            left_neg = any(token in left["text"] for token in NEGATIONS)
            right_neg = any(token in right["text"] for token in NEGATIONS)
            conflict_type = qualifier_diff(left, right)
            if conflict_type is None and left_numbers and right_numbers and left_numbers != right_numbers:
                conflict_type = "numeric_mismatch"
            elif conflict_type is None and left_neg != right_neg:
                conflict_type = "direct_contradiction"
            if conflict_type is None:
                continue
            conflicts.append(
                {
                    "conflict_id": str(uuid.uuid4()),
                    "type": conflict_type,
                    "claim_ids": [left["claim_id"], right["claim_id"]],
                    "evidence_ids": list(
                        dict.fromkeys(
                            left.get("evidence_ids", []) + right.get("evidence_ids", [])
                        )
                    ),
                    "summary": f"{left['text'][:80]} ↔ {right['text'][:80]}",
                    "similarity": round(similarity, 3),
                }
            )
    return conflicts


def apply_conflicts(
    ledger: list[dict], conflicts: list[dict], claims: list[dict]
) -> list[dict]:
    by_id = {claim["claim_id"]: claim for claim in claims}
    existing = {(entry["claim_id"], entry.get("evidence_id")) for entry in ledger}
    for conflict in conflicts:
        left_id, right_id = conflict["claim_ids"]
        left = by_id.get(left_id)
        right = by_id.get(right_id)
        if not left or not right:
            continue
        relation = "rebut" if conflict["type"] in {"numeric_mismatch", "direct_contradiction"} else "mixed"
        for claim, other in ((left, right), (right, left)):
            for evidence_id in other.get("evidence_ids", []):
                key = (claim["claim_id"], evidence_id)
                if key in existing:
                    continue
                existing.add(key)
                ledger.append(
                    {
                        "claim_id": claim["claim_id"],
                        "evidence_id": evidence_id,
                        "support_relation": relation,
                        "trust_score": 0.6,
                        "conflict_type": conflict["type"],
                        "verified": True,
                        "rationale": conflict.get("summary"),
                    }
                )
    return ledger


def assemble_report(outline: list[dict], drafts: dict[str, str]) -> str:
    parts = []
    for section in outline:
        section_id = section.get("id")
        title = section.get("title", section_id)
        parts.append(f"## {title}\n\n{drafts.get(section_id, '').strip()}")
    return "\n\n".join(parts).strip() + "\n"
