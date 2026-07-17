"""CLI entry point for chunking."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from src.db.models import TranscriptLesson, SourceFile
from src.db.session import get_sessionmaker
from src.chunking.parent import process_lesson_chunking
from src.db.operations import get_file_tag_id
from src.ingestion.tag_registry import get_tag_spec


def _lesson_ids_for_status(session, status: str, tag: str) -> list[int]:
    tag_spec = get_tag_spec(tag)
    tag_id = get_file_tag_id(session, tag_spec.corpus, tag)
    return (
        session.execute(
            select(TranscriptLesson.id)
            .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
            .where(TranscriptLesson.status == status, SourceFile.file_tag_id == tag_id)
        )
        .scalars()
        .all()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunking CLI")
    subparsers = parser.add_subparsers(dest="command")

    parent_parser = subparsers.add_parser("parent", help="Create parent chunks")
    parent_parser.add_argument("lesson_id", nargs="?", type=int)
    parent_parser.add_argument("--all", action="store_true")
    parent_parser.add_argument("--tag", required=True)

    child_parser = subparsers.add_parser("child", help="Create child chunks")
    child_parser.add_argument("lesson_id", nargs="?", type=int)
    child_parser.add_argument("--all", action="store_true")
    child_parser.add_argument("--tag", required=True)

    args = parser.parse_args()
    if args.command not in {"parent", "child"}:
        raise SystemExit("Use: python -m src.chunking parent|child <lesson_id> | --all")

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        if args.command == "parent":
            if args.all:
                lessons = _lesson_ids_for_status(session, "validated", args.tag)
                results = []
                for lesson_id in lessons:
                    result = process_lesson_chunking(lesson_id, session)
                    results.append({"lesson_id": lesson_id, **result})
                    print(
                        f"Lesson {lesson_id}: {result['status']} - {result['parent_chunks_created']} chunks, avg {int(result['avg_chunk_size']) if result['avg_chunk_size'] else 0} words"
                    )
                print(json.dumps(results, indent=2))
            else:
                if args.lesson_id is None:
                    raise SystemExit("Lesson ID required unless --all is set")
                result = process_lesson_chunking(args.lesson_id, session)
                print(
                    f"Lesson {args.lesson_id}: {result['status']} - {result['parent_chunks_created']} chunks, avg {int(result['avg_chunk_size']) if result['avg_chunk_size'] else 0} words"
                )
                print(json.dumps(result, indent=2))
        else:
            from src.chunking.child import process_lesson_child_chunking

            if args.all:
                lessons = _lesson_ids_for_status(session, "chunked", args.tag)
                results = []
                for lesson_id in lessons:
                    result = process_lesson_child_chunking(lesson_id, session)
                    results.append({"lesson_id": lesson_id, **result})
                    print(
                        f"Lesson {lesson_id}: {result['status']} - Parent: {result['parents']}, Children: {result['children']}"
                    )
                print(json.dumps(results, indent=2))
            else:
                if args.lesson_id is None:
                    raise SystemExit("Lesson ID required unless --all is set")
                result = process_lesson_child_chunking(args.lesson_id, session)
                print(
                    f"Lesson {args.lesson_id}: {result['status']} - Parent: {result['parents']}, Children: {result['children']}"
                )
                print(json.dumps(result, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
