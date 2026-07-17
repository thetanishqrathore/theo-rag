-- Clough transcripts baseline snapshot for metadata/structure/chunk/index coverage.
-- Usage:
--   docker exec -i theology_postgres psql -U raguser -d theology_rag < scripts/sql/transcripts_baseline_snapshot.sql

\echo '=== Transcript Source Files By Format/Ingestion Status ==='
SELECT
  COALESCE(sf.source_format, 'unknown') AS source_format,
  COALESCE(sf.ingestion_status, 'unknown') AS ingestion_status,
  COUNT(*) AS file_count
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'transcripts'
GROUP BY 1, 2
ORDER BY 1, 2;

\echo '=== Transcript Lessons By Status ==='
SELECT
  COALESCE(l.status, 'unknown') AS lesson_status,
  COUNT(*) AS lesson_count
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'transcripts'
GROUP BY 1
ORDER BY 1;

\echo '=== Transcript Validation Failure Classes ==='
SELECT
  COALESCE(reason->>'code', 'none') AS reason_code,
  COUNT(*) AS affected_lessons
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
LEFT JOIN LATERAL jsonb_array_elements(COALESCE(l.validation_report->'reasons', '[]'::jsonb)) reason ON TRUE
WHERE ft.name = 'transcripts'
  AND l.status = 'failed'
GROUP BY 1
ORDER BY 2 DESC, 1;

\echo '=== Transcript Content Layer Counts ==='
SELECT
  (SELECT COUNT(*)
   FROM lessons l
   JOIN source_files sf ON sf.id = l.source_file_id
   JOIN file_tags ft ON ft.id = sf.file_tag_id
   WHERE ft.name = 'transcripts') AS transcript_lessons,
  (SELECT COUNT(*)
   FROM paragraphs p
   JOIN lessons l ON l.id = p.lesson_id
   JOIN source_files sf ON sf.id = l.source_file_id
   JOIN file_tags ft ON ft.id = sf.file_tag_id
   WHERE ft.name = 'transcripts') AS transcript_paragraphs,
  (SELECT COUNT(*)
   FROM parent_chunks pc
   JOIN lessons l ON l.id = pc.lesson_id
   JOIN source_files sf ON sf.id = l.source_file_id
   JOIN file_tags ft ON ft.id = sf.file_tag_id
   WHERE ft.name = 'transcripts') AS transcript_parent_chunks,
  (SELECT COUNT(*)
   FROM child_chunks cc
   JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
   JOIN lessons l ON l.id = pc.lesson_id
   JOIN source_files sf ON sf.id = l.source_file_id
   JOIN file_tags ft ON ft.id = sf.file_tag_id
   WHERE ft.name = 'transcripts') AS transcript_child_chunks;

\echo '=== Transcript Canonical + Chunk + Embedding Coverage ==='
SELECT
  COUNT(*) AS total_lessons,
  COUNT(*) FILTER (WHERE l.canonical_html_path IS NOT NULL) AS canonical_lessons,
  COUNT(DISTINCT pc.lesson_id) AS lessons_with_parent_chunks,
  COUNT(DISTINCT l2.id) FILTER (WHERE cc.embedding IS NOT NULL) AS lessons_with_embedded_children
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
LEFT JOIN parent_chunks pc ON pc.lesson_id = l.id
LEFT JOIN child_chunks cc ON cc.parent_chunk_id = pc.id
LEFT JOIN lessons l2 ON l2.id = pc.lesson_id
WHERE ft.name = 'transcripts';

\echo '=== Transcript Metadata Completeness (Lesson/Part/Chapter) ==='
SELECT
  COUNT(*) AS total_lessons,
  COUNT(*) FILTER (WHERE l.date IS NOT NULL) AS with_date,
  COUNT(*) FILTER (WHERE NULLIF(TRIM(l.speaker), '') IS NOT NULL) AS with_speaker,
  COUNT(*) FILTER (WHERE tc.id IS NOT NULL) AS with_chapter_row,
  COUNT(*) FILTER (WHERE tc.chapter_number IS NOT NULL AND tc.chapter_number > 0) AS with_nonzero_chapter,
  COUNT(*) FILTER (WHERE tp.id IS NOT NULL) AS with_part_row,
  COUNT(*) FILTER (WHERE ts.id IS NOT NULL) AS with_series_row
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
LEFT JOIN chapters tc ON tc.id = l.chapter_id
LEFT JOIN parts tp ON tp.id = tc.part_id
LEFT JOIN series ts ON ts.id = tp.series_id
WHERE ft.name = 'transcripts';

\echo '=== Transcript Parent Metadata Coverage ==='
SELECT
  COUNT(*) AS total_parent_chunks,
  COUNT(*) FILTER (WHERE metadata_json ? 'source_tag') AS with_source_tag,
  COUNT(*) FILTER (WHERE metadata_json ? 'series_title') AS with_series_title,
  COUNT(*) FILTER (WHERE metadata_json ? 'part_number') AS with_part_number,
  COUNT(*) FILTER (WHERE metadata_json ? 'chapter_number') AS with_chapter_number,
  COUNT(*) FILTER (WHERE metadata_json ? 'section_path') AS with_section_path,
  COUNT(*) FILTER (WHERE metadata_json ? 'section_title') AS with_section_title
FROM parent_chunks pc
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'transcripts';

\echo '=== Transcript Child Metadata Coverage ==='
SELECT
  COUNT(*) AS total_child_chunks,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'source_tag') AS with_source_tag,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'parent_section') AS with_parent_section,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'section_path') AS with_section_path,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'chapter_number') AS with_chapter_number,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'chapter_title') AS with_chapter_title
FROM child_chunks cc
JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'transcripts';
