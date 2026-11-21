# Gether 核心协议（P0 冻结）

> 本文件是前后端、Java 与 Python 之间的契约。任何字段变更必须先改本文件，再改代码。
> 约定：时间统一为**毫秒整数**；ID 统一为 UUID 字符串；JSON 字段统一 `snake_case`。

---

## 一、任务状态机

```text
PENDING ──► PARSING ──► PLANNING ──► GENERATING ──► DONE
   │           │            │             │
   └───────────┴────────────┴─────────────┴──► FAILED
   └───────────┴────────────┴─────────────┴──► CANCELLED
```

| 状态 | 含义 | 允许的下一状态 |
|---|---|---|
| `PENDING` | 任务已创建，等待调度 | `PARSING` / `FAILED` / `CANCELLED` |
| `PARSING` | 下载、字幕/ASR、抽帧/OCR、建索引 | `PLANNING` / `FAILED` / `CANCELLED` |
| `PLANNING` | 已生成大纲，等待用户确认（HITL 暂停） | `GENERATING` / `FAILED` / `CANCELLED` |
| `GENERATING` | 检索、Critic、写作、裁决、流式输出 | `DONE` / `FAILED` / `CANCELLED` |
| `DONE` | 完成，只读 | — |
| `FAILED` | 失败，可重试（重试回到 `PENDING`） | `PENDING` |
| `CANCELLED` | 用户取消，只读 | — |

规则：非法迁移一律拒绝并记审计日志；每次迁移 `version + 1`（乐观锁）。

---

## 二、SSE 事件协议

连接：`GET /api/tasks/{task_id}/events`，`Accept: text/event-stream`。
每条事件：

```text
id: 42
event: token
data: {"task_id":"...","seq":42,"ts":1730000000000,"payload":{...}}

```

### 2.1 统一信封

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 任务 UUID |
| `seq` | int | 任务内单调递增，用于去重与断线重放 |
| `ts` | int | 服务端毫秒时间戳 |
| `payload` | object | 事件体，见下 |

### 2.2 事件类型

| event | 时机 | payload |
|---|---|---|
| `state` | 状态迁移 | `{ "from": "PARSING", "to": "PLANNING", "reason": "outline_ready" }` |
| `progress` | 解析/生成进度 | `{ "stage": "asr", "current": 3, "total": 10, "message": "转写第 3/10 段" }` |
| `outline` | 大纲就绪，等待确认 | `{ "outline_id": "...", "sections": [{ "id": "s1", "title": "...", "points": ["..."] }] }` |
| `token` | 正文增量 | `{ "section_id": "s1", "delta": "……" }` |
| `citation` | 一条引用被确认 | `{ "claim_id": "...", "evidence_id": "...", "video_id": "...", "start_ms": 12000, "end_ms": 18000, "modality": "asr", "quote": "..." }` |
| `conflict` | 发现 claim 级冲突 | `{ "conflict_id": "...", "type": "direct_contradiction", "claim_ids": ["...","..."], "evidence_ids": ["...","..."], "summary": "..." }` |
| `heartbeat` | 心跳（默认 15s） | `{}` |
| `error` | 可恢复/不可恢复错误 | `{ "code": "LLM_UNAVAILABLE", "message": "...", "retryable": true }` |
| `done` | 正常结束 | `{ "status": "DONE", "report_id": "..." }` |

### 2.3 断线重连

- 客户端重连时带 `Last-Event-ID: <seq>`。
- 服务端保留最近 `SSE_REPLAY_BUFFER_SIZE`（默认 200）条事件，从 `seq + 1` 开始重放。
- 重放结束发一条 `state` 事件同步当前状态。
- 心跳缺失超过 30s，客户端主动重连。

---

## 三、内部执行 API（Java → Python）

前缀 `/internal`，均需 `X-Request-Id` 与 HMAC 签名头。

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/health` | 存活与依赖自检 |
| `POST` | `/internal/tasks/{task_id}/parse` | 触发解析（异步） |
| `POST` | `/internal/tasks/{task_id}/plan` | 生成大纲并暂停等待确认 |
| `POST` | `/internal/tasks/{task_id}/resume` | 确认/修改大纲后继续生成 |
| `POST` | `/internal/tasks/{task_id}/resume/stream` | SSE 流式生成（state/token/conflict/citation/done） |
| `GET` | `/internal/tasks/{task_id}/report` | 查询研报、Claim、Ledger、冲突 |
| `POST` | `/internal/tasks/{task_id}/cancel` | 取消（预留） |
| `GET` | `/internal/tasks/{task_id}/status` | 查询内部阶段与进度（预留） |
| `POST` | `/internal/tasks/{task_id}/index` | 分块 + Embedding + 写入 pgvector |
| `GET` | `/internal/tasks/{task_id}/chunks` | 查看检索分块 |
| `POST` | `/internal/retrieve` | 混合检索（向量 + BM25 + RRF） |

### 3.1 任务接口示例（P1 已实现）

```http
POST /internal/tasks
Content-Type: application/json

