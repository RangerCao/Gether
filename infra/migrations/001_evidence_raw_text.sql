-- 已有数据库升级：保存 ASR 纠错前原文
ALTER TABLE evidence_packets ADD COLUMN IF NOT EXISTS raw_text TEXT;
