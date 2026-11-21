from app.contracts import EvidencePacket, EvidenceSourceType
from app.index.chunking import chunk_evidence


def _packet(index: int, video: str, text: str, start: int | None = None) -> EvidencePacket:
    start = start if start is not None else index * 1000
    return EvidencePacket(
        task_id="t1",
        video_id=video,
        modality="asr",
        text=text,
        start_ms=start,
        end_ms=start + 900,
        source_type=EvidenceSourceType.WHISPER,
        content_hash=f"h{index}",
    )


def test_chunking_merges_consecutive_evidence():
    evidence = [_packet(i, "v1", f"第{i}句" + "内容" * 20) for i in range(8)]
    chunks = chunk_evidence(evidence, target_chars=120, max_chars=180, overlap_chars=20)
    assert len(chunks) >= 3
    assert all(chunk.text for chunk in chunks)
    assert chunks[0].start_ms == 0
    # 不产生只包含 overlap 的重复尾块
    assert all(len(chunk.evidence_ids) >= 1 for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_chunking_does_not_mix_videos():
    evidence = [_packet(0, "v1", "视频一内容"), _packet(1, "v2", "视频二内容")]
    chunks = chunk_evidence(evidence)
    assert len(chunks) == 2
    assert {chunk.video_id for chunk in chunks} == {"v1", "v2"}


def test_long_evidence_is_split_with_timestamps():
    evidence = [_packet(0, "v1", "长文本" * 200, start=0)]
    chunks = chunk_evidence(evidence, target_chars=120, max_chars=180, overlap_chars=30)
    assert len(chunks) > 1
    assert chunks[0].start_ms == 0
    assert chunks[-1].end_ms == 900
    assert all(len(chunk.text) <= 180 for chunk in chunks)
