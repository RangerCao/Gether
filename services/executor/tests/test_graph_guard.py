from app.graph.guard import (
    apply_conflicts,
    build_ledger,
    claims_from_drafts,
    detect_conflicts,
    enforce_citations,
)

E1 = "11111111-1111-1111-1111-111111111111"
E2 = "22222222-2222-2222-2222-222222222222"


def _map():
    return {
        E1: {"video_id": "v1", "text": "增速将达到 10%", "modality": "asr"},
        E2: {"video_id": "v2", "text": "增速将下降到 3%", "modality": "asr"},
    }


def test_claims_and_ledger_with_valid_and_invalid_citations():
    claims, stats = claims_from_drafts(
        {"s1": f"增速将达到 10% [ev:{E1}]。无效引用 [ev:deadbeef-dead-dead-dead-deaddeadbeef]。"},
        _map(),
    )
    assert len(claims) == 2
    assert stats["citations_valid"] == 1
    assert stats["citations_invalid"] == 1
    ledger = build_ledger(claims, _map())
    assert any(entry["support_relation"] == "support" for entry in ledger)
    assert any(entry["support_relation"] == "no_evidence" for entry in ledger)


def test_numeric_conflict_between_videos():
    claims = [
        {
            "claim_id": "c1",
            "text": "该行业增速将达到 10%",
            "evidence_ids": [E1],
        },
        {
            "claim_id": "c2",
            "text": "该行业增速将下降到 3%",
            "evidence_ids": [E2],
        },
    ]
    conflicts = detect_conflicts(claims, _map())
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "numeric_mismatch"


def test_no_conflict_within_same_video():
    claims = [
        {"claim_id": "c1", "text": "该行业增速将达到 10%", "evidence_ids": [E1]},
        {"claim_id": "c2", "text": "该行业增速将下降到 3%", "evidence_ids": [E1]},
    ]
    assert detect_conflicts(claims, _map()) == []


def test_qualifier_time_diff_conflict():
    claims = [
        {
            "claim_id": "c1",
            "text": "2026 年该行业增速将达到 10%",
            "qualifiers": {"time": "2026年"},
            "evidence_ids": [E1],
        },
        {
            "claim_id": "c2",
            "text": "2027 年该行业增速将达到 10%",
            "qualifiers": {"time": "2027年"},
            "evidence_ids": [E2],
        },
    ]
    conflicts = detect_conflicts(claims, _map())
    assert conflicts
    assert conflicts[0]["type"] == "time_diff"


def test_apply_conflicts_adds_rebut_entries():
    claims = [
        {"claim_id": "c1", "text": "增速 10%", "evidence_ids": [E1]},
        {"claim_id": "c2", "text": "增速 3%", "evidence_ids": [E2]},
    ]
    ledger = build_ledger(claims, _map())
    conflicts = detect_conflicts(claims, _map())
    ledger = apply_conflicts(ledger, conflicts, claims)
    assert any(entry["support_relation"] == "rebut" for entry in ledger)
    assert any(entry["conflict_type"] == "numeric_mismatch" for entry in ledger)


def test_low_support_claim_is_unverified():
    claims, _ = claims_from_drafts(
        {"s1": f"完全无关的另一段论述 [ev:{E1}]。"},
        _map(),
    )
    assert claims[0]["verified"] is False
    ledger = build_ledger(claims, _map())
    assert ledger[0]["verified"] is False


def test_enforce_citations_removes_uncited_sentences():
    drafts = {
        "s1": (
            f"有证据的结论 [ev:{E1}]。没有证据的断言。"
            + chr(10) * 2
            + "**小标题**"
            + chr(10)
            + f"另有一条有证据的结论 [ev:{E2}]。"
        )
    }
    cleaned, stats = enforce_citations(drafts, {E1, E2})
    assert "没有证据的断言" not in cleaned["s1"]
    assert "有证据的结论" in cleaned["s1"]
    assert "另有一条有证据的结论" in cleaned["s1"]
    assert stats["sentences_removed"] == 1
