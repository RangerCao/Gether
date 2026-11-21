import os
import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.ffmpeg import find_ffmpeg
from app.ingest.ocr import OCRText
from app.ingest.pipeline import IngestPipeline
from app.ingest.sources.local import LocalSource

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("GETHER_TEST_MEDIA"), reason="需要 GETHER_TEST_MEDIA=1"),
]


def _make_video(path: Path, seconds: int = 3) -> None:
    subprocess.run(
        [
            find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=320x240:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


class FakeOCR:
    def __init__(self, text: str = "幻灯片上的文字") -> None:
        self.text = text
        self.calls = 0

    async def recognize(self, image_path):
        self.calls += 1
        return [OCRText(text=self.text, confidence=0.95)]


@pytest.mark.asyncio
async def test_ffmpeg_frames_and_local_ocr_pipeline(tmp_path: Path):
    video = tmp_path / "slides.mp4"
    _make_video(video, seconds=3)

    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        app_env="local",
        ocr_enabled=True,
        ocr_provider="rapidocr",
        asr_provider="none",
        prefer_native_subtitle=False,
        frame_extract_mode="interval",
        frame_interval_seconds=1,
        frame_max_per_video=10,
        data_dir=str(tmp_path / "data"),
    )
    ref = await LocalSource().normalize(video.as_uri())
    ocr = FakeOCR()
    pipeline = IngestPipeline(settings, ocr=ocr)

    result = await pipeline.parse_video(task_id="t1", video_id="v1", ref=ref)
    assert result.stats["ocr_frames"] >= 2
    assert ocr.calls >= 2
    assert len(result.evidence) == 1
    assert result.evidence[0].modality.value == "ocr"
    assert result.evidence[0].text == "幻灯片上的文字"
    assert result.evidence[0].end_ms > result.evidence[0].start_ms


@pytest.mark.asyncio
async def test_rapidocr_reads_synthetic_image(tmp_path: Path):
    pytest.importorskip("rapidocr_onnxruntime")
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from app.ingest.ocr import RapidOCRProvider

    image = tmp_path / "ocr.png"
    canvas = np.full((400, 1000, 3), 255, dtype=np.uint8)
    cv2.putText(canvas, "GETTER 2026 EVIDENCE", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 5)
    cv2.imwrite(str(image), canvas)

    texts = await RapidOCRProvider(min_confidence=0.5).recognize(image)
    joined = " ".join(item.text for item in texts)
    assert "GETTER" in joined.upper()
    assert "EVIDENCE" in joined.upper()
