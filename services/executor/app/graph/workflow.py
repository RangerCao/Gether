"""LangGraph 研报工作流：Planner → HITL → Retriever → Critic → Writer → 裁决。"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.config import Settings
from app.graph.guard import (
    apply_conflicts,
    assemble_report,
    build_ledger,
    claims_from_drafts,
    detect_conflicts,
    enforce_citations,
)
from app.graph.prompts import (
    CRITIC_SYSTEM,
    PLANNER_SYSTEM,
    SINGLE_WRITER_SYSTEM,
    WRITER_SYSTEM,
    critic_user,
    planner_user,
    single_writer_user,
    writer_user,
)
from app.graph.rebind import EvidenceRebinder
from app.graph.single import (
    assemble_single_markdown,
    build_single_outline,
    is_single_video,
    single_gaps,
    single_quality,
    timeline_windows,
    window_as_context,
)
from app.graph.state import ReportState
from app.index.service import IndexNotFound, IndexService
from app.llm.base import LLMClient, LLMRole, Message
from app.repository import TaskRepository

logger = logging.getLogger(__name__)


class ReportWorkflow:
    def __init__(
        self,
        *,
        llm: LLMClient,
        index_service: IndexService,
        task_repository: TaskRepository,
        settings: Settings,
    ) -> None:
        self.llm = llm
        self.index_service = index_service
        self.task_repository = task_repository
        self.settings = settings
        self.rebinder = (
            EvidenceRebinder(
                model_name=settings.report_reranker_model,
                cache_dir=settings.report_reranker_cache_dir,
                threshold=settings.report_rebind_threshold,
                lexical_threshold=settings.report_rebind_lexical_threshold,
            )
            if settings.report_rebind_enabled
            else None
        )

    async def planner(self, state: ReportState) -> dict:
        task_id = state["task_id"]
        if is_single_video(state.get("report_kind")):
            # 单视频用固定六段式，保证结构稳定、少一次 LLM 调用；
            # 用户带问题时追加“针对你的问题”章节。
            outline = build_single_outline(
                state.get("report_question") or ""
            )
            return {"outline": outline, "warnings": state.get("warnings", [])}
        evidence = await self.task_repository.list_evidence(task_id, limit=100)
        payload = [item.model_dump(mode="json") for item in evidence]
        raw = await self.llm.complete(
            LLMRole.PLANNER,
            [
                Message("system", PLANNER_SYSTEM),
                Message("user", planner_user(task_id, payload, state.get("question", ""))),
            ],
            json_mode=True,
            max_tokens=1600,
        )
        sections = self._parse_sections(raw)
        if not sections:
            sections = [
                {"id": "s1", "title": "核心结论", "points": ["主要发现"]},
                {"id": "s2", "title": "分歧与证据", "points": ["不同来源的说法"]},
            ]
        return {"outline": sections, "warnings": state.get("warnings", [])}

    def wait_user(self, state: ReportState) -> dict:
        decision = interrupt({"outline": state.get("outline", [])})
        outline = state.get("outline", [])
        if isinstance(decision, dict) and isinstance(decision.get("outline"), list):
            outline = decision["outline"]
        return {
            "outline": outline,
            "current_index": 0,
            "contexts": {},
            "critiques": {},
            "retries": {},
            "drafts": {},
            "claims": [],
            "ledger": [],
            "conflicts": [],
        }

    async def _contexts_for(
        self, task_id: str, section: dict, *, kind: str | None = None
    ) -> list[dict]:
        single = is_single_video(kind)
        if single and str(section.get("id")) == "s2":
            # “内容脉络”必须覆盖全片，检索天然偏向开头与关键词密集段，
            # 这里直接用时间轴窗口，避免后半段消失。
            return await self._timeline_contexts(task_id)
        query = " ".join(
            [str(section.get("title") or "")]
            + [str(point) for point in section.get("points", [])]
            + ["实测", "数据", "观点"]
        )
        try:
            hits = await self.index_service.search(
                task_id, query, top_k=8 if single else 6
            )
            contexts = [hit.as_dict() for hit in hits]
            if single and len(contexts) < 3:
                # 单视频的索引可能还在后台构建，先用时间轴兜住。
                fallback = await self._timeline_contexts(task_id, limit=4)
                contexts = contexts + [item for item in fallback if item not in contexts]
            return contexts
        except IndexNotFound:
            # 自动索引可能还在后台执行，先补建一次，避免生成空研报。
            logger.warning("index_missing_retry task=%s", task_id)
            try:
                await self.index_service.index_task(task_id)
                hits = await self.index_service.search(
                    task_id, query, top_k=8 if single else 6
                )
                return [hit.as_dict() for hit in hits]
            except Exception as exc:
                logger.warning("index_retry_failed task=%s: %s", task_id, exc)
                return await self._timeline_contexts(task_id) if single else []
        except Exception as exc:
            logger.warning("retrieve_failed task=%s: %s", task_id, exc)
            return await self._timeline_contexts(task_id) if single else []

    async def _timeline_contexts(self, task_id: str, limit: int | None = None) -> list[dict]:
        try:
            evidence = await self.task_repository.list_evidence(task_id, limit=100000)
        except Exception as exc:
            logger.warning("timeline_contexts_failed task=%s: %s", task_id, exc)
            return []
        windows = timeline_windows(evidence)
        if limit is not None:
            windows = windows[:limit]
        return [window_as_context(window) for window in windows]

    async def retrieve(self, state: ReportState) -> dict:
        outline = state.get("outline", [])
        index = state.get("current_index", 0)
        if not outline or index >= len(outline):
            return {"contexts": state.get("contexts", {})}
        section = outline[index]
        section_id = str(section.get("id") or f"s{index + 1}")
        contexts = await self._contexts_for(
            state["task_id"], section, kind=state.get("report_kind")
        )
        all_contexts = dict(state.get("contexts", {}))
        all_contexts[section_id] = contexts
        return {"contexts": all_contexts}

    async def _write_section(
        self,
        section: dict,
        contexts: list[dict],
        question: str,
        *,
        kind: str | None = None,
        video_title: str = "",
    ) -> str:
        if not contexts:
            return "现有证据不足，无法形成有依据的结论。"
        single = is_single_video(kind)
        system = SINGLE_WRITER_SYSTEM if single else WRITER_SYSTEM
        user = (
            single_writer_user(section, contexts, question, video_title)
            if single
            else writer_user(section, contexts, question)
        )
        last_error: Exception | None = None
        # 并行写作时偶发连接被重置，重试一次比整章失败更划算。
        for attempt in range(2):
            try:
                text = await self.llm.complete(
                    LLMRole.WRITER,
                    [Message("system", system), Message("user", user)],
                    max_tokens=self.settings.llm_max_tokens,
                )
                return text.strip()
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(1.5)
        return f"现有证据不足，本章节生成失败：{last_error}"

    async def parallel_draft(self, state: ReportState) -> dict:
        """快速模式：检索与章节写作并行，跳过 critic LLM 调用。"""
        outline = state.get("outline", [])
        kind = state.get("report_kind")
        question = state.get("report_question") or (
            "" if is_single_video(kind) else state.get("question", "")
        )
        video_title = str((state.get("video_meta") or {}).get("title") or "")
        if not outline:
            return {"drafts": {}, "contexts": {}, "current_index": 0}

        # 同时受章节并发和 LLM 并发约束，避免快速模式打满 DeepSeek 限流。
        semaphore = asyncio.Semaphore(
            max(
                min(
                    self.settings.report_parallel_sections,
                    self.settings.llm_concurrency,
                ),
                1,
            )
        )

        async def draft_one(index: int, section: dict) -> tuple[str, list[dict], str]:
            section_id = str(section.get("id") or f"s{index + 1}")
            async with semaphore:
                contexts = await self._contexts_for(
                    state["task_id"], section, kind=kind
                )
                text = await self._write_section(
                    section,
                    contexts,
                    question,
                    kind=kind,
                    video_title=video_title,
                )
            return section_id, contexts, text

        results = await asyncio.gather(
            *(draft_one(index, section) for index, section in enumerate(outline))
        )
        contexts: dict[str, list[dict]] = {}
        drafts: dict[str, str] = {}
        for section_id, section_contexts, text in results:
            contexts[section_id] = section_contexts
            drafts[section_id] = text
        return {
            "drafts": drafts,
            "contexts": contexts,
            "current_index": len(outline),
        }

    async def critic(self, state: ReportState) -> dict:
        outline = state.get("outline", [])
        index = state.get("current_index", 0)
        section = outline[index] if outline else {}
        section_id = str(section.get("id") or f"s{index + 1}")
        contexts = state.get("contexts", {}).get(section_id, [])
        if not contexts:
            critique = {"supported": False, "reason": "没有检索到证据", "missing": []}
        else:
            try:
                raw = await self.llm.complete(
                    LLMRole.CRITIC,
                    [
                        Message("system", CRITIC_SYSTEM),
                        Message("user", critic_user(section, contexts)),
                    ],
                    json_mode=True,
                    max_tokens=600,
                )
                critique = self._parse_json(raw) or {"supported": True, "reason": "fallback"}
            except Exception as exc:
                critique = {"supported": True, "reason": f"critic 失败，放行：{exc}"}
        critiques = dict(state.get("critiques", {}))
        critiques[section_id] = critique
        retries = dict(state.get("retries", {}))
        retries[section_id] = retries.get(section_id, 0) + 1
        return {"critiques": critiques, "retries": retries}

    def route_after_critic(self, state: ReportState) -> str:
        outline = state.get("outline", [])
        index = state.get("current_index", 0)
        section = outline[index] if outline else {}
        section_id = str(section.get("id") or f"s{index + 1}")
        critique = state.get("critiques", {}).get(section_id, {})
        retries = state.get("retries", {}).get(section_id, 0)
        if not critique.get("supported") and retries < 2:
            return "retry"
        return "writer"

    async def writer(self, state: ReportState) -> dict:
        outline = state.get("outline", [])
        index = state.get("current_index", 0)
        section = outline[index] if outline else {}
        section_id = str(section.get("id") or f"s{index + 1}")
        contexts = state.get("contexts", {}).get(section_id, [])
        kind = state.get("report_kind")
        question = state.get("report_question") or (
            "" if is_single_video(kind) else state.get("question", "")
        )
        text = await self._write_section(
            section,
            contexts,
            question,
            kind=kind,
            video_title=str((state.get("video_meta") or {}).get("title") or ""),
        )
        drafts = dict(state.get("drafts", {}))
        drafts[section_id] = text
        return {"drafts": drafts, "current_index": index + 1}

    def route_after_writer(self, state: ReportState) -> str:
        return (
            "next"
            if state.get("current_index", 0) < len(state.get("outline", []))
            else "done"
        )

    async def adjudicate(self, state: ReportState) -> dict:
        evidence = await self.task_repository.list_evidence(state["task_id"], limit=100000)
        evidence_map = {
            item.evidence_id: {
                "video_id": item.video_id,
                "text": item.text,
                "modality": item.modality.value,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
            }
            for item in evidence
        }
        raw_drafts = state.get("drafts", {})
        warnings = list(state.get("warnings", []))
        single = is_single_video(state.get("report_kind"))
        _, citation_stats = claims_from_drafts(raw_drafts, evidence_map)
        if not single and self.rebinder is not None and (
            state.get("mode") != "fast" or self.settings.report_fast_rebind_enabled
        ):
            if len(evidence_map) > self.settings.report_rebind_max_evidence:
                warnings.append(
                    f"证据量 {len(evidence_map)} 超过 rebind 上限 "
                    f"{self.settings.report_rebind_max_evidence}，已跳过 cross-encoder 重绑"
                )
            else:
                try:
                    raw_drafts = await asyncio.wait_for(
                        self.rebinder.rebind(
                            raw_drafts, state.get("contexts", {})
                        ),
                        timeout=max(self.settings.report_rebind_timeout_seconds, 1),
                    )
                except TimeoutError:
                    warnings.append(
                        f"cross-encoder 重绑超过 {self.settings.report_rebind_timeout_seconds}s，"
                        "已保留原始引用继续生成"
                    )
                except Exception as exc:
                    warnings.append(f"cross-encoder 重绑失败，已保留原始引用：{exc}")
        drafts, guard_stats = enforce_citations(raw_drafts, set(evidence_map))
        claims, _ = claims_from_drafts(drafts, evidence_map)
        ledger = build_ledger(claims, evidence_map)
        gaps: list[dict] = []
        quality: dict = {}
        if single:
            # 单视频不存在“跨来源冲突”，改成可核验性缺口。
            conflicts = []
            windows = timeline_windows(evidence)
            gaps = single_gaps(claims, ledger)
            quality = single_quality(evidence, claims, ledger, windows)
            report = assemble_single_markdown(
                state.get("video_meta") or {}, state.get("outline", []), drafts
            )
            if not windows:
                warnings.append("未提取到可用语音/字幕，内容脉络可能缺失时间轴")
            if gaps:
                warnings.append(
                    f"{len(gaps)} 条结论缺少可验证的原片证据，已在证据缺口中列出"
                )
        else:
            conflicts = detect_conflicts(claims, evidence_map)
            ledger = apply_conflicts(ledger, conflicts, claims)
            report = assemble_report(state.get("outline", []), drafts)
        if guard_stats.get("sentences_removed"):
            warnings.append(
                f"已移除 {guard_stats['sentences_removed']} 条无有效引用的正文句"
            )
        unverified = sum(1 for claim in claims if claim.get("evidence_ids") and not claim.get("verified"))
        if unverified:
            warnings.append(f"{unverified} 条 claim 与证据文本相似度偏低")
        if citation_stats.get("citations_invalid"):
            warnings.append(
                f"移除了 {citation_stats['citations_invalid']} 条无效引用"
            )
        return {
            "claims": claims,
            "ledger": ledger,
            "conflicts": conflicts,
            "gaps": gaps,
            "quality": quality,
            # 落库必须用通过引用校验的正文，否则 UI 会展示被 guard 删掉的句子。
            "drafts": drafts,
            "citation_stats": citation_stats,
            "warnings": warnings,
            "report_markdown": report,
        }

    def route_after_wait(self, state: ReportState) -> str:
        if (
            state.get("mode") == "fast"
            and self.settings.report_fast_parallel_enabled
        ):
            return "parallel"
        return "sequential"

    def build(self, checkpointer):
        graph = StateGraph(ReportState)
        graph.add_node("planner", self.planner)
        graph.add_node("wait_user", self.wait_user)
        graph.add_node("retrieve", self.retrieve)
        graph.add_node("parallel_draft", self.parallel_draft)
        graph.add_node("critic", self.critic)
        graph.add_node("writer", self.writer)
        graph.add_node("adjudicate", self.adjudicate)

        graph.add_edge(START, "planner")
        graph.add_edge("planner", "wait_user")
        graph.add_conditional_edges(
            "wait_user",
            self.route_after_wait,
            {"sequential": "retrieve", "parallel": "parallel_draft"},
        )
        graph.add_edge("retrieve", "critic")
        graph.add_conditional_edges(
            "critic",
            self.route_after_critic,
            {"retry": "retrieve", "writer": "writer"},
        )
        graph.add_conditional_edges(
            "writer",
            self.route_after_writer,
            {"next": "retrieve", "done": "adjudicate"},
        )
        graph.add_edge("parallel_draft", "adjudicate")
        graph.add_edge("adjudicate", END)
        return graph.compile(checkpointer=checkpointer)

    @staticmethod
    def _parse_sections(raw: str) -> list[dict]:
        data = ReportWorkflow._parse_json(raw)
        sections = data.get("sections") if isinstance(data, dict) else None
        if not isinstance(sections, list):
            return []
        output = []
        for index, item in enumerate(sections, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            output.append(
                {
                    "id": str(item.get("id") or f"s{index}"),
                    "title": title,
                    "points": [str(point) for point in item.get("points", [])],
                }
            )
        return output

    @staticmethod
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
