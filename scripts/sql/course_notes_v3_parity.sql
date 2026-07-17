\echo '=== Course Notes V3 Parity (DB-side) ==='
SELECT
  COUNT(*) AS db_embedded_child_chunks
FROM child_chunks cc
JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'course_notes'
  AND cc.embedding IS NOT NULL;

\echo '=== Course Notes V3 Chunk Metadata Coverage ==='
SELECT
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'node_path') AS with_node_path,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'page_start') AS with_page_start,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'page_end') AS with_page_end,
  COUNT(*) FILTER (WHERE cc.metadata_json ? 'is_front_matter') AS with_front_matter_flag,
  COUNT(*) AS total_course_note_child_chunks
FROM child_chunks cc
JOIN parent_chunks pc ON pc.id = cc.parent_chunk_id
JOIN lessons l ON l.id = pc.lesson_id
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
WHERE ft.name = 'course_notes';
