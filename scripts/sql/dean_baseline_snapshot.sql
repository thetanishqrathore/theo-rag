-- Dean baseline snapshot for safe reprocessing and parity checks.
-- Usage:
--   docker exec -it theology_postgres psql -U raguser -d theology_rag -f scripts/sql/dean_baseline_snapshot.sql

\echo '=== Dean Source Files By Tag/Format/Ingestion Status ==='
SELECT
  ft.name AS source_tag,
  COALESCE(sf.source_format, 'unknown') AS source_format,
  COALESCE(sf.ingestion_status, 'unknown') AS ingestion_status,
  COUNT(*) AS file_count
FROM source_files sf
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean'
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

\echo '=== Dean Lessons By Tag/Status ==='
SELECT
  ft.name AS source_tag,
  l.status,
  COUNT(*) AS lesson_count
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
WHERE c.name = 'dean'
GROUP BY 1, 2
ORDER BY 1, 2;

\echo '=== Dean Lessons Validation Failure Classes ==='
SELECT
  COALESCE(reason->>'code', 'none') AS reason_code,
  COUNT(*) AS affected_lessons
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
LEFT JOIN LATERAL jsonb_array_elements(COALESCE(l.validation_report->'reasons', '[]'::jsonb)) reason ON TRUE
WHERE c.name = 'dean'
  AND l.status = 'failed'
GROUP BY 1
ORDER BY 2 DESC, 1;

\echo '=== Dean Content Layer Counts ==='
SELECT
  (SELECT COUNT(*) FROM lessons l
     JOIN source_files sf ON sf.id = l.source_file_id
     JOIN file_tags ft ON ft.id = sf.file_tag_id
     JOIN corpus c ON c.id = ft.corpus_id
    WHERE c.name = 'dean') AS dean_lessons,
  (SELECT COUNT(*) FROM paragraphs p
     JOIN lessons l ON l.id = p.lesson_id
     JOIN source_files sf ON sf.id = l.source_file_id
     JOIN file_tags ft ON ft.id = sf.file_tag_id
     JOIN corpus c ON c.id = ft.corpus_id
    WHERE c.name = 'dean') AS dean_paragraphs,
  (SELECT COUNT(*) FROM parent_chunks pc
     JOIN lessons l ON l.id = pc.lesson_id
     JOIN source_files sf ON sf.id = l.source_file_id
     JOIN file_tags ft ON ft.id = sf.file_tag_id
     JOIN corpus c ON c.id = ft.corpus_id
    WHERE c.name = 'dean') AS dean_parent_chunks,
  (SELECT COUNT(*) FROM child_chunks cc
     JOIN corpus c ON c.id = cc.corpus_id
    WHERE c.name = 'dean') AS dean_child_chunks;

\echo '=== Dean Embedded Child Chunks (DB) ==='
SELECT COUNT(*) AS dean_embedded_child_chunks
FROM child_chunks cc
JOIN corpus c ON c.id = cc.corpus_id
WHERE c.name = 'dean'
  AND cc.embedding IS NOT NULL;

\echo '=== Dean Structured Node Coverage ==='
SELECT
  ft.name AS source_tag,
  COUNT(DISTINCT l.id) AS lessons,
  COUNT(DISTINCT dsd.lesson_id) AS structured_docs,
  COUNT(dsn.id) AS structured_nodes
FROM lessons l
JOIN source_files sf ON sf.id = l.source_file_id
JOIN file_tags ft ON ft.id = sf.file_tag_id
JOIN corpus c ON c.id = ft.corpus_id
LEFT JOIN dean_structured_docs dsd ON dsd.lesson_id = l.id
LEFT JOIN dean_structured_nodes dsn ON dsn.lesson_id = l.id
WHERE c.name = 'dean'
GROUP BY 1
ORDER BY 1;
