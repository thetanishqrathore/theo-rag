-- Dean structured content unification:
-- 1) Generic structured tables for all Dean node-backed formats
-- 2) Backward-compatible views for dean_topical_pdf_* names
-- 3) Chunk metadata columns for retrieval provenance

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.dean_structured_docs') IS NULL THEN
        IF to_regclass('public.dean_topical_pdf_docs') IS NOT NULL THEN
            EXECUTE 'ALTER TABLE public.dean_topical_pdf_docs RENAME TO dean_structured_docs';
        ELSE
            EXECUTE '
                CREATE TABLE public.dean_structured_docs (
                    lesson_id INTEGER PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
                    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
                    parser_profile TEXT,
                    selected_engine TEXT,
                    structure_quality_score DOUBLE PRECISION,
                    scan_confidence DOUBLE PRECISION,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )';
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.dean_structured_nodes') IS NULL THEN
        IF to_regclass('public.dean_topical_pdf_nodes') IS NOT NULL THEN
            EXECUTE 'ALTER TABLE public.dean_topical_pdf_nodes RENAME TO dean_structured_nodes';
        ELSE
            EXECUTE '
                CREATE TABLE public.dean_structured_nodes (
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
                )';
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.dean_structured_tables') IS NULL THEN
        IF to_regclass('public.dean_topical_pdf_tables') IS NOT NULL THEN
            EXECUTE 'ALTER TABLE public.dean_topical_pdf_tables RENAME TO dean_structured_tables';
        ELSE
            EXECUTE '
                CREATE TABLE public.dean_structured_tables (
                    id BIGSERIAL PRIMARY KEY,
                    node_id BIGINT NOT NULL REFERENCES dean_structured_nodes(id) ON DELETE CASCADE,
                    table_json JSONB NOT NULL,
                    table_html TEXT
                )';
        END IF;
    END IF;
END $$;

ALTER TABLE public.parent_chunks
ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.child_chunks
ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_dean_structured_nodes_lesson
ON public.dean_structured_nodes(lesson_id);

CREATE INDEX IF NOT EXISTS idx_dean_structured_nodes_lesson_order
ON public.dean_structured_nodes(lesson_id, order_index);

CREATE INDEX IF NOT EXISTS idx_dean_structured_nodes_path
ON public.dean_structured_nodes(lesson_id, node_path);

CREATE INDEX IF NOT EXISTS idx_dean_structured_nodes_parent
ON public.dean_structured_nodes(parent_node_id);

CREATE INDEX IF NOT EXISTS idx_parent_chunks_metadata_gin
ON public.parent_chunks USING GIN (metadata_json);

CREATE INDEX IF NOT EXISTS idx_child_chunks_metadata_gin
ON public.child_chunks USING GIN (metadata_json);

DO $$
DECLARE
    docs_kind "char";
BEGIN
    IF to_regclass('public.dean_topical_pdf_docs') IS NOT NULL THEN
        SELECT relkind
        INTO docs_kind
        FROM pg_class
        WHERE oid = 'public.dean_topical_pdf_docs'::regclass;
        IF docs_kind = 'v' THEN
            EXECUTE 'DROP VIEW public.dean_topical_pdf_docs';
        END IF;
    END IF;
    IF to_regclass('public.dean_topical_pdf_docs') IS NULL THEN
        EXECUTE 'CREATE VIEW public.dean_topical_pdf_docs AS SELECT * FROM public.dean_structured_docs';
    END IF;
END $$;

DO $$
DECLARE
    nodes_kind "char";
BEGIN
    IF to_regclass('public.dean_topical_pdf_nodes') IS NOT NULL THEN
        SELECT relkind
        INTO nodes_kind
        FROM pg_class
        WHERE oid = 'public.dean_topical_pdf_nodes'::regclass;
        IF nodes_kind = 'v' THEN
            EXECUTE 'DROP VIEW public.dean_topical_pdf_nodes';
        END IF;
    END IF;
    IF to_regclass('public.dean_topical_pdf_nodes') IS NULL THEN
        EXECUTE 'CREATE VIEW public.dean_topical_pdf_nodes AS SELECT * FROM public.dean_structured_nodes';
    END IF;
END $$;

DO $$
DECLARE
    tables_kind "char";
BEGIN
    IF to_regclass('public.dean_topical_pdf_tables') IS NOT NULL THEN
        SELECT relkind
        INTO tables_kind
        FROM pg_class
        WHERE oid = 'public.dean_topical_pdf_tables'::regclass;
        IF tables_kind = 'v' THEN
            EXECUTE 'DROP VIEW public.dean_topical_pdf_tables';
        END IF;
    END IF;
    IF to_regclass('public.dean_topical_pdf_tables') IS NULL THEN
        EXECUTE 'CREATE VIEW public.dean_topical_pdf_tables AS SELECT * FROM public.dean_structured_tables';
    END IF;
END $$;

COMMIT;
