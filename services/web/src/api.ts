export type Health = {
  status: string;
  version: string;
  env: string;
  llm_provider: string;
  sources: string[];
};

export type VideoRef = {
  source: "bilibili" | "youtube" | "local";
  source_video_id: string;
  page: number;
  url: string;
  title: string | null;
  duration_ms: number | null;
};

export type Facet = {
  id: string;
  label: string;
  query: string;
  stance: "positive" | "negative" | "neutral" | string;
  required_any?: string[];
  exclude_any?: string[];
};

export type RankingInfo = {
  priority: "relevance" | "authority" | "freshness" | "balanced" | string;
  label: string;
  weights: { m: number; n: number; p: number };
  reason: string;
};

export type Candidate = {
  candidate_id: string;
  source: string;
  source_video_id: string;
  title: string;
  description?: string;
  url: string;
  duration_ms: number | null;
  uploader: string | null;
  views: number | null;
  published_at: number | null;
  freshness: number;
  matched_facets: string[];
  metrics?: {
    content_type?: string;
    content_types?: string[];
    noise_terms?: string[];
    tag?: string;
    tag_hits?: string[];
    tag_score?: number;
    topic_gate?: boolean;
    ranking_priority?: string;
    score_breakdown?: { relevance: number; authority: number; freshness: number; m: number; n: number; p: number };
  } & Record<string, unknown>;
  relevance: number;
  authority: number;
  final_score: number;
  topic_score?: number;
  topic_gate?: boolean;
  rejection_reason?: string | null;
  facet_confidence?: Record<string, number>;
  reason: string | null;
  selected: boolean;
};

export type Sufficiency = {
  ok: boolean;
  coverage?: number;
  warnings: string[];
  filter?: { level: string; threshold: number | string; before: number };
};

