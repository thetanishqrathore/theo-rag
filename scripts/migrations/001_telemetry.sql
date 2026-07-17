-- Telemetry tables for RAG query evaluation

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS rag_query_runs (
    run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    endpoint TEXT NOT NULL,
    user_session_id TEXT,
    corpus TEXT NOT NULL,
    query_text TEXT,
    query_hash TEXT NOT NULL,
    model_gen TEXT,
    model_embed TEXT,
    model_rerank TEXT,
    answer_mode TEXT,
    grounded BOOLEAN,
    refusal BOOLEAN,
    top_k_requested INTEGER,
    returned_citations_count INTEGER,
    bm25_candidates_count INTEGER,
    vector_candidates_count INTEGER,
    union_candidates_count INTEGER,
    reranked_count INTEGER,
    num_unique_parents_pre INTEGER,
    num_unique_parents_final INTEGER,
    num_citations_emitted INTEGER,
    num_citations_matched INTEGER,
    lexical_overlap_top DOUBLE PRECISION,
    s1 DOUBLE PRECISION,
    s5 DOUBLE PRECISION,
    t_retrieve_ms DOUBLE PRECISION,
    t_rerank_ms DOUBLE PRECISION,
    t_expand_ms DOUBLE PRECISION,
    t_generate_ms DOUBLE PRECISION,
    t_post_ms DOUBLE PRECISION,
    t_total_ms DOUBLE PRECISION,
    diversity_cap_enabled BOOLEAN,
    local_expansion_enabled BOOLEAN,
    error_type TEXT,
    error_message TEXT,
    rag_version TEXT
);

CREATE TABLE IF NOT EXISTS rag_rerank (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES rag_query_runs(run_id) ON DELETE CASCADE,
    child_chunk_id UUID,
    parent_chunk_id UUID,
    rank INTEGER NOT NULL,
    rerank_score DOUBLE PRECISION,
    is_primary BOOLEAN,
    context_only BOOLEAN,
    selected_final BOOLEAN
);

CREATE TABLE IF NOT EXISTS rag_evidence_used (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES rag_query_runs(run_id) ON DELETE CASCADE,
    evidence_id UUID,
    parent_chunk_id UUID,
    citation_ref TEXT,
    position INTEGER,
    context_only BOOLEAN,
    matched_in_text BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_rag_query_runs_created_at ON rag_query_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_rag_query_runs_corpus ON rag_query_runs(corpus);
CREATE INDEX IF NOT EXISTS idx_rag_query_runs_model_gen ON rag_query_runs(model_gen);
CREATE INDEX IF NOT EXISTS idx_rag_query_runs_answer_mode ON rag_query_runs(answer_mode);
CREATE INDEX IF NOT EXISTS idx_rag_query_runs_grounded ON rag_query_runs(grounded);
CREATE INDEX IF NOT EXISTS idx_rag_query_runs_refusal ON rag_query_runs(refusal);

CREATE INDEX IF NOT EXISTS idx_rag_rerank_run_id ON rag_rerank(run_id);
CREATE INDEX IF NOT EXISTS idx_rag_rerank_parent_id ON rag_rerank(parent_chunk_id);

CREATE INDEX IF NOT EXISTS idx_rag_evidence_used_run_id ON rag_evidence_used(run_id);
