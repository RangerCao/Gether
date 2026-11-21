import json

import pytest

from app.llm.base import LLMRole
from app.llm.mock import MockLLMClient


@pytest.mark.asyncio
async def test_mock_planner_json():
    client = MockLLMClient()
    raw = await client.complete(LLMRole.PLANNER, [], json_mode=True)
    data = json.loads(raw)
    assert len(data["sections"]) == 3
    assert data["sections"][0]["id"] == "s1"


@pytest.mark.asyncio
async def test_mock_stream_joins_to_complete():
    client = MockLLMClient()
    chunks = [chunk async for chunk in client.stream(LLMRole.WRITER, [])]
    assert len(chunks) > 1
    assert "mock" in "".join(chunks)
