-- Dean metadata hierarchy and source-file enrichment.
-- Supports both old transcript_lessons and new lessons table names.

ALTER TABLE source_files
ADD COLUMN IF NOT EXISTS source_format VARCHAR(20),
ADD COLUMN IF NOT EXISTS source_kind VARCHAR(50),
ADD COLUMN IF NOT EXISTS ingest_exclude_reason TEXT;

CREATE TABLE IF NOT EXISTS dean_collections (
    id SERIAL PRIMARY KEY,
    corpus_id INTEGER NOT NULL REFERENCES corpus(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,
    source_root TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(corpus_id, name)
);

CREATE TABLE IF NOT EXISTS dean_books (
    id SERIAL PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES dean_collections(id) ON DELETE CASCADE,
    book_slug VARCHAR(100) NOT NULL,
    display_name TEXT NOT NULL,
    parser_profile VARCHAR(100) NOT NULL DEFAULT 'default',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(collection_id, book_slug)
);

CREATE TABLE IF NOT EXISTS dean_lessons (
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

DO $$
DECLARE
    lessons_tbl text := COALESCE(
        CASE WHEN to_regclass('public.lessons') IS NOT NULL THEN 'lessons' END,
        CASE WHEN to_regclass('public.transcript_lessons') IS NOT NULL THEN 'transcript_lessons' END
    );
BEGIN
    IF lessons_tbl IS NULL THEN
        RAISE EXCEPTION 'Unable to resolve lessons table for migration 005';
    END IF;

    EXECUTE format(
        'ALTER TABLE %I ADD COLUMN IF NOT EXISTS dean_lesson_id INTEGER REFERENCES dean_lessons(id) ON DELETE SET NULL',
        lessons_tbl
    );

    EXECUTE format(
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_dean_lesson ON %I(dean_lesson_id) WHERE dean_lesson_id IS NOT NULL',
        lessons_tbl
    );
END $$;

CREATE INDEX IF NOT EXISTS idx_dean_collections_corpus ON dean_collections(corpus_id);
CREATE INDEX IF NOT EXISTS idx_dean_books_collection ON dean_books(collection_id);
CREATE INDEX IF NOT EXISTS idx_dean_lessons_book ON dean_lessons(book_id);

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
