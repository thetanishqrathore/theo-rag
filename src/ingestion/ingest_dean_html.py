"""Dean HTML ingestion orchestrator."""

from __future__ import annotations

import json
import re
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
    parse_dean_lesson as _parse_lesson,
    next_dean_lesson_number,
    resolve_dean_book as _resolve_book,
    resolve_dean_collection as _resolve_collection,
    resolve_dean_rel_path as _resolve_rel_path,
)
from src.ingestion.tag_registry import get_tag_spec
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_FIRST_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", flags=re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_title(path: Path) -> str | None:
    html = _read_text(path)[:100000]
    match = _TITLE_RE.search(html)
    if not match:
        p_match = _FIRST_P_RE.search(html)
        if not p_match:
            return None
        # Handle Word-style header blocks where title/lesson/date/site are on separate <br> lines.
        fragment = re.sub(r"<br\s*/?>", "\n", p_match.group(1), flags=re.IGNORECASE)
        raw = _TAG_RE.sub(" ", fragment)
        lines = [_WS_RE.sub(" ", line).strip() for line in raw.splitlines()]
        lines = [line for line in lines if line]
        return lines[0] if lines else None
    raw = _TAG_RE.sub(" ", match.group(1))
    return _WS_RE.sub(" ", raw).strip() or None


def _load_overrides() -> dict[str, int]:
    config = get_config()
    try:
        raw = json.loads(config.dean_lesson_overrides or "{}")
    except json.JSONDecodeError:
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def ingest_dean_html(html_path: Path, tag: str, session: Session | None = None) -> dict:
    """Ingest one Dean HTML source and create linked Dean/transcript lesson rows."""
    setup_logging()
    tag_spec = get_tag_spec(tag)
    if tag_spec.corpus != "dean":
        raise ValueError(f"Tag '{tag}' is not a Dean tag.")
    if html_path.suffix.lower() not in {".htm", ".html"}:
        return {"status": "skipped", "lesson_id": None, "checksum": None, "message": "Not an HTML file"}
    if html_path.name.lower() == "scripture_index_new.htm" or "_files" in html_path.as_posix():
        return {"status": "skipped", "lesson_id": None, "checksum": None, "message": "Excluded support/index file"}

    collection, source_root = _resolve_collection(html_path)
    rel_path = _resolve_rel_path(html_path, source_root)
    filename = html_path.name
    overrides = _load_overrides()
    stem = html_path.stem
    log = logger.bind(function="ingest_dean_html", corpus="dean", tag=tag, filename=filename, collection=collection)

    checksum = _checksum(html_path)
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

        lesson_number, lesson_code = _parse_lesson(stem)
        lesson_number_source = "filename"
        override_key_candidates = [rel_path, filename, stem]
        for key in override_key_candidates:
            if key in overrides:
                lesson_number = overrides[key]
                if not lesson_code:
                    lesson_code = stem
                lesson_number_source = "override"
                break
        if lesson_number is None and collection == "bible_books":
            raise ValueError(f"Unable to derive lesson_number for '{filename}'. Add override in DEAN_LESSON_OVERRIDES.")

        raw_path = Path(get_config().raw_data_path) / "dean" / rel_path
        ensure_raw_copy(html_path, raw_path)

        book_slug, display_name, extra_meta = _resolve_book(html_path, source_root, collection)
        collection_id = get_or_create_dean_collection(session, "dean", collection, source_root=source_root)
        parser_profile = "dean_bible_html_v2" if collection == "bible_books" else "dean_topical_html_v2"
        book_id = get_or_create_dean_book(
            session,
            collection_id=collection_id,
            book_slug=book_slug,
            display_name=display_name,
            parser_profile=parser_profile,
        )
        if lesson_number is None:
            # Single-document topical studies carry no number; sequence them within
            # their topic like the DOC/PDF topical ingesters do.
            lesson_number = next_dean_lesson_number(session, book_id)
            lesson_code = stem
            lesson_number_source = "folder_sequence"

        file_tag_id = get_file_tag_id(session, "dean", tag)
        source_file_id = insert_source_file(
            session,
            file_tag_id=file_tag_id,
            filename=filename,
            raw_path=str(raw_path),
            checksum=checksum,
            ingestion_status="processing",
            source_format=html_path.suffix.lower().lstrip("."),
            source_kind=collection,
        )
        title = _extract_title(html_path)
        dean_lesson_id = insert_dean_lesson(
            session,
            source_file_id=source_file_id,
            book_id=book_id,
            lesson_number=lesson_number,
            lesson_code=lesson_code,
            title=title,
            source_filename=filename,
            source_rel_path=rel_path,
            speaker=get_config().dean_default_speaker,
            parser_version=f"{get_config().dean_html_parser_version}-html",
            metadata_json={
                **extra_meta,
                "source_tag": tag,
                "source_format": html_path.suffix.lower().lstrip("."),
                "parser_profile": parser_profile,
                "profile_confidence": 1.0,
                "title_source": "title_tag_or_first_p" if title else "none",
                "speaker_source": "default",
                "lesson_number_source": lesson_number_source,
                "date_source": "none",
                "quality_flags": [],
                "engine_candidates": [],
                "selected_engine": None,
                "selected_engine_reason": None,
                "structure_quality_score": None,
                "scan_confidence": 0.0,
                "ocr_used": False,
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
                "speaker": get_config().dean_default_speaker,
                "location": None,
                "transcriber": None,
            },
            dean_lesson_id=dean_lesson_id,
        )
        mark_source_file_completed(session, source_file_id)
        session.commit()
        log.info("dean_ingestion_complete", lesson_id=lesson_id, dean_lesson_id=dean_lesson_id)
        return {
            "status": "success",
            "lesson_id": lesson_id,
            "checksum": checksum,
            "message": f"Ingested Dean HTML [{collection}] lesson {lesson_number}",
        }
    except Exception as exc:
        session.rollback()
        log.error("dean_ingestion_failed", error=str(exc))
        return {"status": "error", "lesson_id": None, "checksum": checksum, "message": str(exc)}
    finally:
        if session_created:
            session.close()
