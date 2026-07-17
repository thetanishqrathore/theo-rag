\echo '=== Course Notes Pilot Verification: File 02 ==='

WITH target AS (
  SELECT sf.id, sf.filename, sf.raw_path, sf.ingestion_status
  FROM source_files sf
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'course_notes'
    AND sf.filename = '1995-BF-CourseNotes-02_Lessons_1-33.pdf'
)
SELECT
  t.id AS source_file_id,
  t.filename,
  t.ingestion_status,
  cnv.id AS volume_id,
  cnv.part_number,
  cnv.part_title,
  cnv.page_count
FROM target t
LEFT JOIN course_note_volumes cnv ON cnv.source_file_id = t.id;

\echo '=== Lesson Cardinality (expect exactly 1 lesson with source_unit_key=root) ==='
WITH target AS (
  SELECT sf.id
  FROM source_files sf
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'course_notes'
    AND sf.filename = '1995-BF-CourseNotes-02_Lessons_1-33.pdf'
),
units AS (
  SELECT l.id, l.lesson_number, l.source_unit_key
  FROM lessons l
  JOIN target t ON t.id = l.source_file_id
)
SELECT
  COUNT(*) AS lesson_count,
  MIN(lesson_number) AS min_lesson_number,
  MAX(lesson_number) AS max_lesson_number,
  MIN(source_unit_key) AS min_unit_key,
  MAX(source_unit_key) AS max_unit_key
FROM units;

WITH target AS (
  SELECT sf.id
  FROM source_files sf
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'course_notes'
    AND sf.filename = '1995-BF-CourseNotes-02_Lessons_1-33.pdf'
)
SELECT l.id, l.lesson_number, l.source_unit_key, l.status, l.canonical_html_path
FROM lessons l
JOIN target t ON t.id = l.source_file_id
ORDER BY l.id;

\echo '=== Course Note Unit Diagnostics (optional, non-authoritative) ==='
WITH target AS (
  SELECT sf.id
  FROM source_files sf
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'course_notes'
    AND sf.filename = '1995-BF-CourseNotes-02_Lessons_1-33.pdf'
)
SELECT
  cnu.lesson_id,
  cnu.logical_lesson_number,
  cnu.unit_title,
  cnu.page_start,
  cnu.page_end,
  cnu.boundary_confidence,
  CASE WHEN cnu.page_start IS NULL OR cnu.page_end IS NULL THEN 'missing'
       WHEN cnu.page_start > cnu.page_end THEN 'invalid'
       ELSE 'ok'
  END AS span_status
FROM course_note_units cnu
JOIN target t ON t.id = cnu.source_file_id
ORDER BY cnu.logical_lesson_number;

\echo '=== Node Coverage Per Lesson (expect node-backed extraction) ==='
WITH target AS (
  SELECT sf.id
  FROM source_files sf
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'course_notes'
    AND sf.filename = '1995-BF-CourseNotes-02_Lessons_1-33.pdf'
)
SELECT
  l.id AS lesson_id,
  l.lesson_number,
  l.source_unit_key,
  COUNT(cnn.id) AS node_count
FROM lessons l
JOIN target t ON t.id = l.source_file_id
LEFT JOIN course_note_nodes cnn ON cnn.lesson_id = l.id
GROUP BY l.id, l.lesson_number, l.source_unit_key
ORDER BY l.lesson_number;

\echo '=== Front Matter Node Coverage ==='
WITH target AS (
  SELECT sf.id
  FROM source_files sf
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'course_notes'
    AND sf.filename = '1995-BF-CourseNotes-02_Lessons_1-33.pdf'
)
SELECT
  l.id AS lesson_id,
  COUNT(*) FILTER (WHERE (cnn.quality_flags->>'is_front_matter') = 'true') AS front_matter_nodes,
  COUNT(cnn.id) AS total_nodes
FROM lessons l
JOIN target t ON t.id = l.source_file_id
LEFT JOIN course_note_nodes cnn ON cnn.lesson_id = l.id
GROUP BY l.id;

\echo '=== Validation + Canonical Coverage ==='
WITH target AS (
  SELECT sf.id
  FROM source_files sf
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'course_notes'
    AND sf.filename = '1995-BF-CourseNotes-02_Lessons_1-33.pdf'
)
SELECT
  COUNT(*) FILTER (WHERE l.status = 'validated') AS validated_lessons,
  COUNT(*) FILTER (WHERE l.canonical_html_path IS NOT NULL) AS canonical_lessons,
  COUNT(*) AS total_lessons
FROM lessons l
JOIN target t ON t.id = l.source_file_id;
