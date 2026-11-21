from pathlib import Path

import pytest

from app.ingest.downloader import YtDlpDownloader
from app.ingest.subtitles import SubtitleCue, dedupe_cues, parse_subtitle

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("filename", ["sample.srt", "sample.vtt"])
def test_parse_sample_subtitles(filename: str):
    cues = parse_subtitle(FIXTURES / filename)
    assert len(cues) == 2
    assert cues[0].start_ms == 1000
    assert cues[0].end_ms == 4000
    assert cues[0].text == "第一句 证据"
    assert cues[1].text == "第二句 & 结论"


def test_parse_rolling_auto_subtitles():
    cues = parse_subtitle(FIXTURES / "rolling.vtt")
    texts = [cue.text for cue in cues]
    assert texts[0] == "大家好，今天聊聊行业"
    assert cues[0].start_ms == 1000
    assert cues[0].end_ms == 5000
    assert "第一，市场规模" in texts


def test_dedupe_exact_and_prefix_overlap():
    cues = [
        SubtitleCue(1000, 2000, "你好"),
        SubtitleCue(1500, 3000, "你好"),
        SubtitleCue(2000, 4000, "你好世界"),
        SubtitleCue(5000, 6000, "下一句"),
    ]
    merged = dedupe_cues(cues)
    assert len(merged) == 2
    assert merged[0] == SubtitleCue(1000, 4000, "你好世界")
    assert merged[1].text == "下一句"


def test_invalid_cues_dropped():
    cues = [SubtitleCue(3000, 3000, "坏数据"), SubtitleCue(4000, 5000, "  ")]
    assert dedupe_cues(cues) == []


def test_bilibili_subtitle_vtt_roundtrip(tmp_path: Path):
    text = YtDlpDownloader._cues_to_vtt([(1.2, 3.4, "第一句"), (4.0, 5.0, "第二句")])
    path = tmp_path / "bilibili.zh-CN.vtt"
    path.write_text(text, encoding="utf-8")
    cues = parse_subtitle(path)
    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in cues] == [
        (1200, 3400, "第一句"),
        (4000, 5000, "第二句"),
    ]


def test_bilibili_subtitle_language_selection():
    subtitles = [
        {"lan": "en-US", "subtitle_url": "//example.com/en.json"},
        {"lan": "ai-zh", "subtitle_url": "//example.com/zh.json"},
    ]
    selected = YtDlpDownloader._pick_bilibili_subtitle(
        subtitles, ["zh-Hans", "zh-CN", "zh", "ai-zh"]
    )
    assert selected is not None
    assert selected["lan"] == "ai-zh"
