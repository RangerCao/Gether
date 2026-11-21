-- P3：Evidence Ledger 允许 no_evidence 条目；冲突表
ALTER TABLE ledger_entries ALTER COLUMN evidence_id DROP NOT NULL;

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
CREATE INDEX IF NOT EXISTS idx_conflicts_task ON conflicts (task_id, created_at DESC);
