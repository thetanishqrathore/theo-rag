"""CLI entry point for processing modules."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from src.db.models import TranscriptLesson, SourceFile
from src.db.session import get_sessionmaker
from src.processing.extract import process_lesson_extraction
from src.processing.extract_dean_html import process_dean_lesson_extraction
from src.processing.extract_course_notes import process_course_note_extraction
from src.db.operations import get_file_tag_id
from src.ingestion.tag_registry import get_tag_spec


def _lesson_ids_for_status(session, status: str, tag: str) -> list[int]:
    tag_spec = get_tag_spec(tag)
    tag_id = get_file_tag_id(session, tag_spec.corpus, tag)
    rows = session.execute(
        select(TranscriptLesson.id)
        .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
        .where(TranscriptLesson.status == status, SourceFile.file_tag_id == tag_id)
    ).scalars().all()
    return rows


def _extract_for_lesson(session, lesson_id: int, tag: str) -> dict:
    tag_spec = get_tag_spec(tag)
    if tag == "course_notes":
        return process_course_note_extraction(lesson_id, session)
    if tag_spec.corpus != "dean":
        return process_lesson_extraction(lesson_id, session)

    source_format = session.execute(
        select(SourceFile.source_format)
        .join(TranscriptLesson, TranscriptLesson.source_file_id == SourceFile.id)
        .where(TranscriptLesson.id == lesson_id)
    ).scalar_one_or_none()
    source_format = (source_format or "").lower()
    if tag == "dean_topical_pdfs":
        return process_lesson_extraction(lesson_id, session)
    if source_format in {"htm", "html"}:
        return process_dean_lesson_extraction(lesson_id, session)
    # Default dean extractor path for PDF and other document formats.
    return process_lesson_extraction(lesson_id, session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Processing CLI")
    subparsers = parser.add_subparsers(dest="command")

    extract_parser = subparsers.add_parser("extract", help="Extract paragraphs")
    extract_parser.add_argument("lesson_id", nargs="?", type=int)
    extract_parser.add_argument("--all", action="store_true")
    extract_parser.add_argument("--tag", required=True)

    structure_parser = subparsers.add_parser("structure", help="Detect structure")
    structure_parser.add_argument("lesson_id", nargs="?", type=int)
    structure_parser.add_argument("--all", action="store_true")
    structure_parser.add_argument("--tag", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate structured lessons")
    validate_parser.add_argument("lesson_id", nargs="?", type=int)
    validate_parser.add_argument("--all", action="store_true")
    validate_parser.add_argument("--tag", required=True)

    canonical_parser = subparsers.add_parser("canonical", help="Generate canonical HTML")
    canonical_parser.add_argument("lesson_id", nargs="?", type=int)
    canonical_parser.add_argument("--all", action="store_true")
    canonical_parser.add_argument("--tag", required=True)

    embed_parser = subparsers.add_parser("embed", help="Generate embeddings")
    embed_parser.add_argument("lesson_id", nargs="?", type=int)
    embed_parser.add_argument("--all", action="store_true")
    embed_parser.add_argument("--tag", required=True)

    index_parser = subparsers.add_parser("index", help="Index embeddings into Qdrant")
    index_parser.add_argument("corpus", nargs="?", default="clough")
    index_parser.add_argument("--verify", action="store_true")

    args = parser.parse_args()
    if args.command not in {"extract", "structure", "validate", "canonical", "embed", "index"}:
        raise SystemExit("Use: python -m src.processing extract|structure|validate|canonical|embed|index <lesson_id|corpus> | --all")

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        if args.command == "extract":
            if args.all:
                lessons = _lesson_ids_for_status(session, "pending", args.tag)
                results = []
                for lesson_id in lessons:
                    result = _extract_for_lesson(session, lesson_id, args.tag)
                    results.append({"lesson_id": lesson_id, **result})
                print(json.dumps(results, indent=2))
            else:
                if args.lesson_id is None:
                    raise SystemExit("Lesson ID required unless --all is set")
                result = _extract_for_lesson(session, args.lesson_id, args.tag)
                print(json.dumps(result, indent=2))
        elif args.command == "structure":
            from src.processing.structure import process_lesson_structure

            if args.all:
                lessons = _lesson_ids_for_status(session, "extracted", args.tag)
                results = []
                for lesson_id in lessons:
                    result = process_lesson_structure(lesson_id, session)
                    results.append({"lesson_id": lesson_id, **result})
                print(json.dumps(results, indent=2))
            else:
                if args.lesson_id is None:
                    raise SystemExit("Lesson ID required unless --all is set")
                result = process_lesson_structure(args.lesson_id, session)
                print(json.dumps(result, indent=2))
        elif args.command == "validate":
            from src.processing.validate import validate_lesson_structure

            if args.all:
                lessons = _lesson_ids_for_status(session, "structured", args.tag)
                results = []
                for lesson_id in lessons:
                    result = validate_lesson_structure(lesson_id, session)
                    results.append({"lesson_id": lesson_id, **result})
                print(json.dumps(results, indent=2))
            else:
                if args.lesson_id is None:
                    raise SystemExit("Lesson ID required unless --all is set")
                result = validate_lesson_structure(args.lesson_id, session)
                print(json.dumps(result, indent=2))
        elif args.command == "canonical":
            from src.processing.canonical import generate_canonical_html

            if args.all:
                lessons = _lesson_ids_for_status(session, "validated", args.tag)
                results = []
                for lesson_id in lessons:
                    path = generate_canonical_html(lesson_id, session)
                    results.append({"lesson_id": lesson_id, "status": "success", "html_path": str(path)})
                print(json.dumps(results, indent=2))
            else:
                if args.lesson_id is None:
                    raise SystemExit("Lesson ID required unless --all is set")
                path = generate_canonical_html(args.lesson_id, session)
                print(json.dumps({"status": "success", "html_path": str(path)}, indent=2))
        elif args.command == "embed":
            from src.embedding.generate import process_lesson_embeddings

            if args.all:
                lessons = _lesson_ids_for_status(session, "child_chunked", args.tag)
                results = []
                for lesson_id in lessons:
                    result = process_lesson_embeddings(lesson_id, session)
                    results.append({"lesson_id": lesson_id, **result})
                print(json.dumps(results, indent=2))
            else:
                if args.lesson_id is None:
                    raise SystemExit("Lesson ID required unless --all is set")
                result = process_lesson_embeddings(args.lesson_id, session)
                print(json.dumps(result, indent=2))
        else:
            from src.embedding.index import index_corpus_to_qdrant, verify_qdrant_index, verify_qdrant_parity
            from src.utils.config import get_config
            from src.utils.qdrant_client import get_qdrant_client

            _ = get_config()
            client = get_qdrant_client(timeout=30.0)
            corpus = args.corpus

            if args.verify:
                verify = verify_qdrant_index(corpus, client)
                parity = verify_qdrant_parity(corpus, session, client)
                result = {
                    "verify": verify,
                    "parity": parity,
                }
                print(json.dumps(result, indent=2))
            else:
                result = index_corpus_to_qdrant(corpus, session)
                print(json.dumps(result, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
