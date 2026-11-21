from pathlib import Path

import pytest

from app.contracts import EvidenceSourceType, Modality, VideoRef, VideoSourceName
from app.core.config import Settings
from app.ingest.downloader import SubtitleDownload
from app.ingest.frames import Frame
from app.ingest.ocr import OCRText
from app.ingest.pipeline import IngestError, IngestPipeline
from app.ingest.subtitles import SubtitleCue

FIXTURES = Path(__file__).parent / "fixtures"


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, llm_provider="mock", app_env="local", **kwargs)


def _ref() -> VideoRef:
    return VideoRef(
        source=VideoSourceName.BILIBILI,
        source_video_id="BV1xx411c7mD",
        page=1,
        url="https://www.bilibili.com/video/BV1xx411c7mD",
    )


class FakeDownloader:
    def __init__(
        self,
        subtitle: Path | None = None,
        audio: Path | None = None,
        video: Path | None = None,
    ) -> None:
        self.subtitle = subtitle
        self.audio = audio or Path("audio.wav")
        self.video = video or Path("video.mp4")
        self.subtitle_calls: list[list[str]] = []
        self.audio_called = 0
        self.video_called = 0

    async def download_subtitles(self, ref, langs):
        self.subtitle_calls.append(langs)
        if self.subtitle is None:
            return None
        return SubtitleDownload(path=self.subtitle, language=langs[0])

    async def download_audio(self, ref):
        self.audio_called += 1
        return self.audio

    async def download_video(self, ref, max_height=720):
        self.video_called += 1
        return self.video


class FakeASR:
    def __init__(self, cues: list[SubtitleCue] | None = None) -> None:
        self.cues = cues or []
        self.calls = 0

    async def transcribe(self, audio_path, language):
        self.calls += 1
        return self.cues


class FakeOCR:
    def __init__(self, text: str = "PPT 上的市场规模数据") -> None:
        self.text = text
        self.calls = 0

    async def recognize(self, image_path):
        self.calls += 1
        return [OCRText(text=self.text, confidence=0.95)]


class FakeFrames:
    async def extract(self, video_path, output_dir, **kwargs):
        return [
            Frame(path=Path("frame1.jpg"), start_ms=1000, end_ms=2000),
            Frame(path=Path("frame2.jpg"), start_ms=2000, end_ms=3000),
        ]


@pytest.mark.asyncio
async def test_subtitle_path_skips_asr():
    downloader = FakeDownloader(subtitle=FIXTURES / "sample.srt")
    asr = FakeASR()
    pipeline = IngestPipeline(
        _settings(prefer_native_subtitle=True, ocr_enabled=False),
        downloader=downloader,
        asr=asr,
        ocr=None,
        frames=None,
    )
    result = await pipeline.parse_video(task_id="t1", video_id="v1", ref=_ref())
    assert len(result.evidence) == 2
    assert all(item.source_type == EvidenceSourceType.NATIVE_SUB for item in result.evidence)
    assert asr.calls == 0
    assert downloader.audio_called == 0
    assert result.stats["subtitle_cues"] == 2
    assert downloader.subtitle_calls[0] == ["zh-Hans", "zh-CN", "zh", "ai-zh"]


@pytest.mark.asyncio
async def test_asr_fallback_when_no_subtitle():
    downloader = FakeDownloader(subtitle=None)
    asr = FakeASR([SubtitleCue(1000, 3000, "转写出来的一句话")])
    pipeline = IngestPipeline(
        _settings(ocr_enabled=False),
        downloader=downloader,
        asr=asr,
        ocr=None,
        frames=None,
    )
    result = await pipeline.parse_video(task_id="t1", video_id="v1", ref=_ref())
    assert len(result.evidence) == 1
    assert result.evidence[0].source_type == EvidenceSourceType.WHISPER
    assert asr.calls == 1


@pytest.mark.asyncio
async def test_ocr_evidence_added():
    downloader = FakeDownloader(
        subtitle=FIXTURES / "sample.srt",
        video=Path("video.mp4"),
    )
    asr = FakeASR()
    ocr = FakeOCR()
    pipeline = IngestPipeline(
        _settings(ocr_enabled=True, ocr_merge_gap_ms=1500),
        downloader=downloader,
        asr=asr,
        ocr=ocr,
        frames=FakeFrames(),
    )
    result = await pipeline.parse_video(task_id="t1", video_id="v1", ref=_ref())
    modalities = {item.modality for item in result.evidence}
    assert Modality.ASR in modalities
    assert Modality.OCR in modalities
    assert asr.calls == 0
    assert ocr.calls == 2
    assert downloader.video_called == 1


@pytest.mark.asyncio
async def test_no_evidence_raises():
    downloader = FakeDownloader(subtitle=None)
    pipeline = IngestPipeline(
        _settings(ocr_enabled=False),
        downloader=downloader,
        asr=FakeASR([]),
        ocr=None,
        frames=None,
    )
    with pytest.raises(IngestError) as exc:
        await pipeline.parse_video(task_id="t1", video_id="v1", ref=_ref())
    assert exc.value.code == "NO_EVIDENCE"


@pytest.mark.asyncio
async def test_progress_callback_receives_stages():
    downloader = FakeDownloader(subtitle=FIXTURES / "sample.vtt")
    events: list[tuple[str, int, int, str]] = []
    pipeline = IngestPipeline(
        _settings(ocr_enabled=False),
        downloader=downloader,
        asr=FakeASR(),
        ocr=None,
        frames=None,
    )
    await pipeline.parse_video(
        task_id="t1",
        video_id="v1",
        ref=_ref(),
        progress=lambda *args: events.append(args),
    )
    assert any(stage == "subtitle" for stage, *_ in events)


@pytest.mark.asyncio
async def test_youtube_ref_uses_same_pipeline():
    ref = VideoRef(
        source=VideoSourceName.YOUTUBE,
        source_video_id="dQw4w9WgXcQ",
        page=1,
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    downloader = FakeDownloader(subtitle=FIXTURES / "sample.vtt")
    pipeline = IngestPipeline(
        _settings(ocr_enabled=False),
        downloader=downloader,
        asr=FakeASR(),
        ocr=None,
        frames=None,
    )
    result = await pipeline.parse_video(task_id="t1", video_id="v1", ref=ref)
    assert len(result.evidence) == 2
    assert all(item.source_type == EvidenceSourceType.NATIVE_SUB for item in result.evidence)
    assert downloader.subtitle_calls[0] == ["zh-Hans", "zh", "en"]
