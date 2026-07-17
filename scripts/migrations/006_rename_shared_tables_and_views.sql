-- Rename shared transcript_* tables to neutral names and add corpus-scoped views.
-- Safe to run once on an existing DB that still has transcript_* physical tables.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.transcript_child_chunk_audit') IS NOT NULL
       AND to_regclass('public.child_chunk_audit') IS NULL THEN
        EXECUTE 'ALTER TABLE public.transcript_child_chunk_audit RENAME TO child_chunk_audit';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.transcript_child_chunks') IS NOT NULL
       AND to_regclass('public.child_chunks') IS NULL THEN
        EXECUTE 'ALTER TABLE public.transcript_child_chunks RENAME TO child_chunks';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.transcript_parent_chunks') IS NOT NULL
       AND to_regclass('public.parent_chunks') IS NULL THEN
        EXECUTE 'ALTER TABLE public.transcript_parent_chunks RENAME TO parent_chunks';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.transcript_paragraphs') IS NOT NULL
       AND to_regclass('public.paragraphs') IS NULL THEN
        EXECUTE 'ALTER TABLE public.transcript_paragraphs RENAME TO paragraphs';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.transcript_lessons') IS NOT NULL
       AND to_regclass('public.lessons') IS NULL THEN
        EXECUTE 'ALTER TABLE public.transcript_lessons RENAME TO lessons';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.transcript_chapters') IS NOT NULL
       AND to_regclass('public.chapters') IS NULL THEN
        EXECUTE 'ALTER TABLE public.transcript_chapters RENAME TO chapters';
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.transcript_parts') IS NOT NULL
       AND to_regclass('public.parts') IS NULL THEN
        EXECUTE 'ALTER TABLE public.transcript_parts RENAME TO parts';
    END IF;
END $$;

ALTER INDEX IF EXISTS idx_transcript_lessons_dean_lesson RENAME TO idx_lessons_dean_lesson;

CREATE OR REPLACE VIEW transcript_parts AS SELECT * FROM parts;
CREATE OR REPLACE VIEW transcript_chapters AS SELECT * FROM chapters;
CREATE OR REPLACE VIEW transcript_lessons AS SELECT * FROM lessons;
CREATE OR REPLACE VIEW transcript_paragraphs AS SELECT * FROM paragraphs;
CREATE OR REPLACE VIEW transcript_parent_chunks AS SELECT * FROM parent_chunks;
CREATE OR REPLACE VIEW transcript_child_chunks AS SELECT * FROM child_chunks;
CREATE OR REPLACE VIEW transcript_child_chunk_audit AS SELECT * FROM child_chunk_audit;

CREATE OR REPLACE VIEW dean_lessons_view AS
SELECT l.*
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean';

CREATE OR REPLACE VIEW clough_lessons_view AS
SELECT l.*
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'clough';

CREATE OR REPLACE VIEW dean_parent_chunks_view AS
SELECT pc.*
FROM parent_chunks pc
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean';

CREATE OR REPLACE VIEW clough_parent_chunks_view AS
SELECT pc.*
FROM parent_chunks pc
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'clough';

CREATE OR REPLACE VIEW dean_child_chunks_view AS
SELECT cc.*
FROM child_chunks cc
JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean';

CREATE OR REPLACE VIEW clough_child_chunks_view AS
SELECT cc.*
FROM child_chunks cc
JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'clough';

COMMIT;
