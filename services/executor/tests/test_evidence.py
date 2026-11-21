from app.contracts import EvidenceSourceType, Modality, VideoRef, VideoSourceName
from app.ingest.evidence import (
    OCRBlock,
    build_evidence,
    build_ocr_evidence,
    content_hash,
    merge_ocr_blocks,
)
from app.ingest.subtitles import SubtitleCue


def _ref() -> VideoRef:
    return VideoRef(
        source=VideoSourceName.BILIBILI,
        source_video_id="BV1xx411c7mD",
        page=2,
        url="https://www.bilibili.com/video/BV1xx411c7mD?p=2",
    )


def test_build_evidence_and_dedupe():
    cues = [
        SubtitleCue(1000, 2000, "  同一句  "),
        SubtitleCue(1000, 2000, "同一句"),
        SubtitleCue(3000, 4000, "第二句"),
    ]
    packets = build_evidence(
        cues,
        task_id="task-1",
        video_id="video-1",
        ref=_ref(),
        source_type=EvidenceSourceType.NATIVE_SUB,
    )
    assert len(packets) == 2
    assert packets[0].text == "同一句"
    assert packets[0].modality == Modality.ASR
    assert packets[0].content_hash.startswith("sha256:")
    assert packets[0].video_id == "video-1"


def test_content_hash_is_stable_and_page_sensitive():
    first = content_hash(_ref(), 1000, 2000, "同一句")
    second = content_hash(_ref(), 1000, 2000, "同一句")
    assert first == second
    other_page = _ref().model_copy(update={"page": 3})
    assert content_hash(other_page, 1000, 2000, "同一句") != first


def test_merge_ocr_blocks():
    blocks = [
        OCRBlock(1000, 2000, "市场规模", 0.8, "frame1.jpg"),
        OCRBlock(2500, 3000, "市场规模", 0.9, "frame2.jpg"),
        OCRBlock(8000, 9000, "市场规模", 0.9, "frame3.jpg"),
        OCRBlock(3000, 4000, "增速", 0.9, "frame4.jpg"),
    ]
    merged = merge_ocr_blocks(blocks, max_gap_ms=1000)
    assert len(merged) == 3
    assert merged[0].start_ms == 1000
    assert merged[0].end_ms == 3000
    assert merged[0].confidence == 0.9
    assert merged[0].frame_ref == "frame1.jpg"


def test_build_ocr_evidence():
    blocks = [OCRBlock(1000, 2000, "市场规模", 0.8, "frame1.jpg")]
    packets = build_ocr_evidence(
        blocks, task_id="task-1", video_id="video-1", ref=_ref()
    )
    assert len(packets) == 1
    assert packets[0].modality == Modality.OCR
    assert packets[0].source_type == EvidenceSourceType.OCR
    assert packets[0].frame_ref == "frame1.jpg"