{"videos": ["https://www.bilibili.com/video/BV1xx411c7mD"], "user_id": null}
```

```json
201 {"task_id": "uuid", "status": "PENDING"}
```

```http
POST /internal/tasks/{task_id}/parse
```

```json
202 {"task_id": "uuid", "status": "PARSING", "accepted": true}
```

```http
GET /internal/tasks/{task_id}
```

```json
{
  "task_id": "uuid",
  "status": "PLANNING",
  "progress": 90,
  "evidence_count": 236,
  "videos": [{"id": "uuid", "source": "bilibili", "source_video_id": "BV..."}]
}
```

```http
GET /internal/tasks/{task_id}/evidence?limit=500
```

```json
{"task_id": "uuid", "count": 236, "evidence": [EvidencePacket, ...]}
```

```http
POST /internal/tasks/{task_id}/index
```

```json
{"task_id": "uuid", "evidence_count": 236, "chunk_count": 48, "inserted": 48,
 "embedding_model": "BAAI/bge-small-zh-v1.5", "embedding_dim": 512}
```

```http
POST /internal/tasks/{task_id}/plan
```

```json
{"task_id": "uuid", "status": "WAITING_APPROVAL",
 "outline": [{"id": "s1", "title": "...", "points": ["..."]}]}
```

```http
POST /internal/tasks/{task_id}/resume
Content-Type: application/json

{"outline": [{"id": "s1", "title": "...", "points": ["..."]}], "approved": true}
```

```json
{"task_id": "uuid", "status": "DONE", "claims": 38, "ledger": 48,
 "conflicts": 0, "citation_stats": {"citations_total": 41, "citations_valid": 41, "citations_invalid": 0}}
```

```http
POST /internal/tasks/{task_id}/resume/stream
Content-Type: application/json

{"outline": [...], "approved": true}
```

```text
event: state
data: {"node":"writer"}

event: token
data: {"section_id":"s1","delta":"章节正文..."}

event: conflict
data: {"type":"numeric_mismatch","claim_ids":["..."],"evidence_ids":["..."]}

event: citation
data: {"claim_id":"...","evidence_id":"...","verified":true}

event: done
data: {"status":"DONE","claims":38,"ledger":48,"conflicts":0}
```

```http
GET /internal/tasks/{task_id}/report
```

```json
{"task_id": "uuid", "status": "DONE", "sections": [...],
 "claims": [...], "ledger": [...], "conflicts": [...]}
```

```http
POST /internal/retrieve
Content-Type: application/json

{"task_id": "uuid", "query": "杭州房租为什么高", "top_k": 10}
```

```json
{"task_id": "uuid", "query": "杭州房租为什么高", "count": 3,
 "hits": [{"chunk_id": "uuid", "text": "...", "start_ms": 90000, "end_ms": 125000,
            "score": 0.032, "vector_rank": 1, "bm25_rank": 2,
            "vector_score": 0.78, "bm25_score": 4.1}]}