export type TaskDetail = {
  id: string;
  status: string;
  progress: number;
  evidence_count: number;
  video_count: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskHistoryItem = {
  id: string;
  status: string;
  progress: number;
  evidence_count: number;
  video_count: number;
  question: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type DiscoverySession = {
  session_id: string;
  question: string;
  facets: Facet[];
  queries?: string[];
  negative_terms?: string[];
  rewritten_question?: string;
  ranking?: RankingInfo;
  candidates: Candidate[];
  selected: Candidate[];
  sufficiency?: Sufficiency;
  status?: string;
  task_id?: string | null;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8080";
const USER_ID =
  window.localStorage.getItem("gether_user_id") ??
  (() => {
    const id = crypto.randomUUID();
    window.localStorage.setItem("gether_user_id", id);
    return id;
  })();

let authToken: string | null = window.localStorage.getItem("gether_token");

async function resetToken(): Promise<void> {
  authToken = null;
  window.localStorage.removeItem("gether_token");
}

async function ensureToken(force = false): Promise<string> {
  if (force) await resetToken();
  if (authToken) return authToken;
  const response = await fetch(`${API_BASE}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: USER_ID }),
  });
  if (!response.ok) throw new Error(`认证失败：HTTP ${response.status}`);
  const data = (await response.json()) as { token: string };
  authToken = data.token;
  window.localStorage.setItem("gether_token", data.token);
  return data.token;
}

async function requestHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${await ensureToken()}`,
    ...(extra ?? {}),
  };
}

async function request<T>(
  path: string,
  init?: RequestInit,
  retryAuth = true,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: await requestHeaders(init?.headers as Record<string, string> | undefined),
  });
  if (response.status === 401 && retryAuth) {
    await resetToken();
    return request<T>(path, init, false);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message ?? `HTTP ${response.status}`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  normalize: (url: string) =>
    request<VideoRef>("/internal/sources/normalize", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  discover: (
    question: string,
    mode: "fast" | "quality" = "quality",
    minViews?: number,
  ) =>
    request<DiscoverySession>("/api/discovery/sessions", {
      method: "POST",
      body: JSON.stringify({
        question,
        mode,
        ...(minViews != null ? { min_views: minViews } : {}),
      }),
    }),
  getSession: (sessionId: string) =>
    request<DiscoverySession>(`/api/discovery/sessions/${sessionId}`),
  confirm: (sessionId: string, selectedIds: string[]) =>
    request<{ session_id: string; task_id: string; status: string }>(
      `/api/discovery/sessions/${sessionId}/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ selected_ids: selectedIds }),
      },
    ),
  getTask: (taskId: string) =>
    request<TaskDetail>(`/api/tasks/${taskId}`),
  listTasks: (limit = 12) =>
    request<TaskHistoryItem[]>(`/api/tasks?limit=${limit}`),
  parseTask: (taskId: string) =>
    request<{ task_id: string; status: string; accepted: boolean }>(
      `/api/tasks/${taskId}/parse`,
      { method: "POST" },
    ),
  streamTaskEvents: (
    taskId: string,
    onEvent: (event: StreamEvent) => void,
    signal?: AbortSignal,
  ) => reportApi.streamTaskEvents(taskId, onEvent, signal),
};

export function formatDuration(ms: number | null): string {
  if (!ms) return "时长未知";
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatViews(views: number | null): string {
  if (views == null) return "播放未知";
  if (views >= 10000) return `${(views / 10000).toFixed(1)} 万`;
  return String(views);
}

export type OutlineSection = {
  id: string;
  title: string;
  points: string[];
};

export type Claim = {
  claim_id: string;
  section_id: string;
  text: string;
  qualifiers: Record<string, string>;
};

export type Conflict = {
  conflict_id: string;
  type: string;
  claim_ids: string[];
  evidence_ids: string[];
  summary: string | null;
  similarity?: number | null;
};

export type Citation = {
  evidence_id: string;
  video_id: string;
  start_ms: number;
  end_ms: number;
  modality: string;
  quote: string;
  url: string;
  source: string;
  source_video_id: string;
};

export type ReportData = {
  task_id: string;
  status: string;
  sections: { order_no: number; title: string; content_md: string; status: string }[];
  claims: Claim[];
  ledger: { claim_id: string; evidence_id: string | null; support_relation: string; conflict_type: string; verified: boolean }[];
  conflicts: Conflict[];
  citations?: Citation[];
};

export type StreamEvent = { id?: string; event: string; data: Record<string, unknown> };

export const reportApi = {
  plan: (taskId: string) =>
    request<{ task_id: string; status: string; outline: OutlineSection[]; warnings: string[] }>(
      `/api/tasks/${taskId}/plan`,
      { method: "POST" },
    ),
  report: (taskId: string) =>
    request<ReportData>(`/api/tasks/${taskId}/report`),
  streamTaskEvents: async (
    taskId: string,
    onEvent: (event: StreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    let lastEventId = "0";
    let delayMs = 1000;
    while (!signal?.aborted) {
      try {
        const response = await fetch(`${API_BASE}/api/tasks/${taskId}/events`, {
          headers: await requestHeaders({ "Last-Event-ID": lastEventId }),
          signal,
        });
        if (response.status === 401) {
          await resetToken();
          continue;
        }
        if (!response.ok || !response.body) {
          throw new Error(`SSE HTTP ${response.status}`);
        }
        delayMs = 1000;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let terminal = false;
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const idLine = block.split("\n").find((line) => line.startsWith("id: "));
            const eventLine = block.split("\n").find((line) => line.startsWith("event: "));
            const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
            if (idLine) lastEventId = idLine.slice(4).trim();
            if (eventLine && dataLine) {
              try {
                const data = JSON.parse(dataLine.slice(6)) as Record<string, unknown>;
                const event = eventLine.slice(7).trim();
                onEvent({ id: lastEventId, event, data });
                const status = typeof data.status === "string" ? data.status : "";
                if (
                  event === "parse_finished" ||
                  event === "done" ||
                  status === "PLANNING" ||
                  status === "DONE" ||
                  status === "FAILED"
                ) {
                  terminal = true;
                }
              } catch {
                // ignore malformed/heartbeat frames
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
          if (terminal || signal?.aborted) break;
        }
        if (terminal || signal?.aborted) return;
      } catch (err) {
        if (signal?.aborted) return;
        if (err instanceof Error && err.message.includes("401")) {
          await resetToken();
          continue;
        }
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        delayMs = Math.min(delayMs * 2, 10000);
      }
    }
  },
  resume: (taskId: string, outline: OutlineSection[]) =>
    request<{ task_id: string; status: string }>(`/api/tasks/${taskId}/resume`, {
      method: "POST",
      body: JSON.stringify({ outline, approved: true }),
    }),
  streamResume: async (
    taskId: string,
    outline: OutlineSection[],
    onEvent: (event: StreamEvent) => void,
  ): Promise<void> => {
    const init: RequestInit = {
      method: "POST",
      headers: await requestHeaders(),
      body: JSON.stringify({ outline, approved: true }),
    };
    let response = await fetch(`${API_BASE}/api/tasks/${taskId}/resume/stream`, init);
    if (response.status === 401) {
      await resetToken();
      response = await fetch(`${API_BASE}/api/tasks/${taskId}/resume/stream`, {
        ...init,
        headers: await requestHeaders(),
      });
    }
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body?.detail?.message ?? `HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const eventLine = block.split("\n").find((line) => line.startsWith("event: "));
        const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
        if (eventLine && dataLine) {
          try {
            onEvent({
              event: eventLine.slice(7).trim(),
              data: JSON.parse(dataLine.slice(6)),
            });
          } catch {
            // 忽略非 JSON 的心跳/空行
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  },
};
