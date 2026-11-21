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


