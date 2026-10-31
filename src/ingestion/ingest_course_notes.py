"""Clough course-notes PDF ingestion (single lesson per volume)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz
import structlog
from sqlalchemy.orm import Session

from src.db.models import CourseNoteUnit, CourseNoteVolume
from src.db.operations import (
    check_duplicate_checksum,
    get_file_tag_id,
    get_or_create_chapter,
    get_or_create_part,
    get_or_create_series,
    insert_source_file,
    insert_transcript_lesson,
)
from src.db.session import get_sessionmaker
from src.ingestion.shared import (
    calculate_checksum as _checksum,
    ensure_raw_copy,
    mark_source_file_completed,
)
from src.ingestion.tag_registry import get_tag_spec
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()

LESSON_MARKER_RE = re.compile(r"^\s*Lesson\s+(\d{1,3})(?:\s*[-:]\s*(.+))?\s*$", re.IGNORECASE)
PART_RE = re.compile(r"\bPart\s+([IVXLC]+|\d+)\b", re.IGNORECASE)
PART_FILENAME_RE = re.compile(r"CourseNotes-(\d+)", re.IGNORECASE)
NOISE_LINE_RE = re.compile(
    r"^\s*(?:P\s*age\s+[ivxlcdm0-9]+.*|P\s*art\s+[ivxlcdm0-9]+.*|Bible\s+Fram\w*\s+M\w*|w+\s*\.\s*bibleframework\s*\.\s*org.*)\s*$",
    re.IGNORECASE,
)


def _normalize_line(raw: str) -> str:
    line = raw.replace("\x0c", " ").strip()
    line = re.sub(r"\s+", " ", line).strip()
    if not line:
        return ""
    if "intentionally left blank" in line.lower():
        return ""
    if NOISE_LINE_RE.match(line):
        return ""
    return line


def _extract_pdf_text(pdf_path: Path) -> tuple[list[list[str]], dict[str, Any]]:
    page_lines: list[list[str]] = []
    with fitz.open(pdf_path) as doc:
        metadata = doc.metadata or {}
        for page in doc:
            text = page.get_text("text")
            lines = [_normalize_line(raw) for raw in text.splitlines()]
            lines = [line for line in lines if line]
            page_lines.append(lines)
    return page_lines, metadata


def _roman_to_int(value: str) -> int | None:
    text_value = value.strip().upper()
    if text_value.isdigit():
        return int(text_value)
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    prev = 0
    for ch in reversed(text_value):
        if ch not in roman_map:
            return None
        n = roman_map[ch]
        if n < prev:
            total -= n
        else:
            total += n
            prev = n
    return total if total > 0 else None


def _derive_part_metadata(filename: str, page_lines: list[list[str]], pdf_meta: dict[str, Any]) -> tuple[int, str]:
    part_number = None
    match = PART_FILENAME_RE.search(filename)
    if match:
        part_number = int(match.group(1))

    cover_lines = []
    for lines in page_lines[:3]:
        cover_lines.extend(lines[:40])

    if part_number is None:
        for line in cover_lines:
            part_match = PART_RE.search(line)
            if part_match:
                part_number = _roman_to_int(part_match.group(1))
                break
    if part_number is None:
        part_number = 0

    part_title = ""
    for idx, line in enumerate(cover_lines):
        part_match = PART_RE.match(line)
        if not part_match:
            continue
        suffix = line[part_match.end() :].strip(":- ").strip()
        if suffix:
            part_title = suffix
            break
        if idx + 1 < len(cover_lines):
            candidate = cover_lines[idx + 1].strip()
            if candidate:
                part_title = candidate
                break

    if not part_title:
        meta_title = (pdf_meta.get("title") or "").strip()
        if meta_title and "part" in meta_title.lower():
            part_title = meta_title
    if not part_title:
        part_title = f"Course Notes Part {part_number}" if part_number else "Course Notes"

    return part_number, part_title


def ingest_course_notes(pdf_path: Path, tag: str, session: Session | None = None) -> dict:
    setup_logging()
    tag_spec = get_tag_spec(tag)
    if tag_spec.corpus != "clough" or tag != "course_notes":
        raise ValueError(f"Tag '{tag}' is not a clough course_notes tag.")
    if pdf_path.suffix.lower() != ".pdf":
        return {"status": "skipped", "lesson_id": None, "checksum": None, "message": "Not a PDF file"}

    log = logger.bind(function="ingest_course_notes", tag=tag, filename=pdf_path.name)
    checksum = _checksum(pdf_path)
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

        page_lines, pdf_meta = _extract_pdf_text(pdf_path)
        page_count = len(page_lines)
        if page_count == 0:
            raise ValueError("No readable pages detected in course notes PDF")
        part_number, part_title = _derive_part_metadata(pdf_path.name, page_lines, pdf_meta)
        series_title = "Biblical Framework Course Notes"
        speaker = (pdf_meta or {}).get("author") or "Charles A. Clough"
        speaker = speaker.strip() or "Charles A. Clough"

        raw_path = Path(get_config().raw_data_path) / "clough" / "course_notes" / pdf_path.name
        ensure_raw_copy(pdf_path, raw_path)

        file_tag_id = get_file_tag_id(session, "clough", tag)
        source_file_id = insert_source_file(
            session,
            file_tag_id=file_tag_id,
            filename=pdf_path.name,
            raw_path=str(raw_path),
            checksum=checksum,
            ingestion_status="processing",
            source_format="pdf",
            source_kind="course_notes",
        )

        series_id = get_or_create_series(session, "clough", series_title)
        part_id = get_or_create_part(session, series_id, part_number, part_title)
        chapter_id = get_or_create_chapter(session, part_id, 0, "Course Notes")

        lesson_number = part_number if part_number > 0 else 0
        lesson_title = (
            f"Course Notes Part {part_number:02d}: {part_title}"
            if part_number > 0
            else part_title
        )
        lesson_id = insert_transcript_lesson(
            session=session,
            source_file_id=source_file_id,
            chapter_id=chapter_id,
            part_id=part_id,
            lesson_number=lesson_number,
            metadata={
                "lesson_title": lesson_title,
                "date": None,
                "speaker": speaker,
                "location": None,
                "transcriber": None,
            },
            source_unit_key="root",
        )

        volume = CourseNoteVolume(
            source_file_id=source_file_id,
            series_id=series_id,
            part_id=part_id,
            part_number=part_number,
            part_title=part_title,
            volume_code=f"course_notes_{part_number:02d}" if part_number else "course_notes",
            page_count=page_count,
            metadata_json={
                "filename": pdf_path.name,
                "pdf_metadata": pdf_meta or {},
                "source_model": "single_lesson_root",
            },
        )
        session.add(volume)
        session.flush()

        # Optional diagnostics row: non-authoritative "full document" span.
        session.add(
            CourseNoteUnit(
                source_file_id=source_file_id,
                lesson_id=lesson_id,
                logical_lesson_number=0,
                unit_title="full_document",
                page_start=1,
                page_end=page_count,
                boundary_confidence=1.0,
                metadata_json={
                    "authoritative": False,
                    "scope": "full_document",
                },
            )
        )

        mark_source_file_completed(session, source_file_id)
        session.commit()
        log.info(
            "course_notes_ingestion_complete",
            source_file_id=str(source_file_id),
            lesson_id=lesson_id,
            part_number=part_number,
        )
        return {
            "status": "success",
            "lesson_id": lesson_id,
            "checksum": checksum,
            "message": "Ingested course notes volume as a single lesson",
        }
    except Exception as exc:
        session.rollback()
        log.error("course_notes_ingestion_failed", error=str(exc))
        return {"status": "error", "lesson_id": None, "checksum": checksum, "message": str(exc)}
    finally:
        if session_created:
            session.close()
