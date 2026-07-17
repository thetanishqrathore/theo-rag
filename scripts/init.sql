-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- LAYER 1: INFRASTRUCTURE
-- ============================================================================

CREATE TABLE corpus (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO corpus (name, description) VALUES
('clough', 'Clough corpus'),
('dean', 'Dean corpus')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE file_tags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    corpus_id INTEGER NOT NULL REFERENCES corpus(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(corpus_id, name)
);

INSERT INTO file_tags (name, corpus_id)
SELECT 'transcripts', id FROM corpus WHERE name='clough'
ON CONFLICT DO NOTHING;

INSERT INTO file_tags (name, corpus_id)
SELECT 'course_notes', id FROM corpus WHERE name='clough'
ON CONFLICT DO NOTHING;

INSERT INTO file_tags (name, corpus_id)
SELECT 'mathew_series', id FROM corpus WHERE name='dean'
ON CONFLICT DO NOTHING;

INSERT INTO file_tags (name, corpus_id)
SELECT 'dean_bible_books', id FROM corpus WHERE name='dean'
ON CONFLICT DO NOTHING;

INSERT INTO file_tags (name, corpus_id)
SELECT 'dean_topical_html', id FROM corpus WHERE name='dean'
ON CONFLICT DO NOTHING;

INSERT INTO file_tags (name, corpus_id)
SELECT 'dean_topical_pdfs', id FROM corpus WHERE name='dean'
ON CONFLICT DO NOTHING;

INSERT INTO file_tags (name, corpus_id)
SELECT 'dean_topical_doc', id FROM corpus WHERE name='dean'
ON CONFLICT DO NOTHING;

INSERT INTO file_tags (name, corpus_id)
SELECT 'dean_topical_rtf', id FROM corpus WHERE name='dean'
ON CONFLICT DO NOTHING;

CREATE TABLE source_files (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_tag_id INTEGER NOT NULL REFERENCES file_tags(id) ON DELETE RESTRICT,
    filename TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    checksum TEXT NOT NULL UNIQUE,
    ingestion_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    source_format VARCHAR(20),
    source_kind VARCHAR(50),
    ingest_exclude_reason TEXT,
    error_log TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- LAYER 2: TRANSCRIPT METADATA (NORMALIZED)
-- ============================================================================

CREATE TABLE series (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    corpus_id INTEGER NOT NULL REFERENCES corpus(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(corpus_id, title)
);

CREATE TABLE parts (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    part_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(series_id, part_number)
);

CREATE TABLE chapters (
    id SERIAL PRIMARY KEY,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(part_id, chapter_number)
);

CREATE TABLE lessons (
    id SERIAL PRIMARY KEY,
    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    source_unit_key TEXT NOT NULL DEFAULT 'root',
    chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,
    lesson_number INTEGER NOT NULL,
    title TEXT,
    date DATE,
    speaker TEXT NOT NULL,
    location TEXT,
    transcriber TEXT,
    status VARCHAR(50) DEFAULT 'pending',
    validation_report JSONB,
    canonical_html_path TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(source_file_id, source_unit_key)
);

CREATE TABLE dean_collections (
    id SERIAL PRIMARY KEY,
    corpus_id INTEGER NOT NULL REFERENCES corpus(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,
    source_root TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(corpus_id, name)
);

CREATE TABLE dean_books (
    id SERIAL PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES dean_collections(id) ON DELETE CASCADE,
    book_slug VARCHAR(100) NOT NULL,
    display_name TEXT NOT NULL,
    parser_profile VARCHAR(100) NOT NULL DEFAULT 'default',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(collection_id, book_slug)
);

CREATE TABLE dean_lessons (
    id SERIAL PRIMARY KEY,
    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES dean_books(id) ON DELETE CASCADE,
    lesson_number INTEGER NOT NULL,
    lesson_code VARCHAR(64),
    title TEXT,
    source_filename TEXT NOT NULL,
    source_rel_path TEXT NOT NULL,
    date DATE,
    speaker TEXT NOT NULL,
    parser_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    metadata_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(source_file_id),
    UNIQUE(book_id, lesson_number)
);

CREATE TABLE dean_structured_docs (
    lesson_id INTEGER PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    parser_profile TEXT,
    selected_engine TEXT,
    structure_quality_score DOUBLE PRECISION,
    scan_confidence DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE dean_structured_nodes (
    id BIGSERIAL PRIMARY KEY,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    parent_node_id BIGINT REFERENCES dean_structured_nodes(id) ON DELETE CASCADE,
    node_path TEXT NOT NULL,
    node_type TEXT NOT NULL,
    depth INTEGER NOT NULL,
    order_index INTEGER NOT NULL,
    heading_level INTEGER,
    list_kind TEXT,
    list_ordinal INTEGER,
    text_plain TEXT NOT NULL,
    text_html TEXT,
    text_markdown TEXT,
    page_start INTEGER,
    page_end INTEGER,
    bbox_json JSONB,
    quality_flags JSONB
);

CREATE TABLE dean_structured_tables (
    id BIGSERIAL PRIMARY KEY,
    node_id BIGINT NOT NULL REFERENCES dean_structured_nodes(id) ON DELETE CASCADE,
    table_json JSONB NOT NULL,
    table_html TEXT
);

CREATE TABLE course_note_volumes (
    id SERIAL PRIMARY KEY,
    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE UNIQUE,
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    part_number INTEGER NOT NULL,
    part_title TEXT NOT NULL,
    volume_code VARCHAR(64) NOT NULL,
    page_count INTEGER NOT NULL,
    metadata_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE course_note_units (
    id SERIAL PRIMARY KEY,
    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    logical_lesson_number INTEGER NOT NULL,
    unit_title TEXT,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    boundary_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    metadata_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_file_id, logical_lesson_number)
);

CREATE TABLE course_note_nodes (
    id BIGSERIAL PRIMARY KEY,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    parent_node_id BIGINT REFERENCES course_note_nodes(id) ON DELETE CASCADE,
    node_path TEXT NOT NULL,
    node_type TEXT NOT NULL,
    depth INTEGER NOT NULL,
    order_index INTEGER NOT NULL,
    heading_level INTEGER,
    list_kind TEXT,
    list_ordinal INTEGER,
    text_plain TEXT NOT NULL,
    text_html TEXT,
    text_markdown TEXT,
    page_start INTEGER,
    page_end INTEGER,
    bbox_json JSONB,
    quality_flags JSONB
);

CREATE TABLE course_note_tables (
    id BIGSERIAL PRIMARY KEY,
    node_id BIGINT NOT NULL REFERENCES course_note_nodes(id) ON DELETE CASCADE,
    table_json JSONB NOT NULL,
    table_html TEXT
);

ALTER TABLE lessons
ADD COLUMN dean_lesson_id INTEGER REFERENCES dean_lessons(id) ON DELETE SET NULL;

-- ============================================================================
-- LAYER 3: CONTENT & RETRIEVAL
-- ============================================================================

CREATE TABLE paragraphs (
    id SERIAL PRIMARY KEY,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    paragraph_index INTEGER NOT NULL,
    hierarchy_path TEXT NOT NULL,
    content TEXT NOT NULL,
    html_content TEXT,
    is_junk BOOLEAN DEFAULT FALSE,
    is_question BOOLEAN DEFAULT FALSE,
    bible_refs TEXT[],
    word_count INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(lesson_id, paragraph_index)
);

CREATE TABLE parent_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    citation_ref TEXT NOT NULL,
    start_para_index INTEGER NOT NULL,
    end_para_index INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    section_title TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    CHECK (start_para_index <= end_para_index)
);

CREATE TABLE child_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parent_chunk_id UUID NOT NULL REFERENCES parent_chunks(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    embedding vector(1024),
    corpus_id INTEGER REFERENCES corpus(id) ON DELETE SET NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(parent_chunk_id, chunk_index)
);

CREATE TABLE child_chunk_audit (
    child_chunk_id UUID PRIMARY KEY REFERENCES child_chunks(id) ON DELETE CASCADE,
    content_hash VARCHAR(64) NOT NULL,
    char_len INTEGER NOT NULL,
    word_count INTEGER NOT NULL,
    token_est INTEGER NOT NULL,
    embed_model TEXT NOT NULL,
    embed_dim INTEGER NOT NULL,
    embed_version VARCHAR(20) NOT NULL,
    experiment_tag VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX idx_source_files_tag ON source_files(file_tag_id);
CREATE INDEX idx_lessons_status ON lessons(status);
CREATE UNIQUE INDEX idx_lessons_dean_lesson ON lessons(dean_lesson_id) WHERE dean_lesson_id IS NOT NULL;
CREATE INDEX idx_dean_collections_corpus ON dean_collections(corpus_id);
CREATE INDEX idx_dean_books_collection ON dean_books(collection_id);
CREATE INDEX idx_dean_lessons_book ON dean_lessons(book_id);
CREATE INDEX idx_dean_structured_nodes_lesson ON dean_structured_nodes(lesson_id);
CREATE INDEX idx_dean_structured_nodes_lesson_order ON dean_structured_nodes(lesson_id, order_index);
CREATE INDEX idx_dean_structured_nodes_path ON dean_structured_nodes(lesson_id, node_path);
CREATE INDEX idx_dean_structured_nodes_parent ON dean_structured_nodes(parent_node_id);
CREATE INDEX idx_course_note_units_source ON course_note_units(source_file_id, logical_lesson_number);
CREATE INDEX idx_course_note_units_lesson ON course_note_units(lesson_id);
CREATE INDEX idx_course_note_nodes_lesson_order ON course_note_nodes(lesson_id, order_index);
CREATE INDEX idx_course_note_nodes_path ON course_note_nodes(lesson_id, node_path);
CREATE INDEX idx_course_note_nodes_parent ON course_note_nodes(parent_node_id);
CREATE INDEX idx_paragraphs_lesson ON paragraphs(lesson_id);
CREATE INDEX idx_parent_chunks_lesson ON parent_chunks(lesson_id);
CREATE INDEX idx_parent_chunks_metadata_gin ON parent_chunks USING GIN (metadata_json);
CREATE INDEX idx_child_chunks_parent ON child_chunks(parent_chunk_id);
CREATE INDEX idx_child_chunks_parent_index ON child_chunks(parent_chunk_id, chunk_index);
CREATE INDEX idx_child_chunks_corpus ON child_chunks(corpus_id);
CREATE INDEX idx_child_chunks_metadata_gin ON child_chunks USING GIN (metadata_json);
CREATE INDEX idx_child_chunks_content_tsv ON child_chunks USING GIN (to_tsvector('english', content));
CREATE INDEX idx_child_chunk_audit_hash ON child_chunk_audit(content_hash);

-- Backward-compatibility views for legacy transcript_* names
CREATE VIEW transcript_parts AS SELECT * FROM parts;
CREATE VIEW transcript_chapters AS SELECT * FROM chapters;
CREATE VIEW transcript_lessons AS SELECT * FROM lessons;
CREATE VIEW transcript_paragraphs AS SELECT * FROM paragraphs;
CREATE VIEW transcript_parent_chunks AS SELECT * FROM parent_chunks;
CREATE VIEW transcript_child_chunks AS SELECT * FROM child_chunks;
CREATE VIEW transcript_child_chunk_audit AS SELECT * FROM child_chunk_audit;

-- Backward-compatibility views for topical structured tables.
CREATE VIEW dean_topical_pdf_docs AS SELECT * FROM dean_structured_docs;
CREATE VIEW dean_topical_pdf_nodes AS SELECT * FROM dean_structured_nodes;
CREATE VIEW dean_topical_pdf_tables AS SELECT * FROM dean_structured_tables;

-- Corpus-segregated operational views
CREATE VIEW dean_lessons_view AS
SELECT l.*
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean';

CREATE VIEW clough_lessons_view AS
SELECT l.*
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'clough';

CREATE VIEW dean_parent_chunks_view AS
SELECT pc.*
FROM parent_chunks pc
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean';

CREATE VIEW clough_parent_chunks_view AS
SELECT pc.*
FROM parent_chunks pc
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'clough';

CREATE VIEW dean_child_chunks_view AS
SELECT cc.*
FROM child_chunks cc
JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean';

CREATE VIEW clough_child_chunks_view AS
SELECT cc.*
FROM child_chunks cc
JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'clough';

-- ============================================================================
-- TELEMETRY (RAG QUERY EVALUATION)
-- ============================================================================

CREATE TABLE rag_query_runs (
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
    union_candidates_pretrim_count INTEGER,
    union_candidates_posttrim_count INTEGER,
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
    rag_version TEXT,
    status TEXT
);

CREATE TABLE rag_rerank (
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

CREATE TABLE rag_evidence_used (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES rag_query_runs(run_id) ON DELETE CASCADE,
    evidence_id UUID,
    parent_chunk_id UUID,
    citation_ref TEXT,
    position INTEGER,
    context_only BOOLEAN,
    matched_in_text BOOLEAN
);

CREATE INDEX idx_rag_query_runs_created_at ON rag_query_runs(created_at);
CREATE INDEX idx_rag_query_runs_corpus ON rag_query_runs(corpus);
CREATE INDEX idx_rag_query_runs_model_gen ON rag_query_runs(model_gen);
CREATE INDEX idx_rag_query_runs_answer_mode ON rag_query_runs(answer_mode);
CREATE INDEX idx_rag_query_runs_grounded ON rag_query_runs(grounded);
CREATE INDEX idx_rag_query_runs_refusal ON rag_query_runs(refusal);

CREATE INDEX idx_rag_rerank_run_id ON rag_rerank(run_id);
CREATE INDEX idx_rag_rerank_parent_id ON rag_rerank(parent_chunk_id);

CREATE INDEX idx_rag_evidence_used_run_id ON rag_evidence_used(run_id);
