import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  reportApi,
  type Citation,
  type OutlineSection,
  type ReportData,
  type StreamEvent,
} from "./api";

type ReportErrorInfo = {
  code?: string;
  message: string;
  detail?: string;
  stage?: string;
  retryable?: boolean;
};

type Props = {
  initialTaskId?: string | null;
  ready?: boolean;
  autoStart?: boolean;
  embedded?: boolean;
};

function stripEvidenceMarkers(text: string): string {
  return text
    .replace(/\[ev:[^\]]+\]/g, "")
    .replace(/[#*`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export default function ReportWorkspace({
  initialTaskId,
  ready = false,
  autoStart = false,
  embedded = false,
}: Props) {
  const [taskId, setTaskId] = useState(initialTaskId ?? "");
  const [outlineText, setOutlineText] = useState("");
  const [hasOutline, setHasOutline] = useState(false);
  const [report, setReport] = useState<ReportData | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [generationProgress, setGenerationProgress] = useState(0);
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorInfo, setErrorInfo] = useState<ReportErrorInfo | null>(null);
  const plannedTask = useRef<string | null>(null);

  useEffect(() => {
    if (initialTaskId) setTaskId(initialTaskId);
    setGenerationProgress(0);
  }, [initialTaskId]);

  const citationNumber = useMemo(() => {
    const map = new Map<string, number>();
    return (evidenceId: string) => {
      if (!map.has(evidenceId)) map.set(evidenceId, map.size + 1);
      return map.get(evidenceId)!;
    };
  }, [report]);

  const claimsById = useMemo(() => {
    const map = new Map<string, string>();
    report?.claims.forEach((claim) => map.set(claim.claim_id, claim.text));
    return map;
  }, [report]);

  const citationsById = useMemo(() => {
    const map = new Map<string, Citation>();
    report?.citations?.forEach((citation) => map.set(citation.evidence_id, citation));
    return map;
  }, [report]);

  const summarySection = report?.sections?.[0];
  const summaryText = summarySection
    ? stripEvidenceMarkers(summarySection.content_md)
    : "";
  const summaryExcerpt =
    summaryText.length > 300 ? `${summaryText.slice(0, 300)}…` : summaryText;

  const outlineData = useMemo<OutlineSection[]>(() => {
    try {
      const parsed = JSON.parse(outlineText);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }, [outlineText]);

  function updateOutline(next: OutlineSection[]) {
    setOutlineText(JSON.stringify(next, null, 2));
  }

  function updateSection(index: number, patch: Partial<OutlineSection>) {
    updateOutline(
      outlineData.map((section, itemIndex) =>
        itemIndex === index ? { ...section, ...patch } : section,
      ),
    );
  }

  function moveSection(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= outlineData.length) return;
    const next = [...outlineData];
    [next[index], next[target]] = [next[target], next[index]];
    updateOutline(next);
  }

  function removeSection(index: number) {
    updateOutline(outlineData.filter((_, itemIndex) => itemIndex !== index));
  }

  function addSection() {
    updateOutline([
      ...outlineData,
      { id: `s${outlineData.length + 1}`, title: "新章节", points: [] },
    ]);
  }

  async function handlePlan(event?: FormEvent) {
    event?.preventDefault();
    if (!taskId.trim()) return;
    setBusy(true);
    setError(null);
    setErrorInfo(null);
    setReport(null);
    setEvents([]);
    setGenerationProgress(5);
    let outline: OutlineSection[] | null = null;
    try {
      const result = await reportApi.plan(taskId.trim());
      outline = result.outline;
      setOutlineText(JSON.stringify(result.outline, null, 2));
      setHasOutline(true);
      setStatus(result.status);
      setGenerationProgress(15);
      setEvents([`大纲已生成：${result.outline.length} 章，正在自动生成研报`]);
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      setErrorInfo({ code: "REPORT_PLAN_FAILED", message, stage: "plan" });
    } finally {
      setBusy(false);
      if (outline) {
        // 用户不需要再点“确认并生成研报”。
        window.setTimeout(() => void handleGenerate(outline ?? undefined), 0);
      }
    }
  }

  useEffect(() => {
    if (!initialTaskId) return;
    let cancelled = false;
    reportApi
      .report(initialTaskId)
      .then((data) => {
        if (cancelled || !data.sections?.length) return;
        setReport(data);
        setStatus(data.status);
        setGenerationProgress(100);
      })
      .catch(() => {
        // PLANNING 任务还没有报告，交给下面的自动大纲流程
      });
    return () => {
      cancelled = true;
    };
  }, [initialTaskId]);

  useEffect(() => {
    if (!ready || !autoStart || !initialTaskId || plannedTask.current === initialTaskId) {
      return;
    }
    plannedTask.current = initialTaskId;
    void handlePlan();
    // 只有本次会话中新发起的任务才自动生成；刷新历史任务需用户确认。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, autoStart, initialTaskId]);

  async function handleGenerate(outlineOverride?: OutlineSection[]) {
    if (!taskId.trim()) return;
    let outline: OutlineSection[];
    try {
      outline = outlineOverride ?? (JSON.parse(outlineText) as OutlineSection[]);
    } catch {
      setError("大纲 JSON 无法解析，请先修正大纲。");
      return;
    }
    if (!outline.length) return;
    setBusy(true);
    setError(null);
    setErrorInfo(null);
    setEvents([]);
    setReport(null);
    setGenerationProgress(5);
    try {
      let done = false;
      try {
        await reportApi.streamResume(taskId.trim(), outline, (event: StreamEvent) => {
        const node = typeof event.data.node === "string" ? event.data.node : "";
        const progress =
          typeof event.data.progress === "number" ? event.data.progress : null;
        if (progress != null) setGenerationProgress(progress);
        if (event.event === "state") {
          const elapsed =
            typeof event.data.elapsed_ms === "number"
              ? ` · ${(event.data.elapsed_ms / 1000).toFixed(1)}s`
              : "";
          setEvents((current) => [...current, `节点：${node}${elapsed}`].slice(-12));
        } else if (event.event === "token") {
          const section = String(event.data.section_id ?? "");
          setEvents((current) => [...current, `章节 ${section} 已生成`].slice(-12));
        } else if (event.event === "conflict") {
          setEvents((current) => [...current, `发现冲突：${event.data.type}`].slice(-12));
        } else if (event.event === "error") {
          setError(String(event.data.message ?? "生成失败"));
          } else if (event.event === "done") {
            done = true;
            setStatus("DONE");
            setGenerationProgress(100);
          }
        });
      } catch (streamError) {
        if (done) throw streamError;
        setEvents((current) => [
          ...current,
          "SSE 中断，切换为同步生成",
        ].slice(-12));
        await reportApi.resume(taskId.trim(), outline);
      }
      const result = await reportApi.report(taskId.trim());
      setReport(result);
      setStatus(result.status);
      setGenerationProgress(100);
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      setErrorInfo({ code: "REPORT_FAILED", message, stage: "generate" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className={`report-workspace ${embedded ? "embedded" : ""}`}
      id="report"
    >
      {!embedded && (
        <>
          <div className="session-head">
            <div>
              <p className="eyebrow">P3 · Report Workspace</p>
              <h2>研报与冲突裁决</h2>
            </div>
            <span className="muted">{status || "等待任务"}</span>
          </div>

          {initialTaskId ? (
            <div className="task-pill">
              <span>任务 {initialTaskId.slice(0, 8)}… · {status || "准备中"}</span>
              <button onClick={() => void handlePlan()} disabled={busy}>
                重新生成大纲
              </button>
            </div>
          ) : (
            <form className="discover-form" onSubmit={handlePlan}>
              <input
                value={taskId}
                onChange={(event) => setTaskId(event.target.value)}
                placeholder="粘贴已解析任务的 Task ID"
                aria-label="Task ID"
              />
              <button disabled={busy || taskId.trim().length < 8}>
                {busy ? "处理中…" : "生成大纲"}
              </button>
            </form>
          )}
        </>
      )}

      {error && (
        <div className="error-detail">
          <div className="error-detail-head">
            <b>{errorInfo?.code ?? "REPORT_FAILED"}</b>
            {errorInfo?.stage && <span>阶段：{errorInfo.stage}</span>}
            {errorInfo?.retryable && <span>可重试</span>}
          </div>
          <p>{errorInfo?.message ?? error}</p>
          {errorInfo?.detail && errorInfo.detail !== errorInfo.message && (
            <pre>{errorInfo.detail}</pre>
          )}
        </div>
      )}

      {ready && !autoStart && !hasOutline && !report && !busy && (
        <div className="report-prompt">
          <div>
            <b>解析已完成，尚未生成研报</b>
            <p>为避免刷新页面时自动消耗模型调用，继续生成需要你确认。</p>
          </div>
          <button onClick={() => void handlePlan()}>生成大纲并生成研报</button>
        </div>
      )}

      {report && summarySection && !busy ? (
        <section className="report-summary">
          <div className="report-summary-top">
            <div>
              <p className="eyebrow">研报摘要</p>
              <span className="summary-status">{status || "DONE"}</span>
            </div>
            <span className="summary-count">
              {report.sections.length} 章 · {report.claims.length} claims
            </span>
          </div>
          <h2>{summarySection.title}</h2>
          <p>{summaryExcerpt}</p>
          <a className="summary-link" href="#report-body">
            查看完整研报 ↓
          </a>
        </section>
      ) : (
        (busy || hasOutline || report) && (
          <div className={`progress-cluster report-progress ${embedded ? "inline" : ""}`}>
            <div className="progress-meta">
              <span className="progress-label">
                {busy && generationProgress >= 90
                  ? "证据裁决与冲突检测中"
                  : `研报生成${status ? ` · ${status}` : ""}`}
                {busy && generationProgress < 90 ? " · 自动生成中" : ""}
              </span>
              <span className="progress-value">{generationProgress}%</span>
            </div>
            <div className={`progress-track ${busy ? "active" : ""}`}>
              <div
                className="progress-fill"
                style={{ width: `${generationProgress}%` }}
              />
            </div>
          </div>
        )
      )}

      {hasOutline && (
        <div className="outline-box">
          <div className="outline-head">
            <b>大纲编辑器</b>
            <button onClick={() => handleGenerate()} disabled={busy}>
              {busy ? "生成中…" : "重新生成研报"}
            </button>
          </div>
          <div className="outline-editor">
            {outlineData.map((section, index) => (
              <article className="outline-item" key={`${section.id}-${index}`}>
                <div className="outline-item-head">
                  <span>章节 {index + 1}</span>
                  <div>
                    <button type="button" onClick={() => moveSection(index, -1)}>↑</button>
                    <button type="button" onClick={() => moveSection(index, 1)}>↓</button>
                    <button type="button" onClick={() => removeSection(index)}>删除</button>
                  </div>
                </div>
                <input
                  value={section.title}
                  onChange={(event) => updateSection(index, { title: event.target.value })}
                  aria-label={`章节 ${index + 1} 标题`}
                />
                <textarea
                  value={section.points.join(String.fromCharCode(10))}
                  onChange={(event) =>
                    updateSection(index, {
                      points: event.target.value
                        .split(String.fromCharCode(10))
                        .map((point) => point.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="每行一个要点"
                  aria-label={`章节 ${index + 1} 要点`}
                />
              </article>
            ))}
            <button type="button" className="outline-add" onClick={addSection}>
              + 添加章节
            </button>
            <details className="outline-json">
              <summary>高级：编辑 JSON</summary>
              <textarea
                value={outlineText}
                onChange={(event) => setOutlineText(event.target.value)}
                spellCheck={false}
              />
            </details>
          </div>
        </div>
      )}

      {events.length > 0 && (
        <div className="event-log">
          {events.map((item, index) => (
            <span key={`${item}-${index}`}>{item}</span>
          ))}
        </div>
      )}

      {report && (
        <div className="report-grid" id="report-body">
          <div className="report-main">
            {report.sections.map((section) => (
              <article className="report-section" key={section.order_no}>
                <h3>{section.title}</h3>
                <pre>
                  {section.content_md.split(/(\[ev:[0-9a-fA-F-]{8,}\])/g).map((part, index) => {
                    const match = part.match(/^\[ev:([0-9a-fA-F-]{8,})\]$/);
                    if (!match) return <span key={index}>{part}</span>;
                    const evidenceId = match[1];
                    return (
                      <button
                        type="button"
                        className="cite"
                        key={`${evidenceId}-${index}`}
                        title={`点击查看原片：${evidenceId}`}
                        onClick={() => {
                          const citation = citationsById.get(evidenceId);
                          if (citation) setActiveCitation(citation);
                        }}
                      >
                        [{citationNumber(evidenceId)}]
                      </button>
                    );
                  })}
                </pre>
              </article>
            ))}
          </div>

          <aside className="insight-panel">
            <div className="trace-panel">
              <div className="panel-head">
                <b>Agent 轨迹</b>
                <span>{status}</span>
              </div>
              <div className="trace-list">
                {events.length === 0 ? (
                  <p className="muted">等待生成事件…</p>
                ) : (
                  events.map((item, index) => (
                    <div className="trace-item" key={`${item}-${index}`}>
                      <span className="trace-dot" />
                      <span>{item}</span>
                    </div>
                  ))
                )}
              </div>
              <div className="panel-head">
                <b>当前引用</b>
              </div>
              {activeCitation ? (
                <div className="active-citation">
                  <b>
                    {activeCitation.start_ms / 1000 < 60
                      ? `00:${String(Math.floor(activeCitation.start_ms / 1000)).padStart(2, "0")}`
                      : `${Math.floor(activeCitation.start_ms / 60000)}:${String(
                          Math.floor((activeCitation.start_ms % 60000) / 1000),
                        ).padStart(2, "0")}`}
                  </b>
                  <p>{activeCitation.quote}</p>
                  <small>{activeCitation.modality} · {activeCitation.source}</small>
                </div>
              ) : (
                <p className="muted">点击正文中的引用角标查看原片片段。</p>
              )}
            </div>
            <div className="insight-content">
              <div className="panel-head">
                <b>证据播放器</b>
              </div>
            {activeCitation ? (
              <div className="player-box">
                {activeCitation.source === "bilibili" && (
                  <iframe
                    key={`${activeCitation.evidence_id}-${activeCitation.start_ms}`}
                    src={`https://player.bilibili.com/player.html?bvid=${activeCitation.source_video_id}&t=${Math.floor(activeCitation.start_ms / 1000)}&autoplay=0`}
                    title="B站证据片段"
                    allowFullScreen
                  />
                )}
                {activeCitation.source === "youtube" && (
                  <iframe
                    key={`${activeCitation.evidence_id}-${activeCitation.start_ms}`}
                    src={`https://www.youtube.com/embed/${activeCitation.source_video_id}?start=${Math.floor(activeCitation.start_ms / 1000)}&autoplay=0`}
                    title="YouTube 证据片段"
                    allowFullScreen
                  />
                )}
                {activeCitation.source === "local" && (
                  <video
                    key={activeCitation.evidence_id}
                    src={activeCitation.url}
                    controls
                    autoPlay
                  />
                )}
                <p className="player-quote">{activeCitation.quote}</p>
                <a
                  className="nav-link"
                  href={`${activeCitation.url}${activeCitation.url.includes("?") ? "&" : "?"}t=${Math.floor(activeCitation.start_ms / 1000)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  在{activeCitation.source === "bilibili" ? "B站" : "原站"}打开该时间点
                </a>
              </div>
            ) : (
              <p className="muted">点击正文引用查看原片。</p>
            )}

            <div className="panel-head">
              <b>冲突裁决</b>
              <span>{report.conflicts.length} 组</span>
            </div>
            {report.conflicts.length === 0 ? (
              <p className="muted">
                未检测到 claim 级冲突。单视频任务通常不会有跨来源冲突。
              </p>
            ) : (
              report.conflicts.map((conflict) => (
                <article className="conflict-card" key={conflict.conflict_id}>
                  <div className="conflict-type">{conflict.type}</div>
                  <p>{conflict.summary}</p>
                  <div className="conflict-claims">
                    {conflict.claim_ids.map((claimId) => {
                      const claim = report.claims.find(
                        (item) => item.claim_id === claimId,
                      );
                      return (
                        <blockquote key={claimId}>
                          {claimsById.get(claimId) ?? claimId}
                          {claim && Object.keys(claim.qualifiers ?? {}).length > 0 && (
                            <div className="qualifier-row">
                              {Object.entries(claim.qualifiers).map(([key, value]) => (
                                <span className="qualifier-chip" key={`${claimId}-${key}`}>
                                  {key}: {value}
                                </span>
                              ))}
                            </div>
                          )}
                        </blockquote>
                      );
                    })}
                  </div>
                  <small>{conflict.evidence_ids.length} 条证据参与裁决</small>
                </article>
              ))
            )}

            <div className="panel-head">
              <b>质量指标</b>
            </div>
            <dl className="metrics">
              <div>
                <dt>Claim</dt>
                <dd>{report.claims.length}</dd>
              </div>
              <div>
                <dt>Ledger</dt>
                <dd>{report.ledger.length}</dd>
              </div>
              <div>
                <dt>冲突</dt>
                <dd>{report.conflicts.length}</dd>
              </div>
              <div>
                <dt>未验证 claim</dt>
                <dd>
                  {report.claims.filter(
                    (claim) => !report.ledger.some((entry) => entry.claim_id === claim.claim_id && entry.verified),
                  ).length}
                </dd>
              </div>
            </dl>
            </div>
          </aside>
        </div>
      )}
    </section>
  );
}
