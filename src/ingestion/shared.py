"""Shared ingestion helpers used across corpus ingestors."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

DEAN_YEAR_FILE_RE = re.compile(r"^(?P<year>\d{4})-[^-]+-(?P<code>\d+[A-Za-z]?)$", flags=re.IGNORECASE)
DEAN_SIMPLE_FILE_RE = re.compile(r"^[^-]+-(?P<code>\d+[A-Za-z]?)$", flags=re.IGNORECASE)
DEAN_SUFFIX_FILE_RE = re.compile(r"^[A-Za-z]+(?P<code>\d+)$", flags=re.IGNORECASE)
_TOPICAL_FORMAT_DIRS = {"html", "htm", "pdf", "doc", "docx", "rtf"}
DEAN_TRAILING_DIGITS_RE = re.compile(r"(?P<digits>\d+)(?!.*\d)")


def calculate_checksum(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def ensure_raw_copy(source_path: Path, raw_path: Path) -> None:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    source_checksum = calculate_checksum(source_path)
    if raw_path.exists():
        existing_checksum = calculate_checksum(raw_path)
        if existing_checksum != source_checksum:
            raise FileExistsError(f"Raw file exists with different content: {raw_path}")
        return
    shutil.copy2(source_path, raw_path)


def resolve_dean_collection(path: Path) -> tuple[str, str]:
    parts = list(path.parts)
    if "topical_studies" in parts:
        return "topical_studies", "topical_studies"
    if "transcripts_books_bible" in parts:
        return "bible_books", "transcripts_books_bible"
    raise ValueError(f"Unable to map Dean source path to collection: {path}")


def resolve_dean_rel_path(path: Path, source_root: str) -> str:
    parts = list(path.parts)
    idx = parts.index(source_root)
    return str(Path(*parts[idx:]))


def resolve_dean_book(path: Path, source_root: str, collection: str) -> tuple[str, str, dict[str, Any]]:
    parts = list(path.parts)
    idx = parts.index(source_root)
    rel_parts = parts[idx + 1 :]
    if collection == "bible_books":
        if len(rel_parts) < 3:
            raise ValueError(f"Expected testament/book/file under {source_root}: {path}")
        testament = rel_parts[0]
        book_slug = rel_parts[1]
        return book_slug, book_slug.replace("_", " "), {"testament": testament}
    if len(rel_parts) < 2:
        raise ValueError(f"Expected topic/file under {source_root}: {path}")
    # Topical trees may group topics by file format first (html/<Topic>/file,
    # pdf/<Topic>/file); the topic folder is the book, not the format folder.
    if len(rel_parts) >= 3 and rel_parts[0].lower() in _TOPICAL_FORMAT_DIRS:
        rel_parts = rel_parts[1:]
    book_slug = rel_parts[0]
    return book_slug, book_slug.replace("_", " "), {}


def load_dean_lesson_overrides() -> dict[str, dict[str, Any]]:
    """Parse DEAN_LESSON_OVERRIDES.

    Values are either a plain lesson number or an object
    {"lesson_number": 14, "basis": "inferred_passage_order", "reason": "..."}.
    Each entry is returned as {"lesson_number": int, "basis": str, "reason": str|None}.
    """
    from src.utils.config import get_config

    try:
        raw = json.loads(get_config().dean_lesson_overrides or "{}")
    except json.JSONDecodeError:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        entry = value if isinstance(value, dict) else {"lesson_number": value}
        try:
            number = int(entry["lesson_number"])
        except (KeyError, TypeError, ValueError):
            continue
        result[str(key)] = {
            "lesson_number": number,
            "basis": str(entry.get("basis") or "manual"),
            "reason": entry.get("reason"),
        }
    return result


def dean_lesson_override(*keys: str) -> dict[str, Any] | None:
    """Override entry from DEAN_LESSON_OVERRIDES for the first matching key, if any."""
    overrides = load_dean_lesson_overrides()
    for key in keys:
        if key in overrides:
            return overrides[key]
    return None


def parse_dean_lesson(stem: str) -> tuple[int | None, str | None]:
    for pattern in (DEAN_YEAR_FILE_RE, DEAN_SIMPLE_FILE_RE, DEAN_SUFFIX_FILE_RE):
        match = pattern.match(stem)
        if not match:
            continue
        code = match.group("code")
        digits = "".join(ch for ch in code if ch.isdigit())
        if digits:
            return int(digits), code
    trailing = DEAN_TRAILING_DIGITS_RE.search(stem)
    if trailing:
        digits = trailing.group("digits")
        return int(digits), stem
    return None, None


def next_dean_lesson_number(session: Session, book_id: int) -> int:
    from src.db.models import DeanLesson

    max_value = session.execute(select(func.max(DeanLesson.lesson_number)).where(DeanLesson.book_id == book_id)).scalar_one_or_none()
    return int(max_value or 0) + 1


def mark_source_file_completed(session: Session, source_file_id: str) -> None:
    session.execute(
        text("UPDATE source_files SET ingestion_status='completed' WHERE id = :id"),
        {"id": source_file_id},
    )
