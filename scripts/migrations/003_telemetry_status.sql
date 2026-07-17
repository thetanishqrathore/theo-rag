-- Add status column for telemetry runs
ALTER TABLE rag_query_runs ADD COLUMN IF NOT EXISTS status TEXT;
