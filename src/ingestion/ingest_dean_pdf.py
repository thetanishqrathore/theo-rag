"""Dean topical PDF ingestion orchestrator with profile-aware metadata extraction."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import fitz
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
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()

_LESSON_RE = re.compile(r"\bLesson\s*#?\s*(\d{1,4}[A-Za-z]?)\b", flags=re.IGNORECASE)
_PART_RE = re.compile(r"\bPart\s*\(?\s*(\d{1,3})\s*(?:of\s*(\d{1,3}))?\)?\b", flags=re.IGNORECASE)
_DATE_RE_1 = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b",
    flags=re.IGNORECASE,
)
_DATE_RE_2 = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
    flags=re.IGNORECASE,
)
_SPEAKER_RE = re.compile(
    r"\b(?:by\s+)?(Dr\.?\s+[A-Z][A-Za-z.\- ]{2,60}|Robert\s+Dean(?:,?\s*Jr\.?)?|John\s+D\.\s+Hannah)\b",
    flags=re.IGNORECASE,
)
_TOP_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9\s:,\-'.()]{8,}$")
_JUNK_TITLE_PREFIX_RE = re.compile(r"^(file://|microsoft word\b|i$)", flags=re.IGNORECASE)
_PAGE_MARKER_RE = re.compile(r"\bPage\s+\d+\s+of\s+\d+\b", flags=re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:www\.)?[a-z0-9\-]+\.[a-z]{2,}(?:/[^\s]*)?\b", flags=re.IGNORECASE)
_REPEATED_WS_RE = re.compile(r"\s+")


def _read_pdf_signals(pdf_path: Path, pages: int = 2) -> tuple[dict[str, Any], str, str]:
    metadata: dict[str, Any] = {}
    top_text = ""
    full_text = ""
    with fitz.open(pdf_path) as doc:
        metadata = doc.metadata or {}
        page_count = doc.page_count
        excerpts: list[str] = []
        for idx in range(min(pages, page_count)):
            excerpts.append(doc.load_page(idx).get_text("text"))
        top_text = "\n".join(excerpts)
        full_chunks: list[str] = []
        for idx in range(page_count):
            full_chunks.append(doc.load_page(idx).get_text("text"))
        full_text = "\n".join(full_chunks)
    return metadata, top_text, full_text


def detect_topical_pdf_profile(top_text: str, rel_path: str) -> tuple[str, float, list[str]]:
    signals: list[str] = []
    normalized = top_text or ""
    if "History of Doctrine" in normalized and _LESSON_RE.search(normalized):
        return "chafer_lesson_pdf", 0.95, ["history_of_doctrine", "lesson_marker"]
    if ("Dean Bible Ministries" in normalized or "deanbibleministries.org" in normalized) and _PART_RE.search(normalized):
        return "dbm_slides_pdf", 0.9, ["dbm_branding", "part_marker"]
    if _PAGE_MARKER_RE.search(normalized):
        return "legacy_html_export_pdf", 0.82, ["page_markers"]
    if "topical_studies/pdf/History_Doctrine/" in rel_path.replace("\\", "/"):
        signals.append("history_doctrine_path")
    return "article_pdf", (0.7 if signals else 0.6), signals


def extract_title_with_source(stem: str, top_text: str, pdf_metadata: dict[str, Any]) -> tuple[str, str]:
    lines = [line.strip() for line in top_text.splitlines() if line.strip()]
    for line in lines[:20]:
        if _PAGE_MARKER_RE.search(line):
            continue
        clean = _REPEATED_WS_RE.sub(" ", line).strip(":- ")
        if len(clean) < 8:
            continue
        if clean.lower().startswith("lesson #"):
            continue
        if clean.lower().startswith("summary"):
            continue
        if _TOP_HEADING_RE.match(clean) or clean.istitle():
            return clean.title() if clean.isupper() else clean, "content"
    meta_title = (pdf_metadata.get("title") or "").strip()
    if meta_title and not _JUNK_TITLE_PREFIX_RE.search(meta_title):
        return meta_title, "pdf_metadata"
    return stem.replace("_", " ").strip(), "filename"


def _normalize_speaker_name(raw: str) -> str:
    value = raw.strip().replace("  ", " ")
    value = re.sub(r"\s+", " ", value)
    value = value.replace("Jr", "Jr.")
    return value.strip(" .")


def extract_speaker_with_source(top_text: str, default_speaker: str) -> tuple[str, str]:
    for line in [ln.strip() for ln in top_text.splitlines() if ln.strip()][:80]:
        if _URL_RE.search(line):
            continue
        match = _SPEAKER_RE.search(line)
        if not match:
            continue
        candidate = _normalize_speaker_name(match.group(1))
        if len(candidate) >= 6:
            return candidate, "content"
    return default_speaker, "default"


def _parse_filename_lesson(stem: str) -> tuple[int | None, str | None]:
    lesson_number, lesson_code = _parse_lesson(stem)
    if lesson_number is None:
        return None, None
    return lesson_number, (lesson_code or stem)


def extract_lesson_number_with_source(
    stem: str,
    top_text: str,
    session: Session,
    book_id: int,
) -> tuple[int, str, str]:
    lesson_match = _LESSON_RE.search(top_text)
    if lesson_match:
        raw = lesson_match.group(1)
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            return int(digits), raw, "content"
    parsed = _parse_filename_lesson(stem)
    if parsed[0] is not None:
        return parsed[0], parsed[1] or stem, "filename"
    next_number = _next_lesson_number(session, book_id)
    return next_number, stem, "folder_sequence"


def extract_date_with_source(top_text: str, pdf_metadata: dict[str, Any]) -> tuple[date | None, str]:
    match = _DATE_RE_1.search(top_text)
    if match:
        try:
            parsed = datetime.strptime(f"{match.group(1)} {match.group(2)}, {match.group(3)}", "%B %d, %Y").date()
            return parsed, "content"
        except ValueError:
            pass
    match = _DATE_RE_2.search(top_text)
    if match:
        try:
            parsed = datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%d %b %Y").date()
            return parsed, "content"
        except ValueError:
            pass
    mod_date = (pdf_metadata.get("modDate") or "").strip()
    meta_match = re.search(r"D:(\d{4})(\d{2})(\d{2})", mod_date)
    if meta_match:
        try:
            return date(int(meta_match.group(1)), int(meta_match.group(2)), int(meta_match.group(3))), "metadata"
        except ValueError:
            pass
    return None, "none"


def ingest_dean_pdf(pdf_path: Path, tag: str, session: Session | None = None) -> dict:
    """Ingest one Dean PDF source and create linked Dean/shared lesson rows."""
    setup_logging()
    tag_spec = get_tag_spec(tag)
    if tag_spec.corpus != "dean":
        raise ValueError(f"Tag '{tag}' is not a Dean tag.")
    if pdf_path.suffix.lower() != ".pdf":
        return {"status": "skipped", "lesson_id": None, "checksum": None, "message": "Not a PDF file"}

    collection, source_root = _resolve_collection(pdf_path)
    rel_path = _resolve_rel_path(pdf_path, source_root)
    filename = pdf_path.name
    stem = pdf_path.stem
    log = logger.bind(function="ingest_dean_pdf", corpus="dean", tag=tag, filename=filename, collection=collection)

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

        raw_path = Path(get_config().raw_data_path) / "dean" / rel_path
        ensure_raw_copy(pdf_path, raw_path)

        book_slug, display_name, extra_meta = _resolve_book(pdf_path, source_root, collection)
        collection_id = get_or_create_dean_collection(session, "dean", collection, source_root=source_root)
        book_id = get_or_create_dean_book(
            session,
            collection_id=collection_id,
            book_slug=book_slug,
            display_name=display_name,
            parser_profile=f"dean_{collection}_pdf_v1",
        )

        pdf_metadata, top_text, _full_text = _read_pdf_signals(pdf_path)
        profile, profile_confidence, quality_flags = detect_topical_pdf_profile(top_text, rel_path)
        title, title_source = extract_title_with_source(stem, top_text, pdf_metadata)
        speaker, speaker_source = extract_speaker_with_source(top_text, get_config().dean_default_speaker)
        lesson_number, lesson_code, lesson_number_source = extract_lesson_number_with_source(
            stem, top_text, session, book_id
        )
        parsed_date, date_source = extract_date_with_source(top_text, pdf_metadata)

        file_tag_id = get_file_tag_id(session, "dean", tag)
        source_file_id = insert_source_file(
            session,
            file_tag_id=file_tag_id,
            filename=filename,
            raw_path=str(raw_path),
            checksum=checksum,
            ingestion_status="processing",
            source_format="pdf",
            source_kind=collection,
        )

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
            date=parsed_date,
            parser_version=f"{get_config().dean_html_parser_version}-pdf",
            metadata_json={
                **extra_meta,
                "source_tag": tag,
                "source_format": "pdf",
                "parser_profile": profile,
                "profile_confidence": profile_confidence,
                "title_source": title_source,
                "speaker_source": speaker_source,
                "lesson_number_source": lesson_number_source,
                "date_source": date_source,
                "quality_flags": quality_flags,
                "engine_candidates": [],
                "selected_engine": None,
                "selected_engine_reason": None,
                "structure_quality_score": None,
                "ocr_used": None,
                "scan_confidence": None,
            },
        )

        lesson_id = insert_transcript_lesson(
            session,
            source_file_id=source_file_id,
            chapter_id=None,
            lesson_number=lesson_number,
            metadata={
                "lesson_title": title,
                "date": parsed_date,
                "speaker": speaker,
                "location": None,
                "transcriber": None,
            },
            dean_lesson_id=dean_lesson_id,
        )
        mark_source_file_completed(session, source_file_id)
        session.commit()
        log.info(
            "dean_pdf_ingestion_complete",
            lesson_id=lesson_id,
            dean_lesson_id=dean_lesson_id,
            parser_profile=profile,
            lesson_number_source=lesson_number_source,
        )
        return {
            "status": "success",
            "lesson_id": lesson_id,
            "checksum": checksum,
            "message": f"Ingested Dean PDF [{collection}] lesson {lesson_number}",
        }
    except Exception as exc:
        session.rollback()
        log.error("dean_pdf_ingestion_failed", error=str(exc))
        return {"status": "error", "lesson_id": None, "checksum": checksum, "message": str(exc)}
    finally:
        if session_created:
            session.close()
