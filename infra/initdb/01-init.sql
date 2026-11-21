-- Gether 初始化脚本（P0）
-- 仅执行一次：docker volume 首次创建时由 postgres 镜像自动运行

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------- 任务 ----------
CREATE TABLE IF NOT EXISTS tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID,
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','PARSING','PLANNING','GENERATING','DONE','FAILED','CANCELLED')),
    progress        INT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    input_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code      TEXT,
    error_message   TEXT,
    version         BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_created
    ON tasks (status, created_at DESC);

-- ---------- 视频 ----------
CREATE TABLE IF NOT EXISTS videos (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id          UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    source           TEXT NOT NULL CHECK (source IN ('bilibili','youtube','local')),
    source_video_id  TEXT NOT NULL,
    page             INT NOT NULL DEFAULT 1,
    url              TEXT NOT NULL,
    title            TEXT,
    duration_ms      BIGINT,
    meta_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, source, source_video_id, page)
);

-- ---------- 证据包 ----------
CREATE TABLE IF NOT EXISTS evidence_packets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id       UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    video_id      UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    modality      TEXT NOT NULL CHECK (modality IN ('asr','ocr','frame')),
    text          TEXT NOT NULL,
    raw_text      TEXT,
    start_ms      BIGINT NOT NULL CHECK (start_ms >= 0),
    end_ms        BIGINT NOT NULL,
    frame_ref     TEXT,
    confidence    REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    source_type   TEXT NOT NULL CHECK (source_type IN ('native_sub','auto_sub','whisper','ocr')),
    content_hash  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_ms > start_ms)
);

CREATE INDEX IF NOT EXISTS idx_evidence_video_time
    ON evidence_packets (video_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_evidence_task_modality
    ON evidence_packets (task_id, modality);
CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_dedup
    ON evidence_packets (video_id, content_hash);

-- ---------- Claims ----------
CREATE TABLE IF NOT EXISTS claims (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    section_id      TEXT NOT NULL,
    text            TEXT NOT NULL,
    qualifiers_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_claims_task_section
    ON claims (task_id, section_id);

-- ---------- Evidence Ledger ----------
CREATE TABLE IF NOT EXISTS ledger_entries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id         UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    evidence_id      UUID REFERENCES evidence_packets(id) ON DELETE CASCADE,
    support_relation TEXT NOT NULL
                     CHECK (support_relation IN ('support','rebut','mixed','no_evidence')),
    trust_score      REAL NOT NULL DEFAULT 0.5 CHECK (trust_score BETWEEN 0 AND 1),
    conflict_type    TEXT NOT NULL DEFAULT 'none'
                     CHECK (conflict_type IN ('direct_contradiction','numeric_mismatch','condition_diff','time_diff','none')),
    rationale        TEXT,
    verified         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (claim_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_ledger_claim
    ON ledger_entries (claim_id);

-- ---------- 研报章节 ----------
CREATE TABLE IF NOT EXISTS report_sections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    order_no    INT NOT NULL,
    title       TEXT NOT NULL,
    content_md  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','GENERATING','DONE','FAILED')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, order_no)
);

-- ---------- 检索分块 ----------
CREATE TABLE IF NOT EXISTS chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id       UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    video_id      UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    text          TEXT NOT NULL,
    start_ms      BIGINT NOT NULL CHECK (start_ms >= 0),
    end_ms        BIGINT NOT NULL,
    evidence_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    modalities    TEXT[] NOT NULL DEFAULT '{}',
    content_hash  TEXT NOT NULL,
    embedding     vector(512),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_ms > start_ms),
    UNIQUE (task_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_chunks_task_time
    ON chunks (task_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- ---------- 视频发现（P2.6） ----------
CREATE TABLE IF NOT EXISTS discovery_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID REFERENCES tasks(id) ON DELETE SET NULL,
    question        TEXT NOT NULL,
    constraints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    queries_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    facets_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
    status          TEXT NOT NULL DEFAULT 'DISCOVERING'
                    CHECK (status IN ('DISCOVERING','WAITING_VIDEO_SELECTION','CONFIRMED','FAILED')),
    warnings_json   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS discovery_candidates (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID NOT NULL REFERENCES discovery_sessions(id) ON DELETE CASCADE,
    source            TEXT NOT NULL,
    source_video_id   TEXT NOT NULL,
    title             TEXT NOT NULL,
    url               TEXT NOT NULL,
    duration_ms       BIGINT,
    uploader          TEXT,
    published_at      BIGINT,
    views             BIGINT,
    metrics_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    matched_facets    JSONB NOT NULL DEFAULT '[]'::jsonb,
    relevance         REAL NOT NULL DEFAULT 0,
    authority         REAL NOT NULL DEFAULT 0,
    freshness         REAL NOT NULL DEFAULT 0,
    redundancy        REAL NOT NULL DEFAULT 0,
    final_score       REAL NOT NULL DEFAULT 0,
    cluster_id        TEXT,
    selected          BOOLEAN NOT NULL DEFAULT FALSE,
    reason            TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, source, source_video_id)
);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_session
    ON discovery_candidates (session_id, selected, final_score DESC);

-- ---------- 冲突裁决 ----------
CREATE TABLE IF NOT EXISTS conflicts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id       UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    type          TEXT NOT NULL,
    claim_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary       TEXT,
    similarity    REAL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conflicts_task
    ON conflicts (task_id, created_at DESC);

-- ---------- Java 回调幂等 ----------
CREATE TABLE IF NOT EXISTS task_callbacks (
    id          BIGSERIAL PRIMARY KEY,
    task_id     UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    seq         BIGINT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, event_type, seq)
);

CREATE INDEX IF NOT EXISTS idx_task_callbacks_task
    ON task_callbacks (task_id, created_at DESC);

-- ---------- 审计（状态迁移 / 回调幂等） ----------
CREATE TABLE IF NOT EXISTS audit_events (
    id          BIGSERIAL PRIMARY KEY,
    task_id     UUID,
    event_type  TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_task
    ON audit_events (task_id, created_at DESC);

-- ---------- updated_at 触发器 ----------
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tasks_touch ON tasks;
CREATE TRIGGER trg_tasks_touch BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS trg_report_sections_touch ON report_sections;
CREATE TRIGGER trg_report_sections_touch BEFORE UPDATE ON report_sections
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
