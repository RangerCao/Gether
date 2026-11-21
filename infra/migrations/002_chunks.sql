-- P2：检索分块与向量索引
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
CREATE INDEX IF NOT EXISTS idx_chunks_task_time ON chunks (task_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
