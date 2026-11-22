import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReportWorkspace from "./ReportWorkspace";
import {
  api,
  formatDuration,
  formatViews,
  type Candidate,
  type DiscoverySession,
  type Health,
  type TaskHistoryItem,
} from "./api";

type ErrorInfo = {
  code?: string;
  message: string;
  detail?: string;
  stage?: string;
  url?: string;
  failures?: { url?: string; code?: string; message?: string }[];
  taskId?: string;
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [question, setQuestion] = useState(
    "AI 编程工具真的能提升开发效率吗？",
  );
  const [session, setSession] = useState<DiscoverySession | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [taskId, setTaskId] = useState<string | null>(() =>
    window.localStorage.getItem("gether_task_id"),
  );
  const [taskStatus, setTaskStatus] = useState<string | null>(null);
  const [taskProgress, setTaskProgress] = useState(0);
  const [taskEvidence, setTaskEvidence] = useState(0);
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [errorInfo, setErrorInfo] = useState<ErrorInfo | null>(null);
  const [pollKey, setPollKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [history, setHistory] = useState<TaskHistoryItem[]>([]);
  const [mode, setMode] = useState<"fast" | "quality">("fast");
  const [sourceMode, setSourceMode] = useState<"search" | "single">("search");
  const [singleInput, setSingleInput] = useState("");
  const [singleQuestion, setSingleQuestion] = useState("");
  const [minViews, setMinViews] = useState("");
  const autoRetried = useRef<Set<string>>(new Set());
  // 只有本次会话中新发起的任务才自动继续研报；刷新页面时不自动调用 LLM。
  const [autoReport, setAutoReport] = useState(false);

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .listTasks()
      .then((items) => {
        if (!cancelled) setHistory(items);
      })
      .catch(() => {
        // 历史任务加载失败不阻塞主流程
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, taskStatus, pollKey]);

  useEffect(() => {
    if (taskStatus === "PLANNING") {
      window.setTimeout(() => {
        document.getElementById("report")?.scrollIntoView({ behavior: "smooth" });
      }, 300);
    }
  }, [taskStatus]);

  useEffect(() => {
    if (!taskId) return;
    const controller = new AbortController();
    api
      .streamTaskEvents(
        taskId,
        (event) => {
          const status =
            typeof event.data.status === "string" ? event.data.status : null;
          const progress =
            typeof event.data.progress === "number" ? event.data.progress : null;
          const evidence =
            typeof event.data.evidence_count === "number"
              ? event.data.evidence_count
              : null;
          if (status) setTaskStatus(status);
          if (progress != null) setTaskProgress(progress);
          if (evidence != null) setTaskEvidence(evidence);
          if (event.event === "stage" || event.event === "video_parsed") {
            const message = typeof event.data.message === "string" ? event.data.message : null;
            const elapsed =
              typeof event.data.elapsed_ms === "number"
                ? event.data.elapsed_ms
                : null;
            if (message) {
              setStageMessage(
                elapsed != null ? `${message} · ${(elapsed / 1000).toFixed(1)}s` : message,
              );
            }
          }
          if (event.event === "video_failed") {
            const message = typeof event.data.message === "string" ? event.data.message : "视频解析失败";
            setStageMessage(message);
            setErrorInfo({
              code: typeof event.data.code === "string" ? event.data.code : "VIDEO_FAILED",
              message,
              detail: typeof event.data.detail === "string" ? event.data.detail : undefined,
              stage: typeof event.data.stage === "string" ? event.data.stage : "video_failed",
              url: typeof event.data.url === "string" ? event.data.url : undefined,
              taskId: taskId ?? undefined,
            });
          }
          if (event.event === "parse_failed") {
            const code = typeof event.data.code === "string" ? event.data.code : "PARSE_FAILED";
            const message = typeof event.data.message === "string" ? event.data.message : "解析失败";
            setError(`${code}：${message}`);
            setErrorInfo({
              code,
              message,
              detail: typeof event.data.detail === "string" ? event.data.detail : undefined,
              stage: typeof event.data.stage === "string" ? event.data.stage : undefined,
              failures: Array.isArray(event.data.failures) ? event.data.failures as ErrorInfo["failures"] : undefined,
              taskId: taskId ?? undefined,
            });
          }
          if (event.event === "parse_finished" || status === "PLANNING" || status === "FAILED") {
            controller.abort();
          }
        },
        controller.signal,
      )
      .catch(() => {
        // SSE 不可用时保留轮询兜底
      });
    return () => controller.abort();
  }, [taskId]);

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const task = await api.getTask(taskId);
        if (cancelled) return;
        setTaskStatus(task.status);
        setTaskProgress(task.progress);
        setTaskEvidence(task.evidence_count);
        if (task.status === "PENDING" || task.status === "PARSING") {
          timer = window.setTimeout(poll, 2000);
        } else if (task.status === "PLANNING") {
          setNotice("解析完成，正在自动生成大纲并生成研报。");
        } else if (task.status === "FAILED") {
          setError(task.error_message ?? "解析失败");
          setErrorInfo({
            code: task.error_code ?? "FAILED",
            message: task.error_message ?? "解析失败",
            taskId: taskId,
          });
          if (
            task.error_code === "PARSE_INTERRUPTED" &&
            !autoRetried.current.has(taskId)
          ) {
            autoRetried.current.add(taskId);
            setNotice("检测到解析中断，正在自动重试一次…");
            api
              .parseTask(taskId)
              .then(() => {
                setTaskStatus("PARSING");
                setTaskProgress(1);
                setError(null);
                setPollKey((value) => value + 1);
              })
              .catch((retryError: Error) => {
                setError(`自动重试失败：${retryError.message}`);
              });
          }
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [taskId, pollKey]);

  const facetsById = useMemo(() => {
    const map = new Map<string, string>();
    session?.facets.forEach((facet) => map.set(facet.id, facet.label));
    return map;
  }, [session]);

  const selectedCandidates = useMemo(
    () => session?.candidates.filter((item) => selected.has(item.candidate_id)) ?? [],
    [session, selected],
  );

  async function handleDiscover(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    setTaskId(null);
    try {
      const minViewsValue = minViews.trim() ? Number(minViews) : undefined;
      const result = await api.discover(question.trim(), mode, minViewsValue);
      setSession(result);
      const autoIds = result.selected.map((item) => item.candidate_id);
      setSelected(new Set(autoIds));
      if (autoIds.length === 0) {
        setNotice("没有达到自动选片条件，请查看候选并手动选择。");
        return;
      }
      setNotice(`已按综合得分自动选择 ${autoIds.length} 个视频，正在创建解析任务…`);
      const task = await api.confirm(result.session_id, autoIds);
      setAutoReport(true);
      setTaskId(task.task_id);
      window.localStorage.setItem("gether_task_id", task.task_id);
      setTaskStatus("PARSING");
      setTaskProgress(1);
      setTaskEvidence(0);
      setStageMessage("正在启动解析…");
      setPollKey((value) => value + 1);
      setNotice("已自动提交选片并启动解析，完成后会自动生成研报。");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function toggle(candidate: Candidate) {
    if (taskId) return;
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(candidate.candidate_id)) next.delete(candidate.candidate_id);
      else next.add(candidate.candidate_id);
      return next;
    });
    setTaskId(null);
    setNotice(null);
  }

  async function handleConfirm() {    if (!session || selected.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.confirm(session.session_id, [...selected]);
      setAutoReport(true);
      setTaskId(result.task_id);
      window.localStorage.setItem("gether_task_id", result.task_id);
      setTaskStatus("PARSING");
      setTaskProgress(1);
      setTaskEvidence(0);
      setStageMessage("正在启动解析…");
      setPollKey((value) => value + 1);
      setNotice("已自动提交选片并启动解析，完成后会自动生成研报。");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: "search" | "single") {
    if (next === sourceMode) return;
    setSourceMode(next);
    setSession(null);
    setSelected(new Set());
    setError(null);
    setErrorInfo(null);
    setNotice(null);
  }

  async function handleSingle(event: FormEvent) {
    event.preventDefault();
    const value = singleInput.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setTaskId(null);
    try {
      const result = await api.createSingle(
        value,
        singleQuestion.trim() || undefined,
        mode,
      );
      setAutoReport(true);
      setTaskId(result.task_id);
      window.localStorage.setItem("gether_task_id", result.task_id);
      setTaskStatus("PARSING");
      setTaskProgress(1);
      setTaskEvidence(0);
      setStageMessage("正在启动解析…");
      setPollKey((current) => current + 1);
      setNotice(
        `已提交单视频任务：${result.video?.title || value}，解析完成后自动生成研报。`,
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function openTask(item: TaskHistoryItem) {
    setAutoReport(false);
    setTaskId(item.id);
    window.localStorage.setItem("gether_task_id", item.id);
    setTaskStatus(item.status);
    setTaskProgress(item.progress);
    setTaskEvidence(item.evidence_count);
    setStageMessage(null);
    setSession(null);
    setSelected(new Set());
    setError(item.error_message ?? null);
    setNotice(
      item.status === "DONE"
        ? "已打开历史研报。"
        : item.status === "FAILED"
          ? "该任务已失败，可重新解析。"
          : "已打开历史任务。",
    );
    setPollKey((value) => value + 1);
    window.setTimeout(() => {
      document.getElementById("report")?.scrollIntoView({ behavior: "smooth" });
    }, 200);
  }

  async function handleParse() {
    if (!taskId) return;
    setAutoReport(true);
    setBusy(true);
    setError(null);
    try {
      await api.parseTask(taskId);
      setTaskStatus("PARSING");
      setTaskProgress(1);
      setNotice("解析任务已触发，页面会自动刷新状态。");
      setPollKey((value) => value + 1);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="hero">
        <div className="hero-top">
          <p className="eyebrow">Gether · Discovery</p>
          <div className="hero-actions">
            {taskId && (
              <a className="nav-link" href="#report">
                研报工作台 ↓
              </a>
            )}
            <span className={`status-chip ${health ? "ok" : ""}`}>
              {health ? `执行层 ${health.status}` : "连接中"}
            </span>
          </div>
        </div>
        <h1 className="hero-slogan">
          <span className="slogan-accent">Gether</span>{" "}
          <span className="slogan-rest">what you want to know</span>
        </h1>
        <p className="lede">
          从尽可能广的社交媒体视频范围中收集证据，
          生成尽可能准确、忠于原视频内容的研报。
        </p>
      </header>

      <div className="mode-tabs" role="tablist" aria-label="研报模式">
        <button
          type="button"
          role="tab"
          aria-selected={sourceMode === "search"}
          className={`mode-tab ${sourceMode === "search" ? "active" : ""}`}
          onClick={() => switchMode("search")}
        >
          按问题检索多视频
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={sourceMode === "single"}
          className={`mode-tab ${sourceMode === "single" ? "active" : ""}`}
          onClick={() => switchMode("single")}
        >
          单个视频研报
        </button>
      </div>

      <section className="steps">
        {sourceMode === "search" ? (
          <>
            <span className="step active">01 提问</span>
            <span className="step">02 自动选片</span>
            <span className="step">03 自动解析</span>
            <span className="step">04 自动研报</span>
          </>
        ) : (
          <>
            <span className="step active">01 提供 BV / 链接</span>
            <span className="step">02 解析原片</span>
            <span className="step">03 生成研报</span>
          </>
        )}
      </section>

      {sourceMode === "search" ? (
        <form className="discover-form" onSubmit={handleDiscover}>
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="例如：AI 编程工具真的能提升开发效率吗？"
            aria-label="研究问题"
          />
          <label className="min-views-field">
            <span>播放量 ≥</span>
            <input
              type="number"
              min="0"
              step="1000"
              value={minViews}
              onChange={(event) => setMinViews(event.target.value)}
              placeholder="默认 8000"
              aria-label="最低播放量"
            />
          </label>
          <select
            className="mode-select"
            value={mode}
            onChange={(event) => setMode(event.target.value as "fast" | "quality")}
            aria-label="解析模式"
          >
            <option value="fast">快速模式 · 并行研报 / 4 个视频</option>
            <option value="quality">高质量 · 串行审稿 / 8 个视频</option>
          </select>
          <button disabled={busy || question.trim().length < 2}>
            {busy ? "检索并准备解析中…" : "检索并生成研报"}
          </button>
        </form>
      ) : (
        <form className="discover-form single-form" onSubmit={handleSingle}>
          <div className="single-fields">
            <input
              value={singleInput}
              onChange={(event) => setSingleInput(event.target.value)}
              placeholder="BV1xx411c7mD 或 https://www.bilibili.com/video/BV…"
              aria-label="B站视频 BV 号或链接"
            />
            <input
              value={singleQuestion}
              onChange={(event) => setSingleQuestion(event.target.value)}
              placeholder="（可选）你想从这条视频里确认什么？留空则做完整总结"
              aria-label="针对视频的问题"
            />
          </div>
          <select
            className="mode-select"
            value={mode}
            onChange={(event) => setMode(event.target.value as "fast" | "quality")}
            aria-label="解析模式"
          >
            <option value="fast">快速模式 · 并行写作</option>
            <option value="quality">高质量 · 逐章审稿</option>
          </select>
          <button disabled={busy || !singleInput.trim()}>
            {busy ? "正在提交…" : "生成单视频研报"}
          </button>
        </form>
      )}

      {history.length > 0 && (
        <section className="history-section">
          <div className="session-head">
            <div>
              <p className="eyebrow">最近任务</p>
              <h2>继续之前的研究</h2>
            </div>
            <span className="muted">{history.length} 条</span>
          </div>
          <div className="history-grid">
            {history.slice(0, 8).map((item) => (
              <button
                type="button"
                className={`history-card ${item.id === taskId ? "active" : ""}`}
                key={item.id}
                onClick={() => openTask(item)}
              >
                <span className={`task-dot ${item.status.toLowerCase()}`} />
                <span className="history-question">
                  {item.question || "未命名任务"}
                </span>
                <span className="history-meta">
                  {item.status} · {item.progress}% · {item.evidence_count} 证据
                </span>
                <span className="history-time">
                  {new Date(item.created_at).toLocaleString("zh-CN")}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {error && (
        <div className="error-detail">
          <div className="error-detail-head">
            <b>{errorInfo?.code ?? "请求失败"}</b>
            {errorInfo?.stage && <span>阶段：{errorInfo.stage}</span>}
            {errorInfo?.taskId && <code>{errorInfo.taskId.slice(0, 8)}</code>}
          </div>
          <p>{errorInfo?.message ?? error}</p>
          {errorInfo?.detail && errorInfo.detail !== errorInfo.message && (
            <pre>{errorInfo.detail}</pre>
          )}
          {errorInfo?.url && (
            <p className="muted">
              失败视频：
              <a href={errorInfo.url} target="_blank" rel="noreferrer">
                {errorInfo.url}
              </a>
            </p>
          )}
          {errorInfo?.failures?.length ? (
            <ul>
              {errorInfo.failures.map((failure, index) => (
                <li key={index}>
                  {failure.code ?? "FAILED"}：{failure.message ?? "未知错误"}
                  {failure.url ? `（${failure.url}）` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
      {notice && <p className="notice">{notice}</p>}

      {session && (
        <>
          <div className="session-head">
            <div>
              <p className="eyebrow">检索计划</p>
              <h2>{session.question}</h2>
              {session.rewritten_question && (
                <p className="muted">检索改写：{session.rewritten_question}</p>
              )}
            </div>
            <div className="session-stats">
              <span>{session.candidates.length} 个候选</span>
              <strong>{selected.size} 个已选</strong>
            </div>
          </div>

          {session.ranking && (
            <div className="ranking-banner">
              <span className="ranking-label">{session.ranking.label}</span>
              <span>
                Score = {session.ranking.weights.m.toFixed(2)}R +{" "}
                {session.ranking.weights.n.toFixed(2)}A +{" "}
                {session.ranking.weights.p.toFixed(2)}F
              </span>
              <small>{session.ranking.reason}</small>
            </div>
          )}

          {session.negative_terms?.length ? (
            <p className="muted">
              负向词：{session.negative_terms.join("、")}
            </p>
          ) : null}

          <div className="facet-row">
            {session.facets.map((facet) => (
              <span className="facet" key={facet.id}>
                <b>{facet.label}</b>
                <small>{facet.stance}</small>
              </span>
            ))}
          </div>

          {session.sufficiency?.warnings?.length ? (
            <div className="warnings">
              <b>检索提示</b>
              <ul>
                {session.sufficiency.warnings.slice(0, 4).map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="muted">
              覆盖率 {(session.sufficiency?.coverage ?? 0) * 100}% ·{" "}
              过滤级别 {session.sufficiency?.filter?.level ?? "primary"}
            </p>
          )}

          <section className="candidate-list">
            {session.candidates.map((candidate) => {
              const active = selected.has(candidate.candidate_id);
              return (
                <article
                  className={`candidate ${active ? "selected" : ""}`}
                  key={candidate.candidate_id}
                >
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={active}
                      disabled={Boolean(taskId)}
                      onChange={() => toggle(candidate)}
                    />
                    <span />
                  </label>
                  <div className="candidate-body">
                    <div className="candidate-title">
                      <a href={candidate.url} target="_blank" rel="noreferrer">
                        {candidate.title}
                      </a>
                      {candidate.selected && (
                        <span className="auto-badge">自动选中</span>
                      )}
                      <span className="score">
                        {candidate.final_score.toFixed(2)}
                      </span>
                    </div>
                    <div className="candidate-meta">
                      <span>{candidate.source === "bilibili" ? "B站" : candidate.source}</span>
                      <span>{candidate.uploader ?? "未知作者"}</span>
                      <span>{formatViews(candidate.views)} 播放</span>
                      <span>{formatDuration(candidate.duration_ms)}</span>
                      <span>
                        {candidate.published_at
                          ? new Date(candidate.published_at * 1000).toLocaleDateString("zh-CN")
                          : "发布时间未知"}
                      </span>
                      <span>相关性 {candidate.relevance.toFixed(2)}</span>
                      <span>权威 {candidate.authority.toFixed(2)}</span>
                      <span>时效 {candidate.freshness.toFixed(2)}</span>
                      <span>综合 {candidate.final_score.toFixed(2)}</span>
                      {candidate.topic_score != null && (
                        <span>主题 {candidate.topic_score.toFixed(2)}</span>
                      )}
                      {candidate.metrics?.content_type && (
                        <span>{candidate.metrics.content_type}</span>
                      )}
                      {candidate.metrics?.tag_hits?.length ? (
                        <span className="tag-hit">
                          标签命中：{candidate.metrics.tag_hits.join("、")}
                        </span>
                      ) : null}
                      {candidate.topic_gate === false && (
                        <span className="reject-badge">
                          主题不匹配{candidate.rejection_reason ? ` · ${candidate.rejection_reason}` : ""}
                        </span>
                      )}
                    </div>
                    {candidate.description && (
                      <p className="candidate-desc">
                        {candidate.description.slice(0, 220)}
                        {candidate.description.length > 220 ? "…" : ""}
                      </p>
                    )}
                    <div className="candidate-facets">
                      {candidate.matched_facets.map((id) => (
                        <span key={id}>{facetsById.get(id) ?? id}</span>
                      ))}
                    </div>
                    {candidate.reason && (
                      <p className="reason">{candidate.reason}</p>
                    )}
                  </div>
                </article>
              );
            })}
          </section>

          <div className="confirm-bar">
            <div>
              {taskId ? (
                <div className="progress-cluster">
                  <div className="task-summary-head">
                    <b>{selectedCandidates.length}</b> 个视频已提交
                    <span className={`task-dot ${taskStatus?.toLowerCase() ?? ""}`} />
                  </div>
                  <div className="progress-meta">
                    <span className="progress-label">
                      {taskStatus === "PENDING" && "等待开始解析"}
                      {taskStatus === "PARSING" && "正在解析"}
                      {taskStatus === "PLANNING" && "解析完成"}
                      {taskStatus === "DONE" && "解析完成"}
                      {taskStatus === "FAILED" && "解析中断/失败，请重新解析"}
                      {!taskStatus && "准备中"}
                    </span>
                    <span className="progress-value">
                      {taskStatus === "PLANNING" ? 100 : taskProgress}%
                    </span>
                  </div>
                  <div
                    className={`progress-track ${
                      taskStatus === "PARSING" ? "active" : ""
                    } ${taskStatus === "FAILED" ? "failed" : ""}`}
                  >
                    <div
                      className="progress-fill"
                      style={{
                        width: `${
                          taskStatus === "PLANNING" ? 100 : taskProgress
                        }%`,
                      }}
                    />
                  </div>
                  <p className="progress-sub">
                    {taskEvidence} 条证据 · {taskStatus ?? "PENDING"}
                    {stageMessage ? ` · ${stageMessage}` : ""}
                  </p>
                </div>
              ) : (
                <>
                  <b>{selectedCandidates.length}</b> 个视频已按综合得分自动选中
                  <p className="muted">
                    检索完成后会自动创建任务并启动解析，无需手动确认。
                  </p>
                </>
              )}
            </div>
            <div className="confirm-actions">
              {taskId && <code>{taskId}</code>}
              {taskId && (
                <span className="task-status">
                  {taskStatus ?? "PENDING"} · {taskProgress}% · {taskEvidence} 证据
                </span>
              )}
              {taskId ? (
                taskStatus === "FAILED" ? (
                  <button onClick={handleParse} disabled={busy}>
                    重试解析
                  </button>
                ) : (
                  <span className="task-status">
                    {taskStatus === "PARSING" ? "自动解析中" : "自动流程运行中"}
                  </span>
                )
              ) : (
                <button
                  onClick={handleConfirm}
                  disabled={busy || selected.size === 0}
                >
                  {busy ? "创建中…" : "启动自动流程"}
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {taskId && (
        <ReportWorkspace
          initialTaskId={taskId}
          ready={taskStatus === "PLANNING"}
          autoStart={autoReport}
          embedded
        />
      )}

      <footer>
        <span>B站搜索 · 播放量过滤 · 覆盖优先 MMR</span>
        <span>Gether P2.6</span>
      </footer>
    </main>
  );
}
