-- Add explicit Dean topical format tags for isolated ingestion flows.
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
