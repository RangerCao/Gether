"""P3 研报节点 Prompt。"""

from __future__ import annotations

from typing import Any

PLANNER_SYSTEM = """你是深度研报大纲规划器。根据用户问题与证据摘要，规划 3-6 个章节。
只输出 JSON：{"sections":[{"id":"s1","title":"...","points":["..."]}]}。
章节必须直接回答用户问题，并覆盖不同视频之间的共识与分歧，不要编造证据中没有的信息。
如果问题是决策型（应不应该/该不该/要不要），章节必须是：结论、支持理由、反对理由、成本与风险、适用人群、最终建议；不要生成“如何挑选/验车/检查步骤”教程。"""

WRITER_SYSTEM = """你是中文深度研报写作者。根据给定证据块写一个章节。
硬性要求：
1. 每个事实句末尾必须引用证据，格式为 [ev:<evidence_id>]；
2. 只能引用证据中列出的 evidence_id，不得编造；
3. 不得把不同条件的观点合并成共识；
4. 只写当前章节的标题与要点，不要覆盖其他章节的主题，不要重复其他章节内容；
5. 输出 Markdown 正文，不要输出章节标题，不要输出 JSON。
如果证据不足，明确写“现有证据不足”，不要补写。"""

CRITIC_SYSTEM = """你是证据审查员。判断给定证据是否足以支撑章节主题。
只输出 JSON：{"supported": true/false, "reason": "...", "missing": ["..."]}。
不要因为证据少就放行，也不要要求超出章节主题的证据。"""

SINGLE_WRITER_SYSTEM = """你是单视频研报写作者。你的读者没时间看完整视频，需要一份忠于原片、可回查的研报章节。
硬性要求：
1. 只依据给定证据写作，不得引入视频之外的知识、常识补全或外部数据；
2. 每个事实句末尾必须引用证据，格式为 [ev:<evidence_id>]，只能引用证据中出现的 id；
3. 明确区分“作者主张”与“可核实事实”：作者的个人判断写“作者认为/作者判断”，不要写成定论；
4. 按证据的时间顺序叙述，涉及具体阶段时保留时间点（如 12:30）；
5. 只写当前章节的标题与要点，不要重复其他章节内容；
6. 输出 Markdown 正文，不要输出章节标题，不要输出 JSON；
7. 证据不足时明确写“视频中未提供相关证据”，不要补写。"""


def planner_user(task_id: str, evidence: list[dict[str, Any]], question: str = "") -> str:
    lines = []
    for item in evidence[:80]:
        lines.append(
            f"- [{item.get('modality')}] {item.get('text', '')[:220]}"
        )
    return f"用户问题：{question}\n任务：{task_id}\n证据摘要：\n" + "\n".join(lines)


def writer_user(
    section: dict[str, Any], contexts: list[dict[str, Any]], question: str = ""
) -> str:
    lines = []
    for index, chunk in enumerate(contexts, start=1):
        evidence_ids = ", ".join(chunk.get("evidence_ids", []))
        lines.append(
            f"[证据{index}] video={chunk.get('video_id')} "
            f"time={chunk.get('start_ms')}-{chunk.get('end_ms')}ms\n"
            f"evidence_ids: {evidence_ids}\n"
            f"text: {chunk.get('text', '')}"
        )
    points = "\n".join(f"- {point}" for point in section.get("points", []))
    return (
        f"必须回答的用户问题：{question}\n"
        f"当前章节：{section.get('title')}\n"
        f"要点：\n{points}\n\n"
        f"证据：\n" + "\n\n".join(lines)
    )


def critic_user(section: dict[str, Any], contexts: list[dict[str, Any]]) -> str:
    text = "\n".join(chunk.get("text", "") for chunk in contexts)
    return f"章节：{section.get('title')}\n证据：\n{text[:6000]}"


def _clock(ms: int) -> str:
    total = max(int(ms), 0) // 1000
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def single_writer_user(
    section: dict[str, Any],
    contexts: list[dict[str, Any]],
    question: str = "",
    video_title: str = "",
) -> str:
    lines = []
    for index, chunk in enumerate(contexts, start=1):
        evidence_ids = ", ".join(chunk.get("evidence_ids", []))
        lines.append(
            f"[证据{index}] 时间 {_clock(chunk.get('start_ms', 0))}-"
            f"{_clock(chunk.get('end_ms', 0))}\n"
            f"evidence_ids: {evidence_ids}\n"
            f"text: {chunk.get('text', '')}"
        )
    points = "\n".join(f"- {point}" for point in section.get("points", []))
    return (
        f"视频标题：{video_title or '未命名视频'}\n"
        f"用户关注的问题：{question or '（未指定，按章节要点写）'}\n"
        f"当前章节：{section.get('title')}\n"
        f"要点：\n{points}\n\n"
        f"证据（按时间顺序）：\n" + "\n\n".join(lines)
    )
