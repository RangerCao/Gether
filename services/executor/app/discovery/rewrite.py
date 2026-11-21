"""检索问题重写：把用户问题改写成适合视频搜索/向量匹配的查询。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.discovery.content import classify_intent
from app.discovery.priority import infer_priority
from app.llm.base import LLMClient, LLMRole, Message

REWRITE_SYSTEM = """你是视频检索查询重写器。把用户问题改写成适合平台搜索和向量匹配的中文查询。
只输出 JSON：
{"rewritten":"...","keywords":["..."],"entities":["..."],
 "intent":"decision|evaluation|howto|general",
 "must_have":["..."],"avoid":["..."],
 "priority":"relevance|authority|freshness|balanced",
 "priority_reason":"..."}
要求：
1. 保留用户原意、对象、时间和约束；
2. 决策型问题改写成对比型查询，覆盖优缺点、成本、风险、替代方案、真实体验；
3. 不要把“应不应该买”改写成“如何验车/教程”；
4. entities 放问题中的产品/公司/人物/领域实体，不要编造；
5. must_have 放判断视频是否在讲同一主题的锚点词；
6. avoid 放相邻领域和教程类词，不要把所有问题都套用车/教程示例；
7. priority 根据问题自动判断，只能选 relevance/authority/freshness/balanced。"""


@dataclass(frozen=True)
class RewrittenQuery:
    original: str
    rewritten: str
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    intent: str = "general"
    must_have: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    priority: str = "balanced"
    priority_reason: str = ""


async def rewrite_question(client: LLMClient, question: str) -> RewrittenQuery:
    question = question.strip()
    intent = classify_intent(question)
    try:
        raw = await client.complete(
            LLMRole.QUERY_REWRITE,
            [
                Message("system", REWRITE_SYSTEM),
                Message("user", f"用户问题：{question}"),
            ],
            json_mode=True,
            max_tokens=800,
        )
        data = _parse_json(raw)
        rewritten = str(data.get("rewritten") or "").strip()
        if rewritten:
            keywords = [str(item) for item in data.get("keywords") or [] if str(item).strip()]
            ranking = infer_priority(
                question,
                intent,
                llm_priority=str(data.get("priority") or ""),
                llm_reason=str(data.get("priority_reason") or ""),
            )
            return RewrittenQuery(
                original=question,
                rewritten=rewritten,
                keywords=keywords[:8],
                entities=[str(item) for item in data.get("entities") or [] if str(item).strip()][:8],
                intent=str(data.get("intent") or intent),
                must_have=[str(item) for item in data.get("must_have") or [] if str(item).strip()][:8],
                avoid=[str(item) for item in data.get("avoid") or [] if str(item).strip()][:12],
                priority=ranking.name,
                priority_reason=ranking.reason,
            )
    except Exception:
        pass
    ranking = infer_priority(question, intent)
    if intent == "decision":
        avoid = ["教程", "安装", "选购", "检查清单", "购买流程"]
        if "车" in question:
            avoid += ["验车", "检测", "砍价"]
        return RewrittenQuery(
            original=question,
            rewritten=f"{question} 优缺点 成本 风险 替代方案 真实体验",
            keywords=[],
            entities=[],
            intent="decision",
            avoid=list(dict.fromkeys(avoid)),
            priority=ranking.name,
            priority_reason=ranking.reason,
        )
    return RewrittenQuery(
        original=question,
        rewritten=question,
        intent=intent,
        priority=ranking.name,
        priority_reason=ranking.reason,
    )


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
