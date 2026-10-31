-- Series/part attribution for lessons without a chapter (e.g. Clough Part 1
-- intro transcripts). Part was previously reachable only through chapters.
ALTER TABLE lessons ADD COLUMN IF NOT EXISTS part_id INTEGER REFERENCES parts(id) ON DELETE SET NULL;

UPDATE lessons l
SET part_id = c.part_id
FROM chapters c
WHERE l.chapter_id = c.id AND l.part_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_lessons_part ON lessons(part_id);
