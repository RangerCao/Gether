from app.contracts import EvidencePacket, EvidenceSourceType, Modality, VideoRef, VideoSourceName
from app.ingest.evidence import OCRBlock, build_ocr_evidence, normalize_ocr_text
from app.ingest.fusion import fuse_ocr_asr


def _ref() -> VideoRef:
    return VideoRef(
        source=VideoSourceName.BILIBILI,
        source_video_id="BV1xx411c7mD",
        page=1,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )


def _packet(
    modality: Modality,
    start: int,
    end: int,
    text: str,
    *,
    confidence: float = 0.95,
    source_type: EvidenceSourceType = EvidenceSourceType.WHISPER,
) -> EvidencePacket:
    return EvidencePacket(
        task_id="t1",
        video_id="v1",
        modality=modality,
        text=text,
        start_ms=start,
        end_ms=end,
        confidence=confidence,
        source_type=source_type,
        content_hash=f"sha256:{modality.value}-{start}-{text}",
    )


def test_fuse_similar_ocr_and_asr():
    asr = [
        _packet(Modality.ASR, 35000, 37530, "我今天晚上他们才把就是那个"),
        _packet(Modality.ASR, 37950, 39170, "废物账号给我做好"),
    ]
    ocr = _packet(
        Modality.OCR,
        35000,
        40000,
        "我今天晚上他们才把就是那个 QQ账号给我注册好。",
        source_type=EvidenceSourceType.OCR,
    )
    fused, stats = fuse_ocr_asr(asr + [ocr])
    assert len(fused) == 1
    assert fused[0].text.startswith("我今天晚上")
    assert stats.fused_ocr_blocks == 1
    assert stats.consumed_asr_cues == 2


def test_unrelated_ocr_is_kept():
    asr = [_packet(Modality.ASR, 1000, 3000, "我们来看一下模型训练的结果")]
    ocr = _packet(
        Modality.OCR,
        1000,
        3000,
        "公司介绍",
        source_type=EvidenceSourceType.OCR,
    )
    fused, stats = fuse_ocr_asr(asr + [ocr])
    assert len(fused) == 2
    assert stats.fused_ocr_blocks == 0


def test_low_confidence_ocr_is_not_used_for_fusion():
    asr = [_packet(Modality.ASR, 1000, 3000, "我们今天讲三个重点")]
    ocr = _packet(
        Modality.OCR,
        1000,
        3000,
        "我们今天讲三个重点",
        confidence=0.4,
        source_type=EvidenceSourceType.OCR,
    )
    fused, stats = fuse_ocr_asr(asr + [ocr])
    assert len(fused) == 2
    assert stats.consumed_asr_cues == 0


def test_ocr_text_removes_spaces_between_cjk():
    assert normalize_ocr_text("大家 好 ， 今天 聊 行业") == "大家好，今天聊行业"
    packets = build_ocr_evidence(
        [OCRBlock(1000, 2000, "第一句 证据", 0.9, "frame.jpg")],
        task_id="t1",
        video_id="v1",
        ref=_ref(),
    )
    assert packets[0].text == "第一句证据"