```

约定：

- `POST /parse` 立即返回 202，解析在后台执行；首次状态迁移为 `PENDING → PARSING`。
- 解析完成（至少一条证据）后进入 `PLANNING`，`progress=90`。
- 无任何证据时进入 `FAILED`，`error_code=NO_EVIDENCE`。
- 同一任务重复解析由 `(video_id, content_hash)` 唯一索引保证幂等。

### 3.2 回调（Python → Java）

`POST /api/internal/callbacks/{task_id}`

```json
{
  "task_id": "...",
  "event": "parse_finished",
  "seq": 12,
  "payload": { "evidence_count": 236 },
  "ts": 1730000000000
}
```

签名：`X-Signature = HMAC-SHA256(secret, timestamp + "." + raw_body)`，`X-Timestamp` 允许 ±300s。
幂等：`task_id + event + seq` 唯一，重复回调只生效一次。

---

## 四、核心数据契约

### 4.1 VideoRef

```json
{
  "source": "bilibili",
  "source_video_id": "BV1xx411c7mD",
  "page": 1,
  "url": "https://www.bilibili.com/video/BV1xx411c7mD",
  "title": "……",
  "duration_ms": 2412000
}
```

`source` 枚举：`bilibili` / `youtube` / `local`（`local` 仅测试与 Demo）。

### 4.2 EvidencePacket

```json
{
  "evidence_id": "uuid",
  "task_id": "uuid",
  "video_id": "uuid",
  "modality": "asr",
  "text": "我认为明年该行业增速会回落到 5% 左右。",
  "raw_text": null,
  "start_ms": 132000,
  "end_ms": 138500,
  "frame_ref": null,
  "confidence": 0.93,
  "source_type": "native_sub",
  "content_hash": "sha256:...",
  "created_at": "2025-01-01T00:00:00Z"
}
```

| 字段 | 约束 |
|---|---|
| `modality` | `asr` / `ocr` / `frame` |
| `source_type` | `native_sub` / `auto_sub` / `whisper` / `ocr` |
| `start_ms` | `0 <= start_ms < end_ms` |
| `frame_ref` | `modality=ocr` 时为对象存储 key，否则 `null` |
| `content_hash` | 用于去重，相同视频重复解析不得产生重复证据 |
| `raw_text` | ASR 纠错前原文；未纠错时为 `null`，用于审计 |

### 4.3 Claim

```json
{
  "claim_id": "uuid",
  "task_id": "uuid",
  "section_id": "s2",
  "text": "该行业明年增速将回落至 5%",
  "qualifiers": {
    "subject": "该行业",
    "time": "2026 年",
    "condition": "政策不出现大规模刺激",
    "scope": "中国市场"
  },
  "evidence_ids": ["uuid", "uuid"],
  "created_at": "2025-01-01T00:00:00Z"
}
```

**硬约束**：`qualifiers` 任一维度不同，禁止与另一 claim 合并（避免“删除冲突”）。

### 4.4 LedgerEntry

```json
{
  "ledger_id": "uuid",
  "claim_id": "uuid",
  "evidence_id": "uuid",
  "support_relation": "rebut",
  "trust_score": 0.72,
  "conflict_type": "numeric_mismatch",
  "rationale": "同一时间范围、同一对象，数值 5% vs 12%",
  "verified": true,
  "created_at": "2025-01-01T00:00:00Z"
}
```

| 字段 | 枚举 |
|---|---|
| `support_relation` | `support` / `rebut` / `mixed` / `no_evidence` |
| `conflict_type` | `direct_contradiction` / `numeric_mismatch` / `condition_diff` / `time_diff` / `none` |
| `verified` | Evidence Guard 确定性校验是否通过 |

### 4.5 大纲与章节

```json
{
  "outline_id": "uuid",
  "sections": [
    { "id": "s1", "title": "行业现状", "points": ["市场规模", "增速变化"] }
  ]
}
```

---

## 五、LLM 角色契约

业务代码只调用角色，不直接依赖厂商 SDK：

```python
class LLMRole(str, Enum):
    PLANNER = "planner"
    CRITIC = "critic"
    EXTRACT = "extract"
    WRITER = "writer"
    ROUTER = "router"
    QUERY_REWRITE = "query_rewrite"

class LLMClient(Protocol):
    async def complete(self, role: LLMRole, messages: list[Message], *, json_mode: bool = False) -> str: ...
    async def stream(self, role: LLMRole, messages: list[Message]) -> AsyncIterator[str]: ...
```

供应商枚举：`deepseek` / `dashscope` / `openai` / `ollama` / `mock`。
解析顺序：`LLM_MODEL_<ROLE>` → `<PROVIDER>_MODEL_<ROLE>` → `<PROVIDER>_MODEL_DEFAULT` → 内置默认。
未启用的供应商不校验 Key、不初始化客户端。

---

## 六、错误码

| code | 含义 | 可重试 |
|---|---|---|
| `INVALID_URL` | 不支持的链接/来源 | 否 |
| `VIDEO_TOO_LONG` | 超过时长上限 | 否 |
| `DOWNLOAD_FAILED` | 下载失败/风控 | 是 |
| `SUBTITLE_MISSING` | 无字幕（自动降级 ASR，不直接报错） | — |
| `ASR_FAILED` | 转写失败 | 是 |
| `OCR_FAILED` | OCR 失败（可降级） | 是 |
| `RETRIEVAL_FAILED` | 检索失败 | 是 |
| `LLM_UNAVAILABLE` | 所有 LLM 不可用 | 是 |
| `LLM_OUTPUT_INVALID` | 结构化输出非法 | 是 |
| `EVIDENCE_UNVERIFIED` | 引用校验失败（非致命） | 否 |
| `TASK_CANCELLED` | 用户取消 | 否 |
| `INTERNAL_ERROR` | 未分类异常 | 视情况 |

---

## 七、变更流程

1. 修改本文件并注明版本与日期。
2. 同步修改 Python 的 `app/contracts`、Java 的 DTO、前端的 `src/types`。
3. 跑 `scripts/dev.sh test` 与契约测试。
4. 破坏性变更必须提供迁移说明。
