"""PDF ingestion orchestrator."""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from src.db.operations import (
    check_duplicate_checksum,
    get_or_create_part,
    get_or_create_series,
    get_or_create_chapter,
    get_file_tag_id,
    insert_source_file,
    insert_transcript_lesson,
)
from src.db.session import get_sessionmaker
from src.ingestion.extract_metadata import MetadataExtractionError, extract_metadata_from_pdf
from src.ingestion.shared import (
    calculate_checksum as _calculate_checksum,
    ensure_raw_copy,
    mark_source_file_completed,
)
from src.ingestion.tag_registry import get_tag_spec
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()


def calculate_checksum(pdf_path: Path) -> str:
    """Calculate SHA-256 checksum."""
    return _calculate_checksum(pdf_path)


def _resolve_raw_path(pdf_path: Path, corpus_name: str) -> Path:
    """Resolve destination path for raw storage."""
    config = get_config()
    return Path(config.raw_data_path) / corpus_name / pdf_path.name


def _load_lesson_overrides() -> dict:
    config = get_config()
    try:
        return json.loads(config.transcript_lesson_overrides or "{}")
    except json.JSONDecodeError:
        return {}


def _lesson_number_from_filename(filename: str) -> int | None:
    stem = Path(filename).stem.lower()
    patterns = (
        r"lesson[_\-\s]?(\d{1,3})",
        r"bf[-_](\d{1,3})",
        r"-(\d{1,3})-transcript",
        r"_(\d{1,3})_transcript",
    )
    for pattern in patterns:
        match = re.search(pattern, stem)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if value >= 0:
            return value
    return None


def _resolve_lesson_number(metadata: dict, filename: str) -> int | None:
    # Deterministic fallback order: header/content -> filename -> configured override.
    if metadata.get("lesson_number") is not None:
        return int(metadata["lesson_number"])
    from_filename = _lesson_number_from_filename(filename)
    if from_filename is not None:
        return from_filename
    overrides = _load_lesson_overrides()
    if filename in overrides:
        return int(overrides[filename])
    return None


def ingest_pdf(pdf_path: Path, tag: str, session: Session | None = None) -> dict:
    """Main ingestion orchestrator."""
    setup_logging()
    tag_spec = get_tag_spec(tag)
    corpus_name = tag_spec.corpus
    log = logger.bind(function="ingest_pdf", corpus=corpus_name, tag=tag, filename=pdf_path.name)

    if not pdf_path.exists():
        log.error("file_not_found", path=str(pdf_path))
        return {"status": "error", "lesson_id": None, "checksum": None, "message": "File not found"}

    checksum = calculate_checksum(pdf_path)
    log.info("checksum_calculated", checksum=checksum[:8])

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

        metadata = extract_metadata_from_pdf(pdf_path)

        if metadata.get("part_number") is None or metadata.get("part_title") is None:
            raise MetadataExtractionError("Missing part_number or part_title required for schema")

        lesson_number = _resolve_lesson_number(metadata, pdf_path.name)
        if lesson_number is None:
            log.warning("lesson_number_missing_defaulting_zero", filename=pdf_path.name)
            lesson_number = 0

        raw_path = _resolve_raw_path(pdf_path, corpus_name)
        copied_before = raw_path.exists()
        ensure_raw_copy(pdf_path, raw_path)
        if not copied_before:
            log.info("raw_file_copied", raw_path=str(raw_path))

        series_id = get_or_create_series(session, corpus_name, metadata["series_name"])
        part_id = get_or_create_part(session, series_id, metadata["part_number"], metadata["part_title"])

        chapter_id = None
        chapter_info = metadata.get("chapter_info")
        if chapter_info:
            chapter_number_raw = chapter_info.get("chapter_number")
            chapter_title = (chapter_info.get("chapter_title") or "").strip()
            # Chapter is optional; never synthesize a numeric chapter when absent.
            if chapter_number_raw is not None:
                chapter_number = int(chapter_number_raw)
            else:
                chapter_number = None

        else:
            chapter_number = None
            chapter_title = ""

        if chapter_number is not None:
            chapter_id = get_or_create_chapter(
                session,
                part_id,
                chapter_number,
                chapter_title,
            )

        file_tag_id = get_file_tag_id(session, corpus_name, tag)
        source_file_id = insert_source_file(
            session,
            file_tag_id=file_tag_id,
            filename=pdf_path.name,
            raw_path=str(raw_path),
            checksum=checksum,
            ingestion_status="processing",
            source_format="pdf",
            source_kind="transcripts",
        )

        lesson_id = insert_transcript_lesson(
            session,
            source_file_id=source_file_id,
            chapter_id=chapter_id,
            lesson_number=int(lesson_number),
            metadata=metadata,
            part_id=part_id,
        )

        mark_source_file_completed(session, source_file_id)

        session.commit()
        log.info("ingestion_complete", lesson_id=lesson_id)
        return {
            "status": "success",
            "lesson_id": lesson_id,
            "checksum": checksum,
            "message": f"Ingested [{tag}]: Part {metadata['part_number']}, Lesson {lesson_number}",
        }
    except (MetadataExtractionError, FileNotFoundError, FileExistsError) as exc:
        session.rollback()
        log.error("ingestion_failed", error=str(exc))
        return {"status": "error", "lesson_id": None, "checksum": checksum, "message": str(exc)}
    except Exception as exc:
        session.rollback()
        log.error("ingestion_failed_unexpected", error=str(exc))
        return {"status": "error", "lesson_id": None, "checksum": checksum, "message": str(exc)}
    finally:
        if session_created:
            session.close()
