"""把研究问题拆成检索 facet。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.discovery.content import classify_intent
from app.discovery.models import Facet
from app.llm.base import LLMClient, LLMRole, Message

PLANNER_SYSTEM = """你是视频研究检索规划器。把用户问题拆成 3-6 个可搜索的 facet。
只输出 JSON：
{"topic":{"entities":["..."],"must_have_any":["..."],"exclude_topics":["..."]},
 "facets":[{"id":"f1","label":"...","query":"...","stance":"positive|negative|neutral",
            "required_any":["..."],"exclude_any":["..."]}],
 "negative_terms":["教程","安装","免费"]}
要求：
1. 至少包含一个反方/风险/失败案例 facet（若问题适合评估）；
2. query 要像平台搜索词，而不是自然语言长句，并包含主题实体；
3. 如果问题是决策型（应不应该/该不该/要不要），facet 必须是：支持方、反对方、成本/折旧、风险、替代方案、适用人群；不要生成验车教程步骤；
4. required_any 是判断 facet 命中的主题锚点词；
5. exclude_any / exclude_topics 用于排除相邻领域；
6. negative_terms 只放当前问题相关的教程/广告/相邻领域词；
7. 不要编造实体，不要把所有问题都套用车/教程示例。"""


@dataclass(frozen=True)
class DiscoveryPlan:
    facets: list[Facet]
    negative_terms: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    must_have_any: list[str] = field(default_factory=list)
    exclude_topics: list[str] = field(default_factory=list)


async def plan_discovery(
    client: LLMClient,
    question: str,
    *,
    queries_per_facet: int = 2,
    entities: list[str] | None = None,
    must_have_any: list[str] | None = None,
    exclude_topics: list[str] | None = None,
) -> DiscoveryPlan:
    try:
        raw = await client.complete(
            LLMRole.PLANNER,
            [
                Message("system", PLANNER_SYSTEM),
                Message(
                    "user",
                    f"用户问题：{question}\n每个 facet 生成 1-{queries_per_facet} 个检索 query。",
                ),
            ],
            json_mode=True,
            max_tokens=1400,
        )
        data = _parse_json(raw)
        facets = []
        for index, item in enumerate(data.get("facets") or [], start=1):
            if not isinstance(item, dict):
                continue
            raw_query = item.get("query") or item.get("label") or ""
            if isinstance(raw_query, list):
                query = " ".join(str(part).strip() for part in raw_query if str(part).strip())
            else:
                query = str(raw_query).strip()
            if not query:
                continue
            facets.append(
                Facet(
                    id=str(item.get("id") or f"f{index}"),
                    label=str(item.get("label") or query),
                    query=query,
                    stance=str(item.get("stance") or "neutral"),
                    required_any=_strings(item.get("required_any")),
                    exclude_any=_strings(item.get("exclude_any")),
                )
            )
        if facets:
            negative = [
                str(item).strip()
                for item in data.get("negative_terms") or []
                if str(item).strip()
            ]
            topic = data.get("topic") if isinstance(data.get("topic"), dict) else {}
            return DiscoveryPlan(
                facets=facets[:6],
                negative_terms=negative[:16],
                entities=_strings(topic.get("entities")) or list(entities or []),
                must_have_any=_strings(topic.get("must_have_any")) or list(must_have_any or []),
                exclude_topics=_strings(topic.get("exclude_topics")) or list(exclude_topics or []),
            )
    except Exception:
        pass
    return _fallback(
        question,
        entities=entities,
        must_have_any=must_have_any,
        exclude_topics=exclude_topics,
    )


async def plan_facets(
    client: LLMClient,
    question: str,
    *,
    queries_per_facet: int = 2,
) -> list[Facet]:
    plan = await plan_discovery(client, question, queries_per_facet=queries_per_facet)
    return plan.facets


def build_queries(facets: list[Facet], question: str, per_facet: int = 2) -> list[str]:
    queries: list[str] = []
    for facet in facets:
        queries.append(facet.query)
        if per_facet > 1 and facet.label != facet.query:
            queries.append(f"{facet.label} {question}")
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))


def _fallback(
    question: str,
    *,
    entities: list[str] | None = None,
    must_have_any: list[str] | None = None,
    exclude_topics: list[str] | None = None,
) -> DiscoveryPlan:
    intent = classify_intent(question)
    topic_entities = list(entities or [])
    anchors = list(must_have_any or [])
    excludes = list(exclude_topics or [])
    if intent == "decision":
        facets = [
            Facet("f1", "支持理由", f"{question} 优点 适合 真实体验", "positive"),
            Facet("f2", "反对理由", f"{question} 缺点 风险 后悔", "negative"),
            Facet("f3", "成本与代价", f"{question} 成本 价格 长期费用", "neutral"),
            Facet("f4", "替代方案", f"{question} 替代方案 对比 怎么选", "neutral"),
            Facet("f5", "适用人群", f"{question} 适合谁 不适合 真实案例", "neutral"),
        ]
        need = ["教程", "安装", "选购", "检查清单", "购买流程"]
        if "车" in question:
            need += ["验车", "检测", "砍价"]
        return DiscoveryPlan(
            facets=facets,
            negative_terms=list(dict.fromkeys(need)),
            entities=topic_entities,
            must_have_any=anchors,
            exclude_topics=excludes,
        )
    facets = [
        Facet(
            "f1",
            "核心问题与实测",
            question,
            "neutral",
            required_any=anchors,
            exclude_any=excludes,
        )
    ]
    if intent == "evaluation":
        facets.append(
            Facet(
                "f2",
                "风险与反方观点",
                f"{question} 风险 缺点 失败 案例",
                "negative",
                required_any=anchors,
                exclude_any=excludes,
            )
        )
    negative = ["教程", "安装", "注册", "免费", "白嫖", "入门", "保姆"] if intent == "evaluation" else []
    return DiscoveryPlan(
        facets=facets,
        negative_terms=negative,
        entities=topic_entities,
        must_have_any=anchors,
        exclude_topics=excludes,
    )


def _strings(value) -> list[str]:
    return [str(item).strip() for item in (value or []) if str(item).strip()]

def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}
        return json.loads(match.group(0))
