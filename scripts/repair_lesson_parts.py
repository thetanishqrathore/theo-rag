"""Restore series/part attribution for transcript lessons that have no chapter.

Lessons whose header names a section instead of "Chapter N:" (Part 1 intro
transcripts, "Conclusion", "Appendix: ...", "Part 6 Introduction") were ingested
with chapter_id NULL, and series/part were only reachable through a chapter.
Migration 012 added lessons.part_id; new ingests set it. This one-off repair
fills part_id for existing lessons from each source's own header, then
refreshes derived metadata in place, without changing any chunk ID:

  * parent_chunks.metadata_json  (series_title, part_number, part_title)
  * Qdrant point payload         (part_number)
  * canonical HTML               (regenerated; text unchanged)

Idempotent; --dry-run reports what would change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdrant_client.models import FieldCondition, Filter, MatchValue  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.chunking.parent import _resolve_source_metadata  # noqa: E402
from src.db.models import FileTag, SourceFile, TranscriptLesson, TranscriptParentChunk  # noqa: E402
from src.db.operations import get_or_create_part, get_or_create_series  # noqa: E402
from src.db.session import get_sessionmaker  # noqa: E402
from src.ingestion.extract_transcript_doc_metadata import extract_metadata_from_transcript_doc  # noqa: E402
from src.processing.canonical import generate_canonical_html  # noqa: E402
from src.utils.qdrant_client import get_qdrant_client  # noqa: E402

PART_KEYS = ("series_title", "part_number", "part_title")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session = get_sessionmaker()()
    qdrant = get_qdrant_client(timeout=60.0)
    rows = session.execute(
        select(TranscriptLesson, SourceFile.raw_path, SourceFile.filename)
        .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
        .join(FileTag, SourceFile.file_tag_id == FileTag.id)
        .where(FileTag.name == "transcripts", TranscriptLesson.chapter_id.is_(None))
        .order_by(SourceFile.filename)
    ).all()
    repaired = 0
    for lesson, raw_path, filename in rows:
        if lesson.part_id is None:
            metadata = extract_metadata_from_transcript_doc(Path(raw_path))
            series_id = get_or_create_series(session, "clough", metadata["series_name"])
            part_id = get_or_create_part(session, series_id, metadata["part_number"], metadata["part_title"])
            print(f"{filename}: part {metadata['part_number']} ({metadata['part_title']})")
            if args.dry_run:
                session.rollback()
                continue
            lesson.part_id = part_id
            session.flush()

        source_meta = {k: v for k, v in _resolve_source_metadata(lesson, session).items() if k in PART_KEYS}
        parents = session.execute(
            select(TranscriptParentChunk).where(TranscriptParentChunk.lesson_id == lesson.id)
        ).scalars().all()
        changed = 0
        for parent in parents:
            merged = {**(parent.metadata_json or {}), **source_meta}
            if merged != parent.metadata_json:
                parent.metadata_json = merged
                changed += 1
        session.commit()
        if source_meta.get("part_number") is not None:
            qdrant.set_payload(
                collection_name="clough_chunks",
                payload={"part_number": source_meta["part_number"]},
                points=Filter(must=[FieldCondition(key="lesson_id", match=MatchValue(value=lesson.id))]),
            )
        if lesson.canonical_html_path:
            generate_canonical_html(lesson.id, session, require_validated=False)
        repaired += 1 if changed else 0
        print(f"  lesson {lesson.id}: {changed}/{len(parents)} parent chunks updated, part {source_meta.get('part_number')}")
    print(f"{len(rows)} chapterless transcript lesson(s); {repaired} updated")
    session.close()


if __name__ == "__main__":
    main()
