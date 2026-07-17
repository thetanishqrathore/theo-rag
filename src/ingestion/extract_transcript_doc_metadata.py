"""DOC/DOCX transcript metadata extraction for Clough transcripts."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import structlog

from src.ingestion.extract_metadata import (
    MetadataExtractionError,
    MetadataResult,
    _extract_date,
    _extract_lesson,
    _extract_location,
    _extract_part_and_chapter,
    _extract_series_name,
    _extract_speaker,
    _extract_transcriber,
)
from src.utils.config import get_config

logger = structlog.get_logger()

_DOC_HYPERLINK_RE = re.compile(r'HYPERLINK\s+"[^"]+"\s*', re.IGNORECASE)
_DOC_CONTROL_CHAR_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_REPEATED_WS_RE = re.compile(r"\s+")


def _normalize_doc_line(raw: str) -> str:
    text = _DOC_HYPERLINK_RE.sub("", raw or "")
    text = _DOC_CONTROL_CHAR_RE.sub("", text)
    text = text.replace("\xa0", " ")
    text = _REPEATED_WS_RE.sub(" ", text).strip()
    return text


def _find_txt_output(temp_dir: Path) -> Path | None:
    files = sorted(temp_dir.rglob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return None
    return files[0]


def _convert_doc_to_text(source_path: Path) -> str:
    command = get_config().transcript_doc_soffice_command
    binary = shutil.which(command)
    if not binary:
        raise MetadataExtractionError(f"LibreOffice command not found: {command}")

    with tempfile.TemporaryDirectory(prefix="transcript_doc_meta_") as temp_str:
        temp_dir = Path(temp_str)
        attempts = ("txt:Text", "txt")
        last_error = ""
        for target in attempts:
            try:
                subprocess.run(
                    [binary, "--headless", "--convert-to", target, "--outdir", str(temp_dir), str(source_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=max(30, int(get_config().transcript_doc_soffice_timeout_sec)),
                )
                txt_path = _find_txt_output(temp_dir)
                if txt_path is None:
                    raise MetadataExtractionError("DOC conversion succeeded but no TXT output was produced")
                return txt_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as exc:  # pragma: no cover - runtime integration guard
                last_error = str(exc)
        raise MetadataExtractionError(f"DOC metadata text conversion failed: {last_error}")


def _prepare_lines(text: str) -> list[str]:
    deduped: list[str] = []
    seen_www = False
    for raw in text.splitlines():
        line = _normalize_doc_line(raw)
        if not line:
            continue
        low = line.lower()
        if "www.bibleframework.org" in low:
            if seen_www:
                continue
            seen_www = True
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)
    return deduped[:120]


def extract_metadata_from_transcript_doc(doc_path: Path) -> dict:
    """Parse metadata from transcript DOC/DOCX first page text region."""
    if not doc_path.exists():
        raise FileNotFoundError(f"DOC not found: {doc_path}")

    raw_text = _convert_doc_to_text(doc_path)
    lines = _prepare_lines(raw_text)
    joined = "\n".join(lines)

    series_name = _extract_series_name(lines, joined)
    part_number, part_title, chapter_info = _extract_part_and_chapter(lines)
    lesson_number, lesson_title = _extract_lesson(lines)
    date_iso, date_line_index = _extract_date(lines)
    location = _extract_location(lines, date_line_index)
    speaker = _extract_speaker(lines, joined)
    transcriber = _extract_transcriber(joined)

    config = get_config()
    if not series_name and not bool(config.transcript_metadata_strict_mode):
        series_name = "Biblical Framework Series"

    if not series_name or not speaker:
        logger.bind(filename=doc_path.name).error(
            "doc_metadata_required_fields_missing",
            series_name=series_name,
            speaker=speaker,
        )
        raise MetadataExtractionError("Missing required fields: series_name or speaker")

    return MetadataResult(
        series_name=series_name,
        part_number=part_number,
        part_title=part_title,
        chapter_info=chapter_info,
        lesson_number=lesson_number,
        lesson_title=lesson_title,
        date=date_iso,
        location=location,
        speaker=speaker,
        transcriber=transcriber,
    ).to_dict()

