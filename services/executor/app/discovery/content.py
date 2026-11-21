"""视频内容类型与问题意图分类（规则版，可替换为 LLM）。"""

from __future__ import annotations

import re

CONTENT_RULES: dict[str, tuple[str, ...]] = {
    "tutorial": (
        "教程", "安装", "注册", "下载", "入门", "保姆", "手把手", "零基础",
        "从零", "白嫖", "免费", "激活", "续杯", "怎么用", "使用方法", "配置",
        "指南", "必看", "讲透", "学会", "教你", "步骤", "从零开始",
    ),
    "research": (
        "研究", "论文", "science", "科学", "实验", "数据", "报告", "基准",
        "benchmark", "调研", "统计",
    ),
    "official": (
        "官方", "发布会", "keynote", "universe", "官宣", "大会", "峰会",
        "开发者大会", "官方文档", "官方博客",
    ),
    "experience": (
        "实测", "测评", "体验", "对比", "踩坑", "翻车", "优缺点", "使用感受",
        "怎么选", "选哪个", "哪个好", "一次讲清楚", "横评", "效率翻倍", "效率提升",
    ),
    "opinion": (
        "锐评", "观点", "第一性", "趋势", "预测", "为什么", "思考", "解读",
        "分析",
    ),
    "news": ("发布", "更新", "官宣", "新闻", "融资", "开源"),
    "career": ("校招", "社招", "面试", "简历", "作品集", "求职", "招聘", "就业", "考点", "认证"),
}


def classify_content_types(title: str, description: str = "") -> list[str]:
    text = f"{title} {description}".lower()
    scores = {
        kind: sum(1 for term in terms if term in text)
        for kind, terms in CONTENT_RULES.items()
    }
    # “如何买/怎么安装/如何入门”是教程，但“怎么选/实测/对比”不是。
    howto = re.search(
        r"(如何|怎么)(买|选|安装|使用|配置|入门|做|学|申请|注册|下载)",
        text,
    )
    experience_signal = any(
        term in text for term in ("实测", "测评", "体验", "对比", "横评", "哪个好")
    )
    if howto and not experience_signal:
        scores["tutorial"] += 2
    ordered = sorted(
        (kind for kind, score in scores.items() if score > 0),
        key=lambda kind: scores[kind],
        reverse=True,
    )
    return ordered or ["other"]


def classify_content_type(title: str, description: str = "") -> str:
    return classify_content_types(title, description)[0]


def credibility_score(content_types: list[str], title: str = "") -> float:
    """内容可信度代理：研究/官方 > 新闻 > 观点 > 体验 > 教程。"""
    types = set(content_types)
    if "research" in types:
        return 1.0
    if "official" in types:
        return 0.90
    if "news" in types:
        return 0.65
    if "opinion" in types:
        return 0.45
    if "experience" in types:
        return 0.35
    if "career" in types or "tutorial" in types:
        return 0.10
    return 0.20 if title else 0.15


def classify_intent(question: str) -> str:
    text = question.lower()
    career = ("求职", "面试", "校招", "简历", "招聘", "就业")
    decision = ("应不应该", "该不该", "要不要", "值不值得", "值得买", "买不买", "第一辆")
    evaluation = (
        "为什么", "是否", "真的", "效果", "提升", "对比", "评估",
        "风险", "值得", "影响", "成本", "适用",
    )
    howto = ("如何", "怎么", "安装", "教程", "入门", "使用")
    if any(token in text for token in career):
        return "career"
    if any(token in text for token in decision):
        return "decision"
    if any(token in text for token in evaluation):
        return "evaluation"
    if any(token in text for token in howto):
        return "howto"
    return "general"
