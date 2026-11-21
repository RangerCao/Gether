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
