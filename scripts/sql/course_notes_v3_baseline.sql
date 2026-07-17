\echo '=== Course Notes V3 Baseline: Source Files ==='
SELECT
  sf.id AS source_file_id,
  sf.filename,
  sf.ingestion_status,
  sf.source_format,
  sf.source_kind
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'course_notes'
ORDER BY sf.filename;

\echo '=== Course Notes V3 Baseline: Lesson Cardinality Per Source ==='
SELECT
  sf.filename,
  COUNT(l.id) AS lesson_count,
  COUNT(*) FILTER (WHERE l.source_unit_key <> 'root') AS non_root_units
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
LEFT JOIN lessons l ON l.source_file_id = sf.id
WHERE ft.name = 'course_notes'
GROUP BY sf.filename
ORDER BY sf.filename;

\echo '=== Course Notes V3 Baseline: Split-Shape Detection (should be 0 rows in V3) ==='
SELECT
  sf.filename,
  l.source_unit_key,
  l.lesson_number,
  l.status
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN lessons l ON l.source_file_id = sf.id
WHERE ft.name = 'course_notes'
  AND (l.source_unit_key <> 'root'
       OR (SELECT COUNT(*) FROM lessons l2 WHERE l2.source_file_id = sf.id) > 1)
ORDER BY sf.filename, l.lesson_number;

\echo '=== Course Notes V3 Baseline: Structured Coverage ==='
SELECT
  sf.filename,
  l.id AS lesson_id,
  l.status,
  COUNT(cnn.id) AS node_count,
  COUNT(*) FILTER (WHERE cnn.quality_flags->>'is_front_matter' = 'true') AS front_matter_nodes
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN lessons l ON l.source_file_id = sf.id
LEFT JOIN course_note_nodes cnn ON cnn.lesson_id = l.id
WHERE ft.name = 'course_notes'
GROUP BY sf.filename, l.id, l.status
ORDER BY sf.filename, l.id;

\echo '=== Course Notes V3 Baseline: Content/Chunk/Embedding Counts ==='
SELECT
  COUNT(DISTINCT l.id) AS lessons,
  COUNT(p.id) AS paragraphs,
  COUNT(pc.id) AS parent_chunks,
  COUNT(cc.id) AS child_chunks,
  COUNT(*) FILTER (WHERE cc.embedding IS NOT NULL) AS embedded_child_chunks
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
LEFT JOIN lessons l ON l.source_file_id = sf.id
LEFT JOIN paragraphs p ON p.lesson_id = l.id
LEFT JOIN parent_chunks pc ON pc.lesson_id = l.id
LEFT JOIN child_chunks cc ON cc.parent_chunk_id = pc.id
WHERE ft.name = 'course_notes';
