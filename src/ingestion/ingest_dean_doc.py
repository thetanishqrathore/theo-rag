"""Dean topical DOC/RTF ingestion orchestrator with deterministic metadata fallbacks."""

from __future__ import annotations

from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from src.db.operations import (
    check_duplicate_checksum,
    get_file_tag_id,
    get_or_create_dean_book,
    get_or_create_dean_collection,
    insert_dean_lesson,
    insert_source_file,
    insert_transcript_lesson,
)
from src.db.session import get_sessionmaker
from src.ingestion.shared import (
    calculate_checksum as _checksum,
    ensure_raw_copy,
    mark_source_file_completed,
    next_dean_lesson_number as _next_lesson_number,
    parse_dean_lesson as _parse_lesson,
    resolve_dean_book as _resolve_book,
    resolve_dean_collection as _resolve_collection,
    resolve_dean_rel_path as _resolve_rel_path,
)
from src.ingestion.tag_registry import get_tag_spec
from src.processing.profiles import detect_ingest_doc_profile
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()


def _detect_doc_subtype(path: Path) -> str:
    header = path.read_bytes()[:16]
    if header.startswith(b"{\\rtf"):
        return "rtf_payload_doc"
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole_word_doc"
    return "doc_unknown"


def ingest_dean_doc(doc_path: Path, tag: str, session: Session | None = None) -> dict:
    """Ingest one Dean DOC/RTF source and create linked Dean/shared lesson rows."""
    setup_logging()
    tag_spec = get_tag_spec(tag)
    if tag_spec.corpus != "dean":
        raise ValueError(f"Tag '{tag}' is not a Dean tag.")
    if doc_path.suffix.lower() not in {".doc", ".docx", ".rtf"}:
        return {"status": "skipped", "lesson_id": None, "checksum": None, "message": "Not a DOC/DOCX/RTF file"}

    collection, source_root = _resolve_collection(doc_path)
    rel_path = _resolve_rel_path(doc_path, source_root)
    filename = doc_path.name
    stem = doc_path.stem
    log = logger.bind(function="ingest_dean_doc", corpus="dean", tag=tag, filename=filename, collection=collection)

    checksum = _checksum(doc_path)
    session_created = False
    if session is None:
        SessionLocal = get_sessionmaker()
        session = SessionLocal()
        session_created = True

    try:
        duplicate_id = check_duplicate_checksum(session, checksum)
        if duplicate_id:
            return {
                "status": "duplicate",
                "lesson_id": None,
                "checksum": checksum,
                "message": "Duplicate checksum",
            }

        raw_path = Path(get_config().raw_data_path) / "dean" / rel_path
        ensure_raw_copy(doc_path, raw_path)

        book_slug, display_name, extra_meta = _resolve_book(doc_path, source_root, collection)
        collection_id = get_or_create_dean_collection(session, "dean", collection, source_root=source_root)
        book_id = get_or_create_dean_book(
            session,
            collection_id=collection_id,
            book_slug=book_slug,
            display_name=display_name,
            parser_profile=f"dean_{collection}_doc_v1",
        )

        lesson_number, lesson_code = _parse_lesson(stem)
        lesson_number_source = "filename"
        if lesson_number is None:
            lesson_number = _next_lesson_number(session, book_id)
            lesson_code = stem
            lesson_number_source = "folder_sequence"

        doc_subtype = _detect_doc_subtype(doc_path)
        parser_profile = detect_ingest_doc_profile(rel_path)
        title = stem.replace("_", " ").replace("-", " ").strip()
        speaker = get_config().dean_default_speaker

        file_tag_id = get_file_tag_id(session, "dean", tag)
        source_file_id = insert_source_file(
            session,
            file_tag_id=file_tag_id,
            filename=filename,
            raw_path=str(raw_path),
            checksum=checksum,
            ingestion_status="processing",
            source_format=doc_path.suffix.lower().lstrip("."),
            source_kind=collection,
        )

        source_ext = doc_path.suffix.lower().lstrip(".")
        parser_suffix = "rtf" if source_ext == "rtf" else "doc"
        dean_lesson_id = insert_dean_lesson(
            session,
            source_file_id=source_file_id,
            book_id=book_id,
            lesson_number=lesson_number,
            lesson_code=lesson_code,
            title=title,
            source_filename=filename,
            source_rel_path=rel_path,
            speaker=speaker,
            date=None,
            parser_version=f"{get_config().dean_html_parser_version}-{parser_suffix}",
            metadata_json={
                **extra_meta,
                "source_tag": tag,
                "source_format": source_ext,
                "parser_profile": parser_profile,
                "profile_confidence": 0.72,
                "title_source": "filename",
                "speaker_source": "default",
                "lesson_number_source": lesson_number_source,
                "date_source": "none",
                "quality_flags": [doc_subtype],
                "input_doc_subtype": doc_subtype,
                "engine_candidates": [],
                "selected_engine": None,
                "selected_engine_reason": None,
                "structure_quality_score": None,
                "ocr_used": False,
                "scan_confidence": 0.0,
            },
        )

        lesson_id = insert_transcript_lesson(
            session,
            source_file_id=source_file_id,
            chapter_id=None,
            lesson_number=lesson_number,
            metadata={
                "lesson_title": title,
                "date": None,
                "speaker": speaker,
                "location": None,
                "transcriber": None,
            },
            dean_lesson_id=dean_lesson_id,
        )
        mark_source_file_completed(session, source_file_id)
        session.commit()
        log.info(
            "dean_doc_ingestion_complete",
            lesson_id=lesson_id,
            dean_lesson_id=dean_lesson_id,
            parser_profile=parser_profile,
            lesson_number_source=lesson_number_source,
            doc_subtype=doc_subtype,
        )
        return {
            "status": "success",
            "lesson_id": lesson_id,
            "checksum": checksum,
            "message": f"Ingested Dean DOC [{collection}] lesson {lesson_number}",
        }
    except Exception as exc:
        session.rollback()
        log.error("dean_doc_ingestion_failed", error=str(exc))
        return {"status": "error", "lesson_id": None, "checksum": checksum, "message": str(exc)}
    finally:
        if session_created:
            session.close()
