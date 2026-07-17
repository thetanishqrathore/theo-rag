-- Phase 1 integrity and performance hardening.
-- Safe, idempotent migration for both current and legacy table names.

BEGIN;

DO $$
DECLARE
    parent_tbl text := COALESCE(
        CASE WHEN to_regclass('public.parent_chunks') IS NOT NULL THEN 'parent_chunks' END,
        CASE WHEN to_regclass('public.transcript_parent_chunks') IS NOT NULL THEN 'transcript_parent_chunks' END
    );
    child_tbl text := COALESCE(
        CASE WHEN to_regclass('public.child_chunks') IS NOT NULL THEN 'child_chunks' END,
        CASE WHEN to_regclass('public.transcript_child_chunks') IS NOT NULL THEN 'transcript_child_chunks' END
    );
BEGIN
    IF parent_tbl IS NOT NULL THEN
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_parent_chunks_lesson ON public.%I(lesson_id)', parent_tbl);
    END IF;

    IF child_tbl IS NOT NULL THEN
        EXECUTE format('CREATE INDEX IF NOT EXISTS idx_child_chunks_parent ON public.%I(parent_chunk_id)', child_tbl);
    END IF;
END $$;

DO $$
DECLARE
    has_dupes boolean := FALSE;
BEGIN
    IF to_regclass('public.file_tags') IS NULL THEN
        RETURN;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM (
            SELECT corpus_id, name, COUNT(*) AS c
            FROM public.file_tags
            GROUP BY corpus_id, name
            HAVING COUNT(*) > 1
        ) d
    )
    INTO has_dupes;

    IF has_dupes THEN
        RAISE NOTICE 'Skipping unique index file_tags(corpus_id,name): duplicate rows found.';
    ELSE
        EXECUTE 'CREATE UNIQUE INDEX IF NOT EXISTS idx_file_tags_corpus_name_uq ON public.file_tags(corpus_id, name)';
    END IF;
END $$;

DO $$
DECLARE
    dean_nodes_tbl text := COALESCE(
        CASE WHEN to_regclass('public.dean_structured_nodes') IS NOT NULL THEN 'dean_structured_nodes' END,
        CASE WHEN to_regclass('public.dean_topical_pdf_nodes') IS NOT NULL THEN 'dean_topical_pdf_nodes' END
    );
    has_order_dupes boolean := FALSE;
BEGIN
    IF dean_nodes_tbl IS NULL THEN
        RETURN;
    END IF;

    EXECUTE format(
        'SELECT EXISTS (
            SELECT 1
            FROM (
                SELECT lesson_id, order_index, COUNT(*) AS c
                FROM public.%I
                GROUP BY lesson_id, order_index
                HAVING COUNT(*) > 1
            ) d
        )',
        dean_nodes_tbl
    )
    INTO has_order_dupes;

    IF has_order_dupes THEN
        RAISE NOTICE 'Skipping unique index on %.(lesson_id,order_index): duplicate rows found.', dean_nodes_tbl;
    ELSE
        EXECUTE format(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_dean_nodes_lesson_order_uq ON public.%I(lesson_id, order_index)',
            dean_nodes_tbl
        );
    END IF;
END $$;

DO $$
DECLARE
    has_order_dupes boolean := FALSE;
BEGIN
    IF to_regclass('public.course_note_nodes') IS NULL THEN
        RETURN;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM (
            SELECT lesson_id, order_index, COUNT(*) AS c
            FROM public.course_note_nodes
            GROUP BY lesson_id, order_index
            HAVING COUNT(*) > 1
        ) d
    )
    INTO has_order_dupes;

    IF has_order_dupes THEN
        RAISE NOTICE 'Skipping unique index on course_note_nodes(lesson_id,order_index): duplicate rows found.';
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS idx_course_note_nodes_lesson_order_uq
            ON public.course_note_nodes(lesson_id, order_index);
    END IF;
END $$;

-- node_path is intentionally section-oriented in current extraction logic and can repeat
-- across sibling nodes (e.g., multiple paragraphs under one section).
-- Keep path searchable via non-unique indexes from earlier migrations; do not enforce uniqueness.
DROP INDEX IF EXISTS public.idx_dean_nodes_lesson_path_uq;
DROP INDEX IF EXISTS public.idx_course_note_nodes_lesson_path_uq;

DO $$
DECLARE
    rec record;
BEGIN
    IF to_regclass('public.dean_structured_nodes') IS NOT NULL THEN
        FOR rec IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.dean_structured_nodes'::regclass
              AND contype = 'u'
              AND pg_get_constraintdef(oid) ILIKE '%(lesson_id, node_path)%'
        LOOP
            EXECUTE format('ALTER TABLE public.dean_structured_nodes DROP CONSTRAINT IF EXISTS %I', rec.conname);
        END LOOP;
    END IF;

    IF to_regclass('public.course_note_nodes') IS NOT NULL THEN
        FOR rec IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.course_note_nodes'::regclass
              AND contype = 'u'
              AND pg_get_constraintdef(oid) ILIKE '%(lesson_id, node_path)%'
        LOOP
            EXECUTE format('ALTER TABLE public.course_note_nodes DROP CONSTRAINT IF EXISTS %I', rec.conname);
        END LOOP;
    END IF;
END $$;

DO $$
DECLARE
    lessons_tbl text := COALESCE(
        CASE WHEN to_regclass('public.lessons') IS NOT NULL THEN 'lessons' END,
        CASE WHEN to_regclass('public.transcript_lessons') IS NOT NULL THEN 'transcript_lessons' END
    );
    has_invalid_status boolean := FALSE;
BEGIN
    IF lessons_tbl IS NULL THEN
        RETURN;
    END IF;

    EXECUTE format(
        $sql$
        SELECT EXISTS (
            SELECT 1
            FROM public.%I
            WHERE status IS NULL
               OR status NOT IN (
                    'pending',
                    'extracted',
                    'structured',
                    'validated',
                    'chunked',
                    'child_chunked',
                    'embedded',
                    'failed'
               )
        )
        $sql$,
        lessons_tbl
    )
    INTO has_invalid_status;

    IF has_invalid_status THEN
        RAISE NOTICE 'Skipping lessons status check constraint: invalid status values present.';
    ELSIF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'lessons_status_allowed_ck'
    ) THEN
        EXECUTE format(
            $sql$
            ALTER TABLE public.%I
            ADD CONSTRAINT lessons_status_allowed_ck
            CHECK (
                status IN (
                    'pending',
                    'extracted',
                    'structured',
                    'validated',
                    'chunked',
                    'child_chunked',
                    'embedded',
                    'failed'
                )
            )
            $sql$,
            lessons_tbl
        );
    END IF;
END $$;

COMMIT;
