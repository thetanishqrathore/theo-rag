-- Transcript quality audit focusing on metadata, hierarchy depth, and citation quality.
-- Usage:
--   docker exec -i theology_postgres psql -U raguser -d theology_rag < scripts/sql/transcripts_quality_audit.sql

\echo '=== Transcript Metadata Gaps (Top 50) ==='
SELECT
  l.id AS lesson_id,
  sf.filename,
  l.lesson_number,
  l.title,
  l.date,
  l.speaker,
  tc.chapter_number,
  tc.title AS chapter_title,
  tp.part_number,
  tp.title AS part_title,
  ts.title AS series_title
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
LEFT JOIN chapters tc ON tc.id = l.chapter_id
LEFT JOIN parts tp ON tp.id = tc.part_id
LEFT JOIN series ts ON ts.id = tp.series_id
WHERE ft.name = 'transcripts'
  AND (
    l.date IS NULL
    OR NULLIF(TRIM(l.title), '') IS NULL
  )
ORDER BY l.id
LIMIT 50;

\echo '=== Transcript Paragraph Hierarchy Depth Distribution ==='
WITH transcript_paragraphs AS (
  SELECT
    p.lesson_id,
    p.hierarchy_path,
    COALESCE(array_length(string_to_array(NULLIF(p.hierarchy_path, ''), '.'), 1), 1) AS depth
  FROM paragraphs p
  JOIN lessons l ON l.id = p.lesson_id
  JOIN source_files sf ON sf.id = l.source_file_id
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'transcripts'
)
SELECT
  depth,
  COUNT(*) AS paragraph_count,
  COUNT(DISTINCT lesson_id) AS lesson_count
FROM transcript_paragraphs
GROUP BY depth
ORDER BY depth;

\echo '=== Transcript Lessons With Flat Hierarchy (Only Depth 1) ==='
WITH lesson_depth AS (
  SELECT
    p.lesson_id,
    MAX(COALESCE(array_length(string_to_array(NULLIF(p.hierarchy_path, ''), '.'), 1), 1)) AS max_depth,
    COUNT(*) AS para_count
  FROM paragraphs p
  JOIN lessons l ON l.id = p.lesson_id
  JOIN source_files sf ON sf.id = l.source_file_id
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'transcripts'
  GROUP BY p.lesson_id
)
SELECT
  ld.lesson_id,
  sf.filename,
  ld.para_count,
  ld.max_depth
FROM lesson_depth ld
JOIN lessons l ON l.id = ld.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'transcripts'
  AND ld.max_depth = 1
ORDER BY ld.para_count DESC, ld.lesson_id
LIMIT 50;

\echo '=== Transcript Section Count Distribution (Top-Level Paths) ==='
WITH lesson_sections AS (
  SELECT
    p.lesson_id,
    COUNT(DISTINCT COALESCE(NULLIF(split_part(p.hierarchy_path, '.', 1), ''), '1')) AS section_count
  FROM paragraphs p
  JOIN lessons l ON l.id = p.lesson_id
  JOIN source_files sf ON sf.id = l.source_file_id
  JOIN file_tags ft ON ft.id = sf.file_tag_id
  WHERE ft.name = 'transcripts'
  GROUP BY p.lesson_id
)
SELECT
  section_count,
  COUNT(*) AS lesson_count
FROM lesson_sections
GROUP BY section_count
ORDER BY section_count;

\echo '=== Transcript Parent Citation Quality ==='
SELECT
  COUNT(*) AS total_parent_chunks,
  COUNT(*) FILTER (WHERE citation_ref ILIKE 'Lesson %Para%') AS legacy_citation_shape,
  COUNT(*) FILTER (
    WHERE citation_ref ILIKE 'Part %· Chapter %· Lesson %· Sec %· Para %'
       OR citation_ref ILIKE 'Part %· Lesson %· Sec %· Para %'
  ) AS enriched_citation_shape
FROM parent_chunks pc
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'transcripts';

\echo '=== Transcript Validation Reason Breakdown ==='
SELECT
  COALESCE(reason->>'code', 'none') AS reason_code,
  COUNT(*) AS affected_lessons
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
LEFT JOIN LATERAL jsonb_array_elements(COALESCE(l.validation_report->'reasons', '[]'::jsonb)) reason ON TRUE
WHERE ft.name = 'transcripts'
GROUP BY 1
ORDER BY 2 DESC, 1;
