\echo '=== Course Notes V3 Reset (tag scoped) ==='
BEGIN;

CREATE TEMP TABLE _cn_source_files AS
SELECT sf.id
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'course_notes';

CREATE TEMP TABLE _cn_lessons AS
SELECT l.id
FROM lessons l
JOIN _cn_source_files s ON s.id = l.source_file_id;

CREATE TEMP TABLE _cn_parent_chunks AS
SELECT pc.id
FROM parent_chunks pc
JOIN _cn_lessons l ON l.id = pc.lesson_id;

CREATE TEMP TABLE _cn_child_chunks AS
SELECT cc.id
FROM child_chunks cc
JOIN _cn_parent_chunks pc ON pc.id = cc.parent_chunk_id;

DELETE FROM child_chunk_audit
WHERE child_chunk_id IN (SELECT id FROM _cn_child_chunks);

DELETE FROM child_chunks
WHERE id IN (SELECT id FROM _cn_child_chunks);

DELETE FROM parent_chunks
WHERE id IN (SELECT id FROM _cn_parent_chunks);

DELETE FROM paragraphs
WHERE lesson_id IN (SELECT id FROM _cn_lessons);

DELETE FROM course_note_tables
WHERE node_id IN (
  SELECT id FROM course_note_nodes WHERE lesson_id IN (SELECT id FROM _cn_lessons)
);

DELETE FROM course_note_nodes
WHERE lesson_id IN (SELECT id FROM _cn_lessons);

DELETE FROM course_note_units
WHERE source_file_id IN (SELECT id FROM _cn_source_files);

DELETE FROM course_note_volumes
WHERE source_file_id IN (SELECT id FROM _cn_source_files);

DELETE FROM lessons
WHERE id IN (SELECT id FROM _cn_lessons);

DELETE FROM source_files
WHERE id IN (SELECT id FROM _cn_source_files);

COMMIT;

\echo '=== Course Notes V3 Reset Complete ==='
