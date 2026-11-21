import json

import pytest

from app.ingest.correction import LLMCorrector
from app.ingest.subtitles import SubtitleCue


class FakeLLM:
    def __init__(self, payload: dict | str) -> None:
        self.payload = payload
        self.calls = 0
        self.last_messages = None

    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        self.calls += 1
        self.last_messages = messages
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)

    async def stream(self, role, messages, *, max_tokens=None):
        if False:
            yield ""

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_correct_changes_text_and_keeps_timestamps():
    llm = FakeLLM(
        {"items": [{"i": 1, "text": "我今天晚上他们才把那个QQ账号给我注册好。"}]}
    )
    cues = [SubtitleCue(1000, 5000, "我今天晚上他们才把那个废物账号给我做好")]
    corrected, stats = await LLMCorrector(llm).correct(cues)
    assert corrected[0].start_ms == 1000
    assert corrected[0].end_ms == 5000
    assert "QQ账号" in corrected[0].text
    assert corrected[0].raw_text == "我今天晚上他们才把那个废物账号给我做好"
    assert stats.changed == 1
    assert stats.failed == 0


@pytest.mark.asyncio
async def test_count_mismatch_keeps_original():
    llm = FakeLLM({"items": []})
    cues = [SubtitleCue(1000, 2000, "原文")]
    corrected, stats = await LLMCorrector(llm).correct(cues)
    assert corrected == cues
    assert stats.failed == 1


@pytest.mark.asyncio
async def test_implausible_rewrite_rejected():
    llm = FakeLLM({"items": [{"i": 1, "text": "完全不同的一大段新内容" * 10}]})
    cues = [SubtitleCue(1000, 2000, "原文")]
    corrected, stats = await LLMCorrector(llm).correct(cues)
    assert corrected[0].text == "原文"
    assert stats.rejected == 1


@pytest.mark.asyncio
async def test_markdown_wrapped_json_parsed():
    llm = FakeLLM('```json\n{"items":[{"i":1,"text":"修正后的文本"}]}\n```')
    cues = [SubtitleCue(1000, 2000, "修正前的文本")]
    corrected, stats = await LLMCorrector(llm).correct(cues)
    assert corrected[0].text == "修正后的文本"
    assert stats.changed == 1


@pytest.mark.asyncio
async def test_batching():
    llm = FakeLLM({"items": [{"i": 1, "text": "文本一"}, {"i": 2, "text": "文本二"}]})
    cues = [SubtitleCue(1000, 2000, "文本一"), SubtitleCue(2000, 3000, "文本二")]
    _, stats = await LLMCorrector(llm, batch_size=1).correct(cues)
    assert llm.calls == 2
    assert stats.batches == 2
