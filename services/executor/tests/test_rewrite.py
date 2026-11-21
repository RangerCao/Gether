import pytest

from app.discovery.rewrite import rewrite_question
from app.llm.base import LLMRole


class FakeLLM:
    async def complete(self, role, messages, *, json_mode=False, max_tokens=None):
        assert role == LLMRole.QUERY_REWRITE
        return (
            '{"rewritten":"人生第一辆车 二手车还是新车 优缺点 成本 风险",'
            '"keywords":["第一辆车","二手车","新车"],'
            '"intent":"decision","must_have":["成本","风险"],"avoid":["教程","验车"]}'
        )

    async def stream(self, role, messages, *, max_tokens=None):
        if False:
            yield ""

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_rewrite_question_decision():
    result = await rewrite_question(FakeLLM(), "人生第一辆车应不应该买二手？")
    assert "二手车还是新车" in result.rewritten
    assert result.intent == "decision"
    assert "验车" in result.avoid


@pytest.mark.asyncio
async def test_rewrite_fallback_for_decision_without_llm():
    class Broken:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("no llm")

    result = await rewrite_question(Broken(), "人生第一辆车应不应该买二手？")
    assert result.intent == "decision"
    assert "优缺点" in result.rewritten
    assert "教程" in result.avoid
