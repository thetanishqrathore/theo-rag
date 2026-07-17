"""PDF metadata extraction from the first page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Optional

import structlog
from docling.datamodel.document import TextItem
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat

from src.utils.config import get_config

logger = structlog.get_logger()


class MetadataExtractionError(Exception):
    """Raised when required metadata cannot be parsed from a PDF."""


@dataclass
class MetadataResult:
    """Parsed metadata result."""

    series_name: str
    part_number: Optional[int]
    part_title: Optional[str]
    chapter_info: Optional[dict]
    lesson_number: Optional[int]
    lesson_title: Optional[str]
    date: Optional[str]
    location: Optional[str]
    speaker: str
    transcriber: Optional[str]

    def to_dict(self) -> dict:
        """Convert dataclass to dict."""
        return {
            "series_name": self.series_name,
            "part_number": self.part_number,
            "part_title": self.part_title,
            "chapter_info": self.chapter_info,
            "lesson_number": self.lesson_number,
            "lesson_title": self.lesson_title,
            "date": self.date,
            "location": self.location,
            "speaker": self.speaker,
            "transcriber": self.transcriber,
        }


def _parse_date(date_text: str) -> Optional[str]:
    """Parse date text into ISO format (YYYY-MM-DD)."""
    value = " ".join((date_text or "").strip().split())
    if not value:
        return None
    formats = [
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.date().isoformat()
        except ValueError:
            continue
    return None


def _roman_to_int(value: str) -> int | None:
    text = (value or "").strip().upper()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(text):
        if ch not in roman_map:
            return None
        cur = roman_map[ch]
        if cur < prev:
            total -= cur
        else:
            total += cur
        prev = cur
    return total if total > 0 else None


def _clean_series_name(raw: str) -> str:
    value = " ".join((raw or "").strip().split())
    value = re.sub(r"\s+", " ", value)
    return value


def _normalize_series_line(line: str) -> str | None:
    text = " ".join((line or "").strip().split())
    if not text:
        return None

    canonical = re.search(
        r"(?P<base>[A-Za-z][A-Za-z\s]+Series)\s+(?P<y1>(?:19|20)\d{2})\s*[–-]\s*(?P<y2>(?:19|20)\d{2})",
        text,
        re.IGNORECASE,
    )
    if canonical:
        base = _clean_series_name(canonical.group("base"))
        y1 = int(canonical.group("y1"))
        y2 = int(canonical.group("y2"))
        lo, hi = sorted((y1, y2))
        return f"{base} {lo}–{hi}"

    if re.search(r"\bBiblical\s+Framework\s+Series\b", text, re.IGNORECASE):
        years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", text)]
        if len(years) >= 2:
            lo, hi = min(years), max(years)
            return f"Biblical Framework Series {lo}–{hi}"
        if len(years) == 1:
            return f"Biblical Framework Series {years[0]}–{years[0]}"
        return "Biblical Framework Series"

    return None


def _extract_series_name(lines: list[str], joined: str) -> str | None:
    for line in lines:
        series = _normalize_series_line(line)
        if series:
            return series

    combined = re.search(
        r"Biblical\s+Framework\s+Series\s+(?P<y1>(?:19|20)\d{2})\s*[–-]\s*(?P<y2>(?:19|20)\d{2})",
        joined,
        re.IGNORECASE,
    )
    if combined:
        y1 = int(combined.group("y1"))
        y2 = int(combined.group("y2"))
        lo, hi = sorted((y1, y2))
        return f"Biblical Framework Series {lo}–{hi}"

    return None


def _extract_part_and_chapter(lines: list[str]) -> tuple[Optional[int], Optional[str], Optional[dict]]:
    part_number: Optional[int] = None
    part_title: Optional[str] = None
    chapter_info: Optional[dict] = None

    combined_re = re.compile(
        r"Part\s+(?P<part_num>[IVXLCM\d]+)\s*:\s*(?P<part_title>.+?)\s+"
        r"(?:(?:Ch\.?|Chapter)\s*(?P<chap_num>[IVXLCM\d]+)\s*[:\u2013\-]\s*(?P<chap_title>.+)"
        r"|Appendix\s+(?P<app_letter>[A-Z])\s*:\s*(?P<app_title>.+))",
        re.IGNORECASE,
    )
    part_re = re.compile(r"Part\s+(?P<num>[IVXLCM\d]+)\s*:\s*(?P<title>.+)", re.IGNORECASE)
    chapter_re = re.compile(
        r"(?:Ch\.?|Chapter)\s*(?P<num>[IVXLCM\d]+)\s*[:\u2013\-]\s*(?P<title>.+)",
        re.IGNORECASE,
    )
    appendix_re = re.compile(r"\bAppendix\s+(?P<letter>[A-Z])\s*:\s*(?P<title>.+)", re.IGNORECASE)

    for line in lines:
        combined_match = combined_re.search(line)
        if not combined_match:
            continue
        part_number = _roman_to_int(combined_match.group("part_num"))
        part_title = combined_match.group("part_title").strip()
        if combined_match.group("chap_num"):
            chapter_number = _roman_to_int(combined_match.group("chap_num"))
            chapter_title = (combined_match.group("chap_title") or "").strip()
            if chapter_number is not None:
                chapter_info = {
                    "chapter_number": chapter_number,
                    "chapter_title": chapter_title,
                }
        elif combined_match.group("app_letter"):
            letter = combined_match.group("app_letter").upper()
            chapter_info = {
                "chapter_number": None,
                "chapter_title": f"Appendix {letter}: {(combined_match.group('app_title') or '').strip()}",
            }
        break

    if part_number is None:
        for line in lines:
            part_match = part_re.search(line)
            if not part_match:
                continue
            part_number = _roman_to_int(part_match.group("num"))
            raw_title = part_match.group("title").strip()
            raw_title = re.split(
                r"\s+(?:(?:Ch\.?|Chapter)\s+[IVXLCM\d]+\s*[:\u2013\-]|Appendix\s+[A-Z]\s*:|Lesson\s+\d+\s*[\u2013\-:])",
                raw_title,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()
            part_title = raw_title
            if part_number is not None:
                break

    if chapter_info is None:
        for line in lines:
            chapter_match = chapter_re.search(line)
            if chapter_match:
                chapter_number = _roman_to_int(chapter_match.group("num"))
                if chapter_number is not None:
                    chapter_info = {
                        "chapter_number": chapter_number,
                        "chapter_title": chapter_match.group("title").strip(),
                    }
                    break

            appendix_match = appendix_re.search(line)
            if appendix_match:
                letter = appendix_match.group("letter").upper()
                chapter_info = {
                    "chapter_number": None,
                    "chapter_title": f"Appendix {letter}: {appendix_match.group('title').strip()}",
                }
                break

    return part_number, part_title, chapter_info


def _extract_lesson(lines: list[str]) -> tuple[Optional[int], Optional[str]]:
    lesson_re = re.compile(r"Lesson\s+(?P<num>\d+)\s*[\u2013\-:]\s*(?P<title>.+)", re.IGNORECASE)
    intro_part_re = re.compile(r"\b21st\s+Century\s*-\s*Part\s*(?P<num>\d+)\b", re.IGNORECASE)

    for line in lines:
        lesson_match = lesson_re.search(line)
        if lesson_match:
            return int(lesson_match.group("num")), lesson_match.group("title").strip()

    for line in lines:
        intro_match = intro_part_re.search(line)
        if intro_match:
            return int(intro_match.group("num")), line.strip()

    return None, None


def _extract_date(lines: list[str]) -> tuple[Optional[str], Optional[int]]:
    date_re = re.compile(
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})",
        re.IGNORECASE,
    )

    for idx, line in enumerate(lines):
        date_match = date_re.search(line)
        if not date_match:
            continue
        parsed = _parse_date(date_match.group(1))
        if parsed:
            return parsed, idx

    return None, None


def _extract_location(lines: list[str], date_line_index: int | None) -> Optional[str]:
    location_pattern = re.compile(r".+,\s*[A-Z]{2}(?:\s*\d{5})?$")
    if date_line_index is not None and date_line_index + 1 < len(lines):
        candidate = lines[date_line_index + 1].strip()
        if location_pattern.match(candidate):
            return candidate

    for line in lines:
        if location_pattern.match(line):
            return line
    return None


def _extract_speaker(lines: list[str], joined: str) -> Optional[str]:
    # Strong deterministic fast-path for this corpus.
    for line in lines[:24]:
        if "clough" not in line.lower():
            continue
        cleaned = re.sub(r"^©\s*", "", line).strip()
        cleaned = re.sub(r"\b(?:19|20)\d{2}\b", "", cleaned).strip()
        cleaned = " ".join(cleaned.split())
        name_match = re.search(r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)", cleaned)
        if name_match:
            return name_match.group(1).replace("  ", " ").strip()

    speaker_pattern = re.compile(r"(?:©\s*)?([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+)+)")
    disqualifiers = ["series", "part", "lesson", "ch.", "chapter", "church", "chapel", "transcribed"]
    for line in lines:
        speaker_match = speaker_pattern.search(line)
        if not speaker_match:
            continue
        lowered = line.lower()
        if any(term in lowered for term in disqualifiers):
            continue
        candidate = " ".join(speaker_match.group(1).split())
        if len(candidate.split()) >= 2:
            return candidate

    joined_match = re.search(r"(?:©\s*)?(Charles\s+A\.?\s+Clough)", joined, re.IGNORECASE)
    if joined_match:
        return "Charles A. Clough"

    return None


def _extract_transcriber(joined: str) -> Optional[str]:
    transcriber_match = re.search(r"Transcribed\s+by\s+([A-Za-z\s.]+)", joined, re.IGNORECASE)
    if not transcriber_match:
        return None
    return " ".join(transcriber_match.group(1).split()).strip()


def extract_metadata_from_pdf(pdf_path: Path) -> dict:
    """
    Parse metadata from PDF first page.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Dictionary containing parsed metadata keys.

    Raises:
        FileNotFoundError: If the PDF path does not exist.
        MetadataExtractionError: If required fields are missing.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(pdf_path)
    doc = result.document

    text_items: list[str] = []
    for item, _level in doc.iterate_items():
        if isinstance(item, TextItem) and item.text.strip():
            text_items.append(item.text.strip())
        if len(text_items) >= 64:
            break

    lines = [" ".join(line.split()) for line in text_items if line.strip()]
    joined = "\n".join(lines)

    series_name = _extract_series_name(lines, joined)
    part_number, part_title, chapter_info = _extract_part_and_chapter(lines)
    lesson_number, lesson_title = _extract_lesson(lines)
    date_iso, date_line_index = _extract_date(lines)
    location = _extract_location(lines, date_line_index)
    speaker = _extract_speaker(lines, joined)
    transcriber = _extract_transcriber(joined)

    config = get_config()
    strict_mode = bool(config.transcript_metadata_strict_mode)
    if not series_name and not strict_mode:
        series_name = "Biblical Framework Series"

    if not series_name or not speaker:
        log = logger.bind(filename=pdf_path.name)
        log.error("metadata_required_fields_missing", series_name=series_name, speaker=speaker)
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
