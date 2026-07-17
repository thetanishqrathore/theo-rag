-- Add topical PDF semantic structure tables.
-- These are additive and keep compatibility with shared lesson/paragraph/chunk tables.

CREATE TABLE IF NOT EXISTS dean_topical_pdf_docs (
    lesson_id INTEGER PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    parser_profile TEXT,
    selected_engine TEXT,
    structure_quality_score DOUBLE PRECISION,
    scan_confidence DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dean_topical_pdf_nodes (
    id BIGSERIAL PRIMARY KEY,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    parent_node_id BIGINT REFERENCES dean_topical_pdf_nodes(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS dean_topical_pdf_tables (
    id BIGSERIAL PRIMARY KEY,
    node_id BIGINT NOT NULL REFERENCES dean_topical_pdf_nodes(id) ON DELETE CASCADE,
    table_json JSONB NOT NULL,
    table_html TEXT
);

CREATE INDEX IF NOT EXISTS idx_dean_topical_pdf_nodes_lesson
ON dean_topical_pdf_nodes(lesson_id);

CREATE INDEX IF NOT EXISTS idx_dean_topical_pdf_nodes_lesson_order
ON dean_topical_pdf_nodes(lesson_id, order_index);

CREATE INDEX IF NOT EXISTS idx_dean_topical_pdf_nodes_path
ON dean_topical_pdf_nodes(lesson_id, node_path);

CREATE INDEX IF NOT EXISTS idx_dean_topical_pdf_nodes_parent
ON dean_topical_pdf_nodes(parent_node_id);

