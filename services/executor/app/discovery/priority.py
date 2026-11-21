"""自动判断排序倾向：相关性 / 权威性 / 时效性 / 均衡。"""

from __future__ import annotations

from dataclasses import dataclass

PRIORITY_WEIGHTS: dict[str, dict[str, float]] = {
    "relevance": {"m": 0.60, "n": 0.25, "p": 0.15},
    "authority": {"m": 0.25, "n": 0.60, "p": 0.15},
    "freshness": {"m": 0.25, "n": 0.15, "p": 0.60},
    "balanced": {"m": 0.40, "n": 0.30, "p": 0.30},
}

PRIORITY_LABELS: dict[str, str] = {
    "relevance": "相关性优先",
    "authority": "权威性优先",
    "freshness": "时效性优先",
    "balanced": "均衡",
}

FRESHNESS_TERMS = (
    "最新", "最近", "今年", "本季度", "当前", "现在", "近期",
    "2025", "2026", "政策", "趋势", "动态", "前沿", "发布",
)
AUTHORITY_TERMS = (
    "权威", "官方", "专家", "教授", "研究", "论文", "数据",
    "报告", "机构", "学术", "大厂", "可信", "专业",
)
RELEVANCE_TERMS = (
    "实测", "体验", "对比", "优缺点", "怎么选", "真实",
    "案例", "翻车", "避坑", "使用感受", "是否", "真的", "效果",
    "影响", "成本", "价格", "团队", "适用性", "性价比",
)


@dataclass(frozen=True)
class RankingPriority:
    name: str
    reason: str
    weights: dict[str, float]

    def as_dict(self) -> dict:
        return {
            "priority": self.name,
            "label": PRIORITY_LABELS[self.name],
            "weights": dict(self.weights),
            "reason": self.reason,
        }


def weights_for(priority: str) -> dict[str, float]:
    return dict(PRIORITY_WEIGHTS.get(priority, PRIORITY_WEIGHTS["balanced"]))


def infer_priority(
    question: str,
    intent: str = "general",
    *,
    llm_priority: str | None = None,
    llm_reason: str | None = None,
) -> RankingPriority:
    rule_name, rule_reason = _rule_priority(question, intent)
    # 明确的规则信号优先，避免 LLM 对所有问题都返回 balanced。
    if rule_name != "balanced":
        reason = llm_reason.strip() if llm_priority == rule_name and llm_reason else rule_reason
        return RankingPriority(name=rule_name, reason=reason, weights=weights_for(rule_name))
    if llm_priority in PRIORITY_WEIGHTS:
        return RankingPriority(
            name=llm_priority,
            reason=(llm_reason or "由问题语义判断").strip(),
            weights=weights_for(llm_priority),
        )
    return RankingPriority(name=rule_name, reason=rule_reason, weights=weights_for(rule_name))


def _rule_priority(question: str, intent: str) -> tuple[str, str]:
    text = question.lower()
    freshness = sum(1 for term in FRESHNESS_TERMS if term in text)
    authority = sum(1 for term in AUTHORITY_TERMS if term in text)
    relevance = sum(1 for term in RELEVANCE_TERMS if term in text)

    if freshness >= 2 and authority >= 2:
        return "balanced", "同时检测到时效性与权威性信号，采用均衡排序"
    if freshness >= 2:
        return "freshness", "检测到时效性信号，优先近期内容"
    if authority >= 2:
        return "authority", "检测到权威性信号，优先官方/研究/专业来源"
    if relevance >= 1 or intent in {"evaluation", "decision"}:
        return "relevance", "问题关注真实效果/取舍，优先相关性"
    return "balanced", "没有明显倾向，采用均衡排序"
