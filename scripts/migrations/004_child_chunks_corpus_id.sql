-- Add corpus_id to child chunks for faster BM25 filtering.
-- Supports both old transcript_* names and new neutral table names.

DO $$
DECLARE
    child_tbl text := COALESCE(
        CASE WHEN to_regclass('public.child_chunks') IS NOT NULL THEN 'child_chunks' END,
        CASE WHEN to_regclass('public.transcript_child_chunks') IS NOT NULL THEN 'transcript_child_chunks' END
    );
    parent_tbl text := COALESCE(
        CASE WHEN to_regclass('public.parent_chunks') IS NOT NULL THEN 'parent_chunks' END,
        CASE WHEN to_regclass('public.transcript_parent_chunks') IS NOT NULL THEN 'transcript_parent_chunks' END
    );
    lesson_tbl text := COALESCE(
        CASE WHEN to_regclass('public.lessons') IS NOT NULL THEN 'lessons' END,
        CASE WHEN to_regclass('public.transcript_lessons') IS NOT NULL THEN 'transcript_lessons' END
    );
BEGIN
    IF child_tbl IS NULL OR parent_tbl IS NULL OR lesson_tbl IS NULL THEN
        RAISE EXCEPTION 'Unable to resolve shared table names for migration 004';
    END IF;

    EXECUTE format(
        'ALTER TABLE %I ADD COLUMN IF NOT EXISTS corpus_id INTEGER REFERENCES corpus(id) ON DELETE SET NULL',
        child_tbl
    );

    EXECUTE format('CREATE INDEX IF NOT EXISTS idx_child_chunks_corpus ON %I(corpus_id)', child_tbl);

    EXECUTE format(
        $sql$
        UPDATE %I cc
        SET corpus_id = c.id
        FROM %I pc
        JOIN %I l ON pc.lesson_id = l.id
        JOIN source_files sf ON l.source_file_id = sf.id
        JOIN file_tags ft ON sf.file_tag_id = ft.id
        JOIN corpus c ON ft.corpus_id = c.id
        WHERE cc.parent_chunk_id = pc.id
          AND (cc.corpus_id IS NULL OR cc.corpus_id <> c.id)
        $sql$,
        child_tbl, parent_tbl, lesson_tbl
    );
END $$;
