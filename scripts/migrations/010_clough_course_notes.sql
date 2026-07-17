-- Clough course notes v2:
-- 1) Multi-unit lessons per source file via lessons.source_unit_key
-- 2) Course-note volume/unit/node/table structure tables

BEGIN;

ALTER TABLE public.lessons
ADD COLUMN IF NOT EXISTS source_unit_key TEXT;

ALTER TABLE public.lessons
ALTER COLUMN source_unit_key SET DEFAULT 'root';

UPDATE public.lessons
SET source_unit_key = 'root'
WHERE source_unit_key IS NULL;

ALTER TABLE public.lessons
ALTER COLUMN source_unit_key SET NOT NULL;

ALTER TABLE public.lessons
DROP CONSTRAINT IF EXISTS lessons_source_file_id_key;

ALTER TABLE public.lessons
DROP CONSTRAINT IF EXISTS transcript_lessons_source_file_id_key;

-- Legacy unique indexes may still exist after table renames in older environments.
DROP INDEX IF EXISTS public.lessons_source_file_id_key;
DROP INDEX IF EXISTS public.transcript_lessons_source_file_id_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'lessons_source_file_id_source_unit_key_key'
    ) THEN
        ALTER TABLE public.lessons
        ADD CONSTRAINT lessons_source_file_id_source_unit_key_key
            UNIQUE (source_file_id, source_unit_key);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.course_note_volumes (
    id SERIAL PRIMARY KEY,
    source_file_id UUID NOT NULL REFERENCES public.source_files(id) ON DELETE CASCADE UNIQUE,
    series_id INTEGER NOT NULL REFERENCES public.series(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES public.parts(id) ON DELETE CASCADE,
    part_number INTEGER NOT NULL,
    part_title TEXT NOT NULL,
    volume_code VARCHAR(64) NOT NULL,
    page_count INTEGER NOT NULL,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.course_note_units (
    id SERIAL PRIMARY KEY,
    source_file_id UUID NOT NULL REFERENCES public.source_files(id) ON DELETE CASCADE,
    lesson_id INTEGER NOT NULL REFERENCES public.lessons(id) ON DELETE CASCADE,
    logical_lesson_number INTEGER NOT NULL,
    unit_title TEXT,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    boundary_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_file_id, logical_lesson_number)
);

CREATE TABLE IF NOT EXISTS public.course_note_nodes (
    id BIGSERIAL PRIMARY KEY,
    lesson_id INTEGER NOT NULL REFERENCES public.lessons(id) ON DELETE CASCADE,
    parent_node_id BIGINT REFERENCES public.course_note_nodes(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS public.course_note_tables (
    id BIGSERIAL PRIMARY KEY,
    node_id BIGINT NOT NULL REFERENCES public.course_note_nodes(id) ON DELETE CASCADE,
    table_json JSONB NOT NULL,
    table_html TEXT
);

CREATE INDEX IF NOT EXISTS idx_course_note_units_source
ON public.course_note_units(source_file_id, logical_lesson_number);

CREATE INDEX IF NOT EXISTS idx_course_note_units_lesson
ON public.course_note_units(lesson_id);

CREATE INDEX IF NOT EXISTS idx_course_note_nodes_lesson_order
ON public.course_note_nodes(lesson_id, order_index);

CREATE INDEX IF NOT EXISTS idx_course_note_nodes_lesson_path
ON public.course_note_nodes(lesson_id, node_path);

CREATE INDEX IF NOT EXISTS idx_course_note_nodes_parent
ON public.course_note_nodes(parent_node_id);

COMMIT;
