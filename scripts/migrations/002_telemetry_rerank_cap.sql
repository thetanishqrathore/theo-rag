-- Add pre/post trim counts for candidate union
ALTER TABLE rag_query_runs ADD COLUMN IF NOT EXISTS union_candidates_pretrim_count INTEGER;
ALTER TABLE rag_query_runs ADD COLUMN IF NOT EXISTS union_candidates_posttrim_count INTEGER;
