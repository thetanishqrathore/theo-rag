"""Document extraction pipeline with multi-engine quality selection."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, List

import fitz
import structlog
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import TextItem
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import DeanLesson, TranscriptLesson, TranscriptParagraph, SourceFile, FileTag
from src.processing.structured_nodes import (
    StructuredNodeInput,
    StructuredTableInput,
    derive_paragraph_rows_from_nodes,
    is_dean_node_lesson,
    persist_dean_structured_doc,
    persist_dean_structured_nodes,
)
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()


class ExtractionError(Exception):
    """Raised when extraction fails for a lesson."""


@dataclass
class RawBlock:
    text: str
    kind: str
    depth_hint: int
    markdown: str | None = None
    list_kind: str | None = None


@dataclass
class EngineCandidate:
    engine: str
    blocks: list[RawBlock]
    score: float
    metrics: dict[str, Any]
    ocr_used: bool
    error: str | None = None


@dataclass
class HierarchyState:
    current_path: list[int]
    next_at_depth: dict[int, int]


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_PAGE_MARKER_RE = re.compile(r"\bPage\s+\d+(?:\s+of\s+\d+)?\b", re.IGNORECASE)
_REPEATED_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"\b(?:www\.)?[a-z0-9\-]+\.[a-z]{2,}(?:/[^\s]*)?\b", re.IGNORECASE)
_ROMAN_RE = re.compile(r"^[IVXLCM]{1,8}[.)]\s+(.+)$")
_NUMERIC_RE = re.compile(r"^\d{1,3}[.)]\s+(.+)$")
_ALPHA_RE = re.compile(r"^[A-Z][.)]\s+(.+)$")
_LOWER_ALPHA_RE = re.compile(r"^[a-z][.)]\s+(.+)$")
_BULLET_RE = re.compile(r"^[-*•]\s+(.+)$")
_NB_RE = re.compile(r"^N\.B\.\s*(.+)$", re.IGNORECASE)
_ALL_CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9\s:,\-()'&]{8,}$")
_TRANSCRIPT_CHAPTER_HEADING_RE = re.compile(
    r"^(?:chapter|ch\.?)\s+[ivxlcm\d]+\s*[:\u2013\-]\s*.+$",
    re.IGNORECASE,
)
_TRANSCRIPT_LESSON_HEADING_RE = re.compile(
    r"^lesson\s+\d+\s*[\u2013\-:]\s*.+$",
    re.IGNORECASE,
)
_TRANSCRIPT_APPENDIX_HEADING_RE = re.compile(
    r"^(?:appendix\s+[a-z]|conclusion|part\s+\d+\s+review\s+lessons)\b.*$",
    re.IGNORECASE,
)
_TRANSCRIPT_TITLE_LINE_RE = re.compile(
    r"^a\s+biblical\s+framework\s+for\s+.+\s+-\s+part\s+\d+$",
    re.IGNORECASE,
)
_TRANSCRIPT_DATE_RE = re.compile(
    r"^(?:\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})$",
    re.IGNORECASE,
)
_TRANSCRIPT_LOCATION_RE = re.compile(r".+,\s*[A-Z]{2}(?:\s+\d{5})?$")
_TRANSCRIPT_NAME_LINE_RE = re.compile(r"^(?:charles\s+a\.?\s+clough|charlie\s+clough)$", re.IGNORECASE)
_TRANSCRIPT_TRANSCRIBER_RE = re.compile(r"^transcribed\s+by\s+.+$", re.IGNORECASE)
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_MD_BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_TABLE_SEPARATOR_RE = re.compile(r"^\|?(?:\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$")
_DOC_HYPERLINK_RE = re.compile(r'HYPERLINK\s+"[^"]+"\s*', re.IGNORECASE)
_DOC_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_DOC_P_CLASS_RULE_RE = re.compile(r"p\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}", re.IGNORECASE)
_DOC_MARGIN_LEFT_RE = re.compile(r"margin-left:\s*([-\d.]+)\s*(px|in)", re.IGNORECASE)
_DOC_TEXT_INDENT_RE = re.compile(r"text-indent:\s*([-\d.]+)\s*(px|in)", re.IGNORECASE)
_DOC_ARTIFACT_LINE_RE = re.compile(r"^(?:HYPERLINK|file://)", re.IGNORECASE)
_COPYRIGHT_RE = re.compile(r"(?:^|\s)(?:©|copyright\b)", re.IGNORECASE)
_DOC_SOURCE_FORMATS = {"doc", "docx", "rtf"}


def _create_docling_converter() -> DocumentConverter:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def extract_bible_references(text: str) -> List[str]:
    pattern = (
        r"\b([1-3]?\s?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(\d+):(\d+)(?:-(\d+))?"
    )
    matches = re.findall(pattern, text)
    references: list[str] = []
    for book, chapter, verse_start, verse_end in matches:
        book_clean = " ".join(book.split())
        if verse_end:
            references.append(f"{book_clean} {chapter}:{verse_start}-{verse_end}")
        else:
            references.append(f"{book_clean} {chapter}:{verse_start}")
    return references


def _is_probable_junk_line(text: str) -> bool:
    cleaned = _normalize_line(text)
    if not cleaned:
        return True
    words = cleaned.split()
    if len(words) <= 30 and _COPYRIGHT_RE.search(cleaned):
        return True
    if len(words) <= 20 and cleaned.lower().startswith(("all rights reserved", "www.")):
        return True
    return False


def _normalize_line(text: str) -> str:
    value = text.strip()
    value = _PAGE_MARKER_RE.sub("", value)
    value = re.sub(r"(\w)-\s+(\w)", r"\1\2", value)
    value = _REPEATED_WS_RE.sub(" ", value).strip()
    return value


def _strip_markdown_controls(text: str, strict: bool = True) -> str:
    value = text.strip()
    if not value:
        return value

    value = _MD_LINK_RE.sub(r"\1", value)
    value = _MD_CODE_RE.sub(r"\1", value)
    value = re.sub(r"^#{1,6}\s+", "", value)
    value = re.sub(r"^\s*>\s*", "", value)
    value = re.sub(r"^\s*[-*+]\s+", "", value)
    value = re.sub(r"^\s*\d{1,3}[.)]\s+", "", value)
    value = _MD_BOLD_RE.sub(r"\2", value)
    value = _MD_ITALIC_RE.sub(lambda m: (m.group(1) or m.group(2) or "").strip(), value)
    if strict:
        value = value.replace("**", "").replace("__", "").replace("*", "").replace("`", "")
        value = value.replace("|", " ")
    value = _REPEATED_WS_RE.sub(" ", value).strip()
    return value


def _extract_markdown_heading(text: str) -> tuple[int, str] | None:
    match = _MD_HEADING_RE.match(text)
    if not match:
        return None
    level = len(match.group(1))
    content = match.group(2).strip()
    return max(1, min(level, 6)), content


def _line_kind(text: str) -> tuple[str, int, str | None]:
    md_heading = _extract_markdown_heading(text)
    if md_heading:
        level = md_heading[0]
        return "heading", max(1, min(level, 4)), None
    if _MD_TABLE_SEPARATOR_RE.match(text):
        return "table_sep", 2, None
    if "|" in text and text.count("|") >= 2:
        return "table_row", 2, None
    if _NB_RE.match(text):
        return "note", 3, None
    if _TRANSCRIPT_CHAPTER_HEADING_RE.match(text):
        return "heading", 1, None
    if _TRANSCRIPT_LESSON_HEADING_RE.match(text):
        return "heading", 2, None
    if _TRANSCRIPT_APPENDIX_HEADING_RE.match(text):
        return "heading", 2, None
    if _TRANSCRIPT_TITLE_LINE_RE.match(text):
        return "heading", 2, None
    if _ROMAN_RE.match(text):
        return "heading", 1, None
    if _NUMERIC_RE.match(text):
        return "list_item", 2, "ol"
    if _ALPHA_RE.match(text):
        return "heading", 2, None
    if _LOWER_ALPHA_RE.match(text):
        return "list_item", 3, "alpha"
    if _BULLET_RE.match(text):
        return "list_item", 3, "ul"
    if text.endswith(":") and len(text.split()) <= 14:
        return "heading", 2, None
    if _ALL_CAPS_HEADING_RE.match(text):
        return "heading", 1, None
    if text.startswith(">"):
        return "blockquote", 2, None
    return "paragraph", 2, None


def _clean_doc_text(text: str) -> str:
    value = unescape(text or "")
    value = value.replace("\xa0", " ")
    value = _DOC_HYPERLINK_RE.sub("", value)
    value = value.replace("\u0000", " ")
    return _normalize_line(value)


def _indent_to_depth(indent_px: float) -> int:
    if indent_px >= 96:
        return 4
    if indent_px >= 64:
        return 3
    if indent_px >= 28:
        return 2
    return 1


def _css_length_to_px(value: float, unit: str) -> float:
    if unit.lower() == "in":
        return value * 96.0
    return value


def _parse_doc_paragraph_style_depths(html_text: str) -> dict[str, int]:
    depths: dict[str, int] = {}
    for style_match in _DOC_STYLE_BLOCK_RE.finditer(html_text):
        style_block = style_match.group(1) or ""
        for rule_match in _DOC_P_CLASS_RULE_RE.finditer(style_block):
            cls = rule_match.group(1)
            body = rule_match.group(2) or ""
            margin_match = _DOC_MARGIN_LEFT_RE.search(body)
            indent_match = _DOC_TEXT_INDENT_RE.search(body)
            margin = (
                _css_length_to_px(float(margin_match.group(1)), margin_match.group(2))
                if margin_match
                else 0.0
            )
            indent = (
                _css_length_to_px(float(indent_match.group(1)), indent_match.group(2))
                if indent_match
                else 0.0
            )
            depths[cls] = _indent_to_depth(max(0.0, margin + max(indent, 0.0)))
    return depths


def _ol_list_kind(attrs: dict[str, str]) -> str:
    marker = (attrs.get("type") or "").strip().lower()
    if marker in {"a"}:
        return "alpha"
    if marker in {"i"}:
        return "roman"
    return "ol"


class _DocHtmlBlockParser(HTMLParser):
    """Extract semantic text blocks from converted DOC HTML."""

    def __init__(self, paragraph_depths: dict[str, int]):
        super().__init__(convert_charrefs=True)
        self.paragraph_depths = paragraph_depths
        self.blocks: list[RawBlock] = []
        self.list_stack: list[str] = []
        self.current_tag: str | None = None
        self.current_depth = 2
        self.current_list_kind: str | None = None
        self.current_data: list[str] = []
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): (val or "") for key, val in attrs}
        tag = tag.lower()
        if tag == "ol":
            self.list_stack.append(_ol_list_kind(attr_map))
            return
        if tag == "ul":
            self.list_stack.append("ul")
            return
        if tag == "br":
            if self.in_cell:
                self.current_cell.append(" ")
            elif self.current_tag is not None:
                self.current_data.append(" ")
            return
        if tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []
            return
        if tag == "tr":
            self.current_row = []
            return
        if tag not in {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
            return

        self.current_tag = tag
        self.current_data = []
        if tag.startswith("h"):
            level = int(tag[1]) if tag[1:].isdigit() else 2
            self.current_depth = max(1, min(level, 4))
            self.current_list_kind = None
            return

        if tag == "li":
            self.current_depth = max(2, min(4, len(self.list_stack) + 1))
            self.current_list_kind = self.list_stack[-1] if self.list_stack else "ul"
            return

        cls_name = (attr_map.get("class") or "").split()[0] if attr_map.get("class") else ""
        self.current_depth = self.paragraph_depths.get(cls_name, 2)
        self.current_list_kind = None

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self.in_cell = False
            text = _clean_doc_text("".join(self.current_cell))
            self.current_cell = []
            if text:
                self.current_row.append(text)
            return
        if tag == "tr":
            if not self.current_row:
                return
            joined = " | ".join(self.current_row).strip()
            if joined:
                self.blocks.append(RawBlock(text=joined, kind="table_row", depth_hint=2, markdown=joined))
            self.current_row = []
            return
        if tag in {"ol", "ul"}:
            if self.list_stack:
                self.list_stack.pop()
            return
        if self.current_tag != tag:
            return

        raw_text = "".join(self.current_data)
        self.current_data = []
        self.current_tag = None
        text = _clean_doc_text(raw_text)
        if not text or _DOC_ARTIFACT_LINE_RE.match(text):
            return

        if tag.startswith("h"):
            self.blocks.append(RawBlock(text=text, kind="heading", depth_hint=self.current_depth, markdown=raw_text))
            return
        if tag == "li":
            list_kind = self.current_list_kind or (self.list_stack[-1] if self.list_stack else "ul")
            self.blocks.append(
                RawBlock(
                    text=text,
                    kind="list_item",
                    depth_hint=self.current_depth,
                    markdown=raw_text,
                    list_kind=list_kind,
                )
            )
            return
        if tag == "blockquote":
            self.blocks.append(RawBlock(text=text, kind="blockquote", depth_hint=max(2, self.current_depth), markdown=raw_text))
            return

        inferred_kind, inferred_depth, inferred_list_kind = _line_kind(text)
        self.blocks.append(
            RawBlock(
                text=text,
                kind=inferred_kind,
                depth_hint=max(self.current_depth, inferred_depth),
                markdown=raw_text,
                list_kind=inferred_list_kind,
            )
        )

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)
            return
        if self.current_tag is not None:
            self.current_data.append(data)


def _split_long_text(text: str, max_words: int = 900) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if len(parts) <= 1:
        return [
            " ".join(words[idx : idx + max_words]).strip()
            for idx in range(0, len(words), max_words)
            if " ".join(words[idx : idx + max_words]).strip()
        ]
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in parts:
        sent_words = len(sentence.split())
        if current and current_words + sent_words > max_words:
            chunks.append(" ".join(current).strip())
            current = [sentence]
            current_words = sent_words
            continue
        current.append(sentence)
        current_words += sent_words
    if current:
        chunks.append(" ".join(current).strip())
    return chunks


def _suppress_repeated_lines(lines: list[str]) -> list[str]:
    config = get_config()
    threshold = max(2, int(config.dean_pdf_header_footer_repeat_threshold))
    counts: dict[str, int] = {}
    for line in lines:
        key = line.lower()
        counts[key] = counts.get(key, 0) + 1
    repeated = {
        key for key, count in counts.items()
        if count >= threshold and len(key.split()) <= 20
    }
    kept: list[str] = []
    for line in lines:
        key = line.lower()
        if key in repeated:
            continue
        if _URL_RE.search(line) and len(line.split()) <= 8:
            continue
        if _PAGE_MARKER_RE.search(line):
            continue
        kept.append(line)
    return kept


def _lines_to_blocks(lines: list[str]) -> list[RawBlock]:
    blocks: list[RawBlock] = []
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_buffer:
            return
        text = _normalize_line(" ".join(paragraph_buffer))
        paragraph_buffer.clear()
        if not text:
            return
        blocks.append(RawBlock(text=text, kind="paragraph", depth_hint=2, markdown=text))

    for raw in lines:
        line = _normalize_line(raw)
        if not line:
            flush_paragraph()
            continue
        kind, depth, list_kind = _line_kind(line)
        if kind == "table_sep":
            continue
        if kind in {"heading", "list_item", "blockquote", "note", "table_row"}:
            flush_paragraph()
            blocks.append(RawBlock(text=line, kind=kind, depth_hint=depth, markdown=raw, list_kind=list_kind))
            continue
        paragraph_buffer.append(line)

    flush_paragraph()
    return blocks


def _extract_blocks_with_pymupdf(pdf_path: Path) -> tuple[list[RawBlock], bool, float]:
    lines: list[str] = []
    total_chars = 0
    page_count = 0
    with fitz.open(pdf_path) as doc:
        page_count = max(doc.page_count, 1)
        for page in doc:
            text = page.get_text("text")
            total_chars += len(text or "")
            lines.extend(text.splitlines())
    normalized = [_normalize_line(line) for line in lines]
    normalized = [line for line in normalized if line]
    normalized = _suppress_repeated_lines(normalized)
    chars_per_page = total_chars / page_count
    target_chars = float(get_config().dean_pdf_scan_chars_per_page_target)
    scan_confidence = max(0.0, min(1.0, 1.0 - (chars_per_page / max(target_chars, 1.0))))
    return _lines_to_blocks(normalized), False, round(scan_confidence, 4)


def _extract_blocks_with_docling(pdf_path: Path) -> tuple[list[RawBlock], bool]:
    converter = _create_docling_converter()
    result = converter.convert(pdf_path)
    doc = result.document
    lines: list[str] = []
    for item, _level in doc.iterate_items():
        if not isinstance(item, TextItem):
            continue
        label = str(getattr(item, "label", "")).upper()
        if "PAGE_HEADER" in label or "PAGE_FOOTER" in label:
            continue
        text = _normalize_line(item.text or "")
        if not text:
            continue
        lines.append(text)
    lines = _suppress_repeated_lines(lines)
    return _lines_to_blocks(lines), True


def _read_marker_output(temp_dir: Path) -> str | None:
    preferred = sorted(temp_dir.rglob("*.md"))
    if preferred:
        return preferred[0].read_text(encoding="utf-8", errors="ignore")
    html_files = sorted(temp_dir.rglob("*.html"))
    if html_files:
        html_text = html_files[0].read_text(encoding="utf-8", errors="ignore")
        stripped = re.sub(r"<[^>]+>", " ", html_text)
        return _REPEATED_WS_RE.sub(" ", stripped)
    txt_files = sorted(temp_dir.rglob("*.txt"))
    if txt_files:
        return txt_files[0].read_text(encoding="utf-8", errors="ignore")
    return None


def _extract_blocks_with_marker(pdf_path: Path) -> tuple[list[RawBlock], bool]:
    config = get_config()
    command = config.dean_pdf_marker_command
    binary = shutil.which(command)
    if not binary:
        raise ExtractionError(f"Marker command not found: {command}")

    with tempfile.TemporaryDirectory(prefix="marker_extract_") as temp_str:
        temp_dir = Path(temp_str)
        attempts = [
            # Newer marker-pdf style: explicit output directory flag.
            [binary, str(pdf_path), "--output_dir", str(temp_dir), "--output_format", "markdown"],
            [binary, str(pdf_path), "--output_dir", str(temp_dir)],
            # Alternate flag name seen in some variants.
            [binary, str(pdf_path), "--output-dir", str(temp_dir), "--output_format", "markdown"],
            [binary, str(pdf_path), "--output-dir", str(temp_dir)],
            # Legacy positional output directory fallback.
            [binary, str(pdf_path), str(temp_dir), "--output_format", "markdown"],
            [binary, str(pdf_path), str(temp_dir)],
        ]
        last_error = ""
        for cmd in attempts:
            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=max(30, int(config.dean_pdf_marker_timeout_sec)),
                )
                output = _read_marker_output(temp_dir)
                if not output:
                    raise ExtractionError("Marker finished but no output file found")
                lines = [_normalize_line(line) for line in output.splitlines()]
                lines = [line for line in lines if line]
                lines = _suppress_repeated_lines(lines)
                return _lines_to_blocks(lines), True
            except subprocess.CalledProcessError as exc:  # pragma: no cover - runtime integration guard
                stderr = (exc.stderr or "").strip()
                stdout = (exc.stdout or "").strip()
                detail = stderr or stdout or str(exc)
                last_error = f"cmd={' '.join(cmd)} :: {detail}"
                continue
            except Exception as exc:  # pragma: no cover - runtime integration guard
                last_error = f"cmd={' '.join(cmd)} :: {exc}"
                continue
    raise ExtractionError(f"Marker extraction failed: {last_error}")


def _find_single_converted_file(temp_dir: Path, extensions: tuple[str, ...]) -> Path | None:
    candidates: list[Path] = []
    for ext in extensions:
        candidates.extend(temp_dir.rglob(f"*{ext}"))
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def _run_soffice_convert(source_path: Path, target: str, output_dir: Path, timeout_sec: int) -> None:
    command = get_config().dean_doc_soffice_command
    binary = shutil.which(command)
    if not binary:
        raise ExtractionError(f"LibreOffice command not found: {command}")
    # Run in isolated temp output dir to avoid collisions in batch runs.
    subprocess.run(
        [binary, "--headless", "--convert-to", target, "--outdir", str(output_dir), str(source_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=max(30, timeout_sec),
    )


def _extract_blocks_with_soffice_html(doc_path: Path) -> tuple[list[RawBlock], bool]:
    config = get_config()
    with tempfile.TemporaryDirectory(prefix="doc_extract_html_") as temp_str:
        temp_dir = Path(temp_str)
        attempts = ["html", "html:XHTML Writer File:UTF8", "xhtml"]
        last_error = ""
        for target in attempts:
            try:
                _run_soffice_convert(doc_path, target, temp_dir, timeout_sec=int(config.dean_doc_soffice_timeout_sec))
                html_path = _find_single_converted_file(temp_dir, (".html", ".htm", ".xhtml"))
                if not html_path:
                    raise ExtractionError("DOC conversion finished but no HTML output was produced")
                html_text = html_path.read_text(encoding="utf-8", errors="ignore")
                parser = _DocHtmlBlockParser(_parse_doc_paragraph_style_depths(html_text))
                parser.feed(html_text)
                blocks = [block for block in parser.blocks if _clean_doc_text(block.text)]
                if blocks:
                    return blocks, False
                raise ExtractionError("Converted HTML did not produce usable semantic blocks")
            except Exception as exc:  # pragma: no cover - runtime integration guard
                last_error = f"target={target} :: {exc}"
                continue
    raise ExtractionError(f"DOC HTML extraction failed: {last_error}")


def _extract_blocks_with_soffice_text(doc_path: Path) -> tuple[list[RawBlock], bool]:
    config = get_config()
    with tempfile.TemporaryDirectory(prefix="doc_extract_txt_") as temp_str:
        temp_dir = Path(temp_str)
        attempts = ["txt:Text", "txt"]
        last_error = ""
        for target in attempts:
            try:
                _run_soffice_convert(doc_path, target, temp_dir, timeout_sec=int(config.dean_doc_soffice_timeout_sec))
                txt_path = _find_single_converted_file(temp_dir, (".txt",))
                if not txt_path:
                    raise ExtractionError("DOC conversion finished but no TXT output was produced")
                text = txt_path.read_text(encoding="utf-8", errors="ignore")
                lines = [_clean_doc_text(line) for line in text.splitlines()]
                lines = [line for line in lines if line and not _DOC_ARTIFACT_LINE_RE.match(line)]
                lines = _suppress_repeated_lines(lines)
                blocks = _lines_to_blocks(lines)
                if blocks:
                    return blocks, False
                raise ExtractionError("Converted TXT did not produce usable blocks")
            except Exception as exc:  # pragma: no cover - runtime integration guard
                last_error = f"target={target} :: {exc}"
                continue
    raise ExtractionError(f"DOC text extraction failed: {last_error}")


def _detect_doc_profile(blocks: list[RawBlock]) -> str:
    if not blocks:
        return "doctrinal_outline_doc"
    total_words = sum(len(block.text.split()) for block in blocks)
    list_items = sum(1 for block in blocks if block.kind == "list_item")
    headings = sum(1 for block in blocks if block.kind == "heading")
    avg_words = total_words / max(len(blocks), 1)
    if total_words >= 9000 and list_items <= max(8, len(blocks) // 10) and avg_words > 20:
        return "lecture_transcript_doc"
    if list_items >= 8 or headings >= 4:
        return "doctrinal_outline_doc"
    return "doctrinal_outline_doc"


def _profile_for_lesson(lesson: TranscriptLesson, session: Session) -> str:
    if not lesson.dean_lesson_id:
        source = session.execute(
            select(FileTag.name, SourceFile.source_format)
            .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
            .where(SourceFile.id == lesson.source_file_id)
        ).first()
        source_tag = source[0] if source else None
        source_format = str(source[1] or "").lower() if source else ""
        if source_tag == "transcripts":
            if source_format in _DOC_SOURCE_FORMATS:
                return get_config().transcript_validation_profile or "clough_transcripts_doc_v3"
            return "clough_transcripts_pdf_v2"
        return "default"
    dean_lesson = session.execute(
        select(DeanLesson).where(DeanLesson.id == lesson.dean_lesson_id)
    ).scalar_one_or_none()
    if not dean_lesson or not dean_lesson.metadata_json:
        return "default"
    return str(dean_lesson.metadata_json.get("parser_profile") or "default")


def _profile_block_target(profile: str) -> tuple[int, int]:
    if profile == "clough_transcripts_doc_v3":
        return 70, 220
    if profile == "clough_transcripts_pdf_v2":
        return 95, 280
    if profile == "dbm_slides_pdf":
        return 60, 180
    if profile == "legacy_html_export_pdf":
        return 90, 240
    if profile == "chafer_lesson_pdf":
        return 120, 280
    if profile == "article_pdf":
        return 120, 320
    if profile == "doctrinal_outline_doc":
        return 80, 260
    if profile == "lecture_transcript_doc":
        return 110, 320
    return 120, 260


def _score_candidate(candidate: EngineCandidate, profile: str) -> tuple[float, dict[str, Any]]:
    blocks = candidate.blocks
    if not blocks:
        return 0.0, {
            "heading_continuity": 0.0,
            "list_nesting_integrity": 0.0,
            "duplicate_header_leakage": 0.0,
            "paragraph_distribution": 0.0,
            "section_transition_coherence": 0.0,
        }

    total = len(blocks)
    headings = [b for b in blocks if b.kind == "heading"]
    lists = [b for b in blocks if b.kind == "list_item"]
    heading_continuity = min(1.0, len(headings) / max(1.0, total / 12.0))

    unique_lines = len({b.text.strip().lower() for b in blocks if b.text.strip()})
    duplicate_ratio = max(0.0, 1.0 - (unique_lines / max(total, 1)))
    page_leaks = sum(1 for b in blocks if _PAGE_MARKER_RE.search(b.text))
    leakage = min(1.0, duplicate_ratio + (page_leaks / max(total, 1)))
    duplicate_header_leakage = max(0.0, 1.0 - leakage)

    min_target, max_target = _profile_block_target(profile)
    paragraph_like = [b for b in blocks if b.kind in {"paragraph", "blockquote", "note"}]
    in_range = 0
    for block in paragraph_like:
        words = len(block.text.split())
        if min_target <= words <= max_target:
            in_range += 1
    paragraph_distribution = in_range / max(len(paragraph_like), 1)

    if lists:
        depth_span = max(b.depth_hint for b in lists) - min(b.depth_hint for b in lists)
        list_nesting_integrity = min(1.0, 0.5 + 0.25 * depth_span)
    else:
        list_nesting_integrity = 0.65

    heading_positions = [idx for idx, block in enumerate(blocks) if block.kind == "heading"]
    if len(heading_positions) <= 1:
        section_transition_coherence = 0.7
    else:
        gaps = [b - a for a, b in zip(heading_positions, heading_positions[1:])]
        avg_gap = sum(gaps) / len(gaps)
        variance = sum((gap - avg_gap) ** 2 for gap in gaps) / len(gaps)
        section_transition_coherence = max(0.0, min(1.0, 1.0 - (variance / max(avg_gap * avg_gap, 1.0))))

    metrics = {
        "heading_continuity": round(heading_continuity, 4),
        "list_nesting_integrity": round(list_nesting_integrity, 4),
        "duplicate_header_leakage": round(duplicate_header_leakage, 4),
        "paragraph_distribution": round(paragraph_distribution, 4),
        "section_transition_coherence": round(section_transition_coherence, 4),
    }
    score = (
        0.28 * heading_continuity
        + 0.2 * list_nesting_integrity
        + 0.2 * duplicate_header_leakage
        + 0.17 * paragraph_distribution
        + 0.15 * section_transition_coherence
    )
    return round(score, 4), metrics


def _path_to_string(path: list[int]) -> str:
    return ".".join(str(v) for v in path if v > 0) or "1"


def _advance_path_for_depth(state: HierarchyState, depth: int) -> list[int]:
    safe_depth = max(1, min(depth, 4))
    parent = state.current_path[: safe_depth - 1]
    next_value = state.next_at_depth.get(safe_depth, 1)
    new_path = parent + [next_value]
    state.next_at_depth[safe_depth] = next_value + 1
    for deeper in list(state.next_at_depth.keys()):
        if deeper > safe_depth:
            state.next_at_depth[deeper] = 1
    state.current_path = new_path
    return new_path


def _clean_marker_prefix(text: str) -> str:
    text = _clean_doc_text(text)
    heading_match = _extract_markdown_heading(text)
    if heading_match:
        return _strip_markdown_controls(heading_match[1], strict=get_config().dean_topical_markdown_strip_strict)
    for regex in (_ROMAN_RE, _NUMERIC_RE, _ALPHA_RE, _LOWER_ALPHA_RE, _BULLET_RE, _NB_RE):
        match = regex.match(text)
        if match:
            cleaned = match.group(1).strip()
            return _strip_markdown_controls(cleaned, strict=get_config().dean_topical_markdown_strip_strict)
    return _strip_markdown_controls(text.strip(), strict=get_config().dean_topical_markdown_strip_strict)


def _is_transcript_header_metadata_line(text: str) -> bool:
    value = _normalize_line(text)
    if not value:
        return True
    lowered = value.lower()
    if _TRANSCRIPT_NAME_LINE_RE.match(value):
        return True
    if _TRANSCRIPT_TRANSCRIBER_RE.match(value):
        return True
    if _TRANSCRIPT_TITLE_LINE_RE.match(value):
        return True
    if _COPYRIGHT_RE.search(value) and "clough" in lowered:
        return True
    if "biblical framework series" in lowered:
        return True
    if re.match(r"^part\s+[ivxlcm\d]+\s*:", value, re.IGNORECASE):
        return True
    if re.match(r"^(?:ch\.?|chapter)\s+[ivxlcm\d]+\s*[:\u2013\-]", value, re.IGNORECASE):
        return True
    if re.match(r"^appendix\s+[a-z]\s*:", value, re.IGNORECASE):
        return True
    if re.match(r"^lesson\s+\d+\s*[\u2013\-:]", value, re.IGNORECASE):
        return True
    if _TRANSCRIPT_DATE_RE.match(value):
        return True
    if _TRANSCRIPT_LOCATION_RE.match(value):
        return True
    if _URL_RE.search(value):
        return True
    return False


def _strip_transcript_leading_metadata_blocks(blocks: list[RawBlock]) -> list[RawBlock]:
    start_idx = 0
    for idx, block in enumerate(blocks[:28]):
        text = _normalize_line(block.text)
        if not text:
            start_idx = idx + 1
            continue
        if _is_transcript_header_metadata_line(text):
            start_idx = idx + 1
            continue
        words = len(text.split())
        if (
            idx <= 14
            and words <= 22
            and not re.search(r"[.!?]$", text)
            and text[:1].isupper()
            and not text.lower().startswith(
                ("we ", "i ", "let ", "just ", "some ", "the ", "our ", "this ", "in ", "to ")
            )
        ):
            start_idx = idx + 1
            continue
        # Stop trimming when first substantive body paragraph begins.
        if words >= 18:
            break
        # Preserve short non-metadata content once encountered.
        break
    return blocks[start_idx:]


def _split_transcript_paragraph_text(
    text: str,
    target_words: int = 220,
    min_words: int = 70,
    max_words: int = 450,
) -> list[str]:
    sentences = [chunk.strip() for chunk in _SENTENCE_SPLIT_RE.split(text) if chunk.strip()]
    if not sentences:
        return _split_long_text(text, max_words=max_words)

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if not current:
            return
        merged = " ".join(current).strip()
        if merged:
            chunks.append(merged)
        current = []
        current_words = 0

    for sentence in sentences:
        sentence_words = sentence.split()
        size = len(sentence_words)
        if size > max_words:
            flush()
            chunks.extend(
                [
                    " ".join(sentence_words[idx : idx + max_words]).strip()
                    for idx in range(0, size, max_words)
                    if " ".join(sentence_words[idx : idx + max_words]).strip()
                ]
            )
            continue

        if current and current_words + size > target_words:
            if current_words >= min_words or current_words + size > max_words:
                flush()
        current.append(sentence)
        current_words += size

    flush()

    if len(chunks) >= 2 and len(chunks[-1].split()) < min_words:
        chunks[-2] = f"{chunks[-2]} {chunks[-1]}".strip()
        chunks.pop()

    return chunks


def _html_fragment(kind: str, text: str, heading_level: int = 2) -> str:
    safe = escape(text)
    if kind == "heading":
        level = max(2, min(4, heading_level))
        return f"<h{level}>{safe}</h{level}>"
    if kind == "list_item":
        return f"<li>{safe}</li>"
    if kind == "blockquote":
        return f"<blockquote>{safe}</blockquote>"
    if kind == "note":
        return f"<blockquote class=\"note\">{safe}</blockquote>"
    if kind == "table_row":
        cells = [escape(cell.strip()) for cell in text.split("|") if cell.strip()]
        if not cells:
            return ""
        joined = "".join(f"<td>{cell}</td>" for cell in cells)
        return f"<table><tr>{joined}</tr></table>"
    return f"<p>{safe}</p>"


def _blocks_to_paragraph_rows(blocks: list[RawBlock], profile: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    state = HierarchyState(current_path=[], next_at_depth={1: 1, 2: 1, 3: 1, 4: 1})
    current_content_path = [1]
    current_section_path = [1]
    active_list_path: str | None = None
    active_list_depth: int | None = None
    active_list_kind: str | None = None

    source_blocks = blocks
    transcript_profile = profile in {"clough_transcripts_pdf_v2", "clough_transcripts_doc_v3"}
    if transcript_profile:
        source_blocks = _strip_transcript_leading_metadata_blocks(blocks)

    for block in source_blocks:
        text = _clean_marker_prefix(block.text)
        if not text:
            continue
        if block.kind == "heading":
            heading_depth = block.depth_hint
            current_content_path = _advance_path_for_depth(state, heading_depth)
            current_section_path = list(current_content_path)
            active_list_path = None
            active_list_depth = None
            active_list_kind = None
            heading_level = min(4, len(current_content_path) + 1)
            fragments = _split_long_text(text, max_words=900)
            for chunk in fragments:
                rows.append(
                    {
                        "kind": "heading",
                        "text": chunk,
                        "hierarchy_path": _path_to_string(current_content_path),
                        "html_content": _html_fragment("heading", chunk, heading_level=heading_level),
                    }
                )
            continue

        if block.kind == "list_item":
            target_depth = max(2, block.depth_hint)
            list_kind = block.list_kind or ("ol" if _NUMERIC_RE.match(block.text) else "ul")
            if (
                active_list_path is None
                or active_list_depth != target_depth
                or active_list_kind != list_kind
            ):
                list_path = _path_to_string(_advance_path_for_depth(state, target_depth))
                active_list_path = list_path
                active_list_depth = target_depth
                active_list_kind = list_kind
            else:
                list_path = active_list_path
            current_content_path = [int(v) for v in list_path.split(".") if v.isdigit()] or [1]
            for chunk in _split_long_text(text, max_words=900):
                rows.append(
                    {
                        "kind": "list_item",
                        "text": chunk,
                        "hierarchy_path": list_path,
                        "html_content": _html_fragment("list_item", chunk),
                    }
                )
            continue

        active_list_path = None
        active_list_depth = None
        active_list_kind = None
        base_path = current_section_path
        if not base_path:
            base_path = [1]
        if transcript_profile:
            chunks = _split_transcript_paragraph_text(text)
        else:
            chunks = _split_long_text(text, max_words=900)
        for chunk in chunks:
            rows.append(
                {
                    "kind": block.kind,
                    "text": chunk,
                    "hierarchy_path": _path_to_string(base_path),
                    "html_content": _html_fragment(block.kind, chunk),
                }
            )

    return rows


def _nearest_heading_parent(path: str, heading_orders: dict[str, int]) -> int | None:
    parts = [p for p in path.split(".") if p]
    for size in range(len(parts), 0, -1):
        candidate = ".".join(parts[:size])
        node_id = heading_orders.get(candidate)
        if node_id is not None:
            return node_id
    return None


def _build_table_row_html(cells: list[str]) -> str:
    joined = "".join(f"<td>{escape(cell)}</td>" for cell in cells)
    return f"<table><tr>{joined}</tr></table>"


def _blocks_to_topical_nodes(
    blocks: list[RawBlock], profile: str
) -> tuple[list[StructuredNodeInput], list[StructuredTableInput]]:
    state = HierarchyState(current_path=[], next_at_depth={1: 1, 2: 1, 3: 1, 4: 1})
    current_content_path = [1]
    current_section_path = [1]
    heading_orders: dict[str, int] = {}
    list_ordinals: dict[str, int] = {}

    nodes: list[StructuredNodeInput] = []
    tables: list[StructuredTableInput] = []

    order_index = 0
    active_list_parent_order: int | None = None
    active_list_path: str | None = None
    active_list_kind: str | None = None
    active_list_depth: int | None = None

    for block in blocks:
        cleaned_text = _clean_marker_prefix(block.text)
        if not cleaned_text:
            continue

        if block.kind == "heading":
            current_content_path = _advance_path_for_depth(state, block.depth_hint)
            path = _path_to_string(current_content_path)
            current_section_path = [int(v) for v in path.split(".") if v.isdigit()] or [1]
            parent_path = ".".join(path.split(".")[:-1]) if "." in path else ""
            parent_order = heading_orders.get(parent_path) if parent_path else None
            heading_level = min(4, len(path.split(".")) + 1)
            heading_depth = len(path.split("."))
            for chunk in _split_long_text(cleaned_text, max_words=900):
                order_index += 1
                node = StructuredNodeInput(
                    node_path=path,
                    node_type="heading",
                    depth=heading_depth,
                    order_index=order_index,
                    parent_order_index=parent_order,
                    heading_level=heading_level,
                    text_plain=chunk,
                    text_html=_html_fragment("heading", chunk, heading_level=heading_level),
                    text_markdown=(block.markdown or block.text).strip() or None,
                )
                nodes.append(node)
                heading_orders[path] = order_index
            active_list_parent_order = None
            active_list_path = None
            active_list_kind = None
            active_list_depth = None
            continue

        if block.kind == "list_item":
            target_depth = max(2, block.depth_hint)
            list_kind = block.list_kind or ("ol" if _NUMERIC_RE.match(block.text) else "ul")
            if (
                active_list_parent_order is None
                or active_list_depth != target_depth
                or active_list_kind != list_kind
            ):
                list_path = _path_to_string(_advance_path_for_depth(state, target_depth))
                current_content_path = [int(v) for v in list_path.split(".") if v.isdigit()] or [1]
                heading_parent = _nearest_heading_parent(list_path, heading_orders)
                list_depth = len(list_path.split(".")) + 1
                order_index += 1
                nodes.append(
                    StructuredNodeInput(
                        node_path=list_path,
                        node_type="list",
                        depth=list_depth,
                        order_index=order_index,
                        parent_order_index=heading_parent,
                        list_kind=list_kind,
                        text_plain="",
                        text_html=None,
                        text_markdown=None,
                    )
                )
                active_list_parent_order = order_index
                active_list_path = list_path
                active_list_kind = list_kind
                active_list_depth = target_depth
            else:
                list_path = active_list_path
                current_content_path = [int(v) for v in list_path.split(".") if v.isdigit()] or [1]
                list_depth = len(list_path.split(".")) + 1

            key = f"{list_path}:{list_kind}"
            list_ordinals[key] = list_ordinals.get(key, 0) + 1
            list_ordinal = list_ordinals[key]
            for chunk in _split_long_text(cleaned_text, max_words=900):
                order_index += 1
                nodes.append(
                    StructuredNodeInput(
                        node_path=list_path,
                        node_type="list_item",
                        depth=list_depth + 1,
                        order_index=order_index,
                        parent_order_index=active_list_parent_order,
                        list_kind=list_kind,
                        list_ordinal=list_ordinal,
                        text_plain=chunk,
                        text_html=_html_fragment("list_item", chunk),
                        text_markdown=(block.markdown or block.text).strip() or None,
                    )
                )
            continue

        active_list_parent_order = None
        active_list_path = None
        active_list_kind = None
        active_list_depth = None
        base_path = _path_to_string(current_section_path)
        parent_order = _nearest_heading_parent(base_path, heading_orders)
        base_depth = len(base_path.split("."))
        node_depth = base_depth + (1 if parent_order is not None else 0)

        if block.kind == "table_row" and get_config().dean_topical_table_parser_enabled:
            cells = [_strip_markdown_controls(cell) for cell in block.text.split("|") if cell.strip()]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue
            text_plain = " | ".join(cells)
            order_index += 1
            nodes.append(
                StructuredNodeInput(
                    node_path=base_path,
                    node_type="table",
                    depth=node_depth,
                    order_index=order_index,
                    parent_order_index=parent_order,
                    text_plain=text_plain,
                    text_html=_build_table_row_html(cells),
                    text_markdown=(block.markdown or block.text).strip() or None,
                )
            )
            tables.append(
                StructuredTableInput(
                    node_order_index=order_index,
                    table_json={"rows": [cells]},
                    table_html=_build_table_row_html(cells),
                )
            )
            continue

        for chunk in _split_long_text(cleaned_text, max_words=900):
            order_index += 1
            nodes.append(
                StructuredNodeInput(
                    node_path=base_path,
                    node_type=block.kind,
                    depth=node_depth,
                    order_index=order_index,
                    parent_order_index=parent_order,
                    text_plain=chunk,
                    text_html=_html_fragment(block.kind, chunk),
                    text_markdown=(block.markdown or block.text).strip() or None,
                )
            )

    return nodes, tables


def _extract_candidates(
    source_path: Path,
    profile: str,
    scan_confidence: float,
    source_format: str,
    force_engine_override: str | None = None,
) -> list[EngineCandidate]:
    config = get_config()
    normalized_format = (source_format or "").strip(".").lower()
    doc_mode = normalized_format in _DOC_SOURCE_FORMATS
    force_engine = (force_engine_override or "").strip().lower() or None
    if force_engine is None:
        force_engine = (
            (config.dean_doc_force_engine if doc_mode else config.dean_pdf_force_engine) or ""
        ).strip().lower() or None
    candidates: list[EngineCandidate] = []

    def add_candidate(engine: str, fn, ocr_default: bool = False) -> None:
        if force_engine and engine != force_engine:
            return
        try:
            result = fn()
            if len(result) == 3:
                blocks, ocr_used, _ = result
            else:
                blocks, ocr_used = result
            score, metrics = _score_candidate(
                EngineCandidate(engine=engine, blocks=blocks, score=0.0, metrics={}, ocr_used=ocr_used),
                profile=profile,
            )
            candidates.append(
                EngineCandidate(
                    engine=engine,
                    blocks=blocks,
                    score=score,
                    metrics=metrics,
                    ocr_used=bool(ocr_used or ocr_default),
                )
            )
        except Exception as exc:  # pragma: no cover - runtime integration guard
            logger.warning("engine_candidate_failed", engine=engine, error=str(exc))
            candidates.append(
                EngineCandidate(
                    engine=engine,
                    blocks=[],
                    score=0.0,
                    metrics={},
                    ocr_used=ocr_default,
                    error=str(exc),
                )
            )

    if doc_mode:
        add_candidate("soffice_html", lambda: _extract_blocks_with_soffice_html(source_path), ocr_default=False)
        add_candidate("soffice_text", lambda: _extract_blocks_with_soffice_text(source_path), ocr_default=False)
    else:
        if force_engine == "marker" or (force_engine is None and config.dean_pdf_marker_enabled):
            add_candidate("marker", lambda: _extract_blocks_with_marker(source_path), ocr_default=scan_confidence >= 0.5)

        add_candidate("docling", lambda: _extract_blocks_with_docling(source_path), ocr_default=True)
        add_candidate("pymupdf", lambda: _extract_blocks_with_pymupdf(source_path), ocr_default=False)

    valid = [candidate for candidate in candidates if candidate.blocks]
    if force_engine and not valid:
        raise ExtractionError(f"Forced engine '{force_engine}' failed for {source_path.name}")
    if not valid:
        raise ExtractionError(f"No extraction engine produced usable output for {source_path.name}")
    return candidates


def _select_best_candidate(candidates: list[EngineCandidate]) -> EngineCandidate:
    valid = [candidate for candidate in candidates if candidate.blocks]
    valid.sort(key=lambda candidate: candidate.score, reverse=True)
    return valid[0]


def _update_dean_extraction_metadata(
    lesson: TranscriptLesson,
    session: Session,
    candidates: list[EngineCandidate],
    selected: EngineCandidate,
    scan_confidence: float,
    parser_profile: str | None = None,
) -> None:
    if not lesson.dean_lesson_id:
        return
    dean_lesson = session.execute(
        select(DeanLesson).where(DeanLesson.id == lesson.dean_lesson_id)
    ).scalar_one_or_none()
    if not dean_lesson:
        return
    metadata = dict(dean_lesson.metadata_json or {})
    metadata["engine_candidates"] = [
        {
            "engine": candidate.engine,
            "score": candidate.score,
            "metrics": candidate.metrics,
            "ocr_used": candidate.ocr_used,
            "error": candidate.error,
            "block_count": len(candidate.blocks),
        }
        for candidate in candidates
    ]
    metadata["selected_engine"] = selected.engine
    metadata["selected_engine_reason"] = "highest_structure_quality_score"
    metadata["structure_quality_score"] = selected.score
    metadata["ocr_used"] = selected.ocr_used
    metadata["scan_confidence"] = scan_confidence
    if parser_profile:
        metadata["parser_profile"] = parser_profile
    dean_lesson.metadata_json = metadata


def process_lesson_extraction(lesson_id: int, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="process_lesson_extraction", lesson_id=lesson_id)

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        log.error("lesson_not_found")
        return {"status": "error", "paragraphs_created": 0, "message": "Lesson not found"}

    source_row = session.execute(
        select(SourceFile.raw_path, SourceFile.source_format, FileTag.name)
        .join(FileTag, SourceFile.file_tag_id == FileTag.id)
        .where(SourceFile.id == lesson.source_file_id)
    ).first()
    if not source_row:
        lesson.status = "failed"
        session.commit()
        log.error("source_file_missing")
        return {"status": "error", "paragraphs_created": 0, "message": "Source file not found"}

    source_path = Path(source_row[0])
    source_format = ((source_row[1] or source_path.suffix.lstrip(".") or "").lower()).strip()
    source_tag = str(source_row[2] or "").strip()
    doc_mode = source_format in _DOC_SOURCE_FORMATS
    marker_required = source_tag == "dean_topical_pdfs"
    if not source_path.exists():
        lesson.status = "failed"
        session.commit()
        log.error("source_path_missing", path=str(source_path), source_format=source_format)
        return {"status": "error", "paragraphs_created": 0, "message": "Source file not found on disk"}

    try:
        profile = _profile_for_lesson(lesson, session)
        scan_confidence = 0.0
        pymupdf_blocks: list[RawBlock] = []
        if not doc_mode and not marker_required:
            # First pass gathers low-cost scan density signal and fallback blocks for PDF path.
            pymupdf_blocks, _, scan_confidence = _extract_blocks_with_pymupdf(source_path)

        candidates = _extract_candidates(
            source_path,
            profile=profile,
            scan_confidence=scan_confidence,
            source_format=source_format,
            force_engine_override="marker" if marker_required else None,
        )
        force_engine = "marker" if marker_required else (
            (get_config().dean_doc_force_engine if doc_mode else get_config().dean_pdf_force_engine) or ""
        ).strip().lower() or None

        if not doc_mode and not marker_required:
            # Ensure fallback blocks are available when pymupdf was skipped due force settings.
            has_pymupdf = any(candidate.engine == "pymupdf" and candidate.blocks for candidate in candidates)
            if not force_engine and not has_pymupdf and pymupdf_blocks:
                score, metrics = _score_candidate(
                    EngineCandidate("pymupdf", pymupdf_blocks, 0.0, {}, False),
                    profile=profile,
                )
                candidates.append(
                    EngineCandidate(
                        engine="pymupdf",
                        blocks=pymupdf_blocks,
                        score=score,
                        metrics=metrics,
                        ocr_used=False,
                    )
                )

        selected = _select_best_candidate(candidates)
        if source_tag == "transcripts" and doc_mode:
            html_candidate = next(
                (candidate for candidate in candidates if candidate.engine == "soffice_html" and candidate.blocks),
                None,
            )
            if html_candidate is not None:
                selected = html_candidate
            else:
                text_candidate = next(
                    (candidate for candidate in candidates if candidate.engine == "soffice_text" and candidate.blocks),
                    None,
                )
                if text_candidate is None:
                    raise ExtractionError("Transcript DOC extraction failed: no soffice output")
                selected = text_candidate
        if doc_mode and profile in {"default", "article_pdf", "legacy_html_export_pdf", "chafer_lesson_pdf", "dbm_slides_pdf"}:
            profile = _detect_doc_profile(selected.blocks)
            for idx, candidate in enumerate(candidates):
                if not candidate.blocks:
                    continue
                rescored, metrics = _score_candidate(candidate, profile=profile)
                candidates[idx].score = rescored
                candidates[idx].metrics = metrics
            selected = _select_best_candidate(candidates)

        topical_node_mode = bool(get_config().dean_topical_node_mode and is_dean_node_lesson(lesson, session))

        if topical_node_mode:
            nodes, tables = _blocks_to_topical_nodes(selected.blocks, profile=profile)
            rows = derive_paragraph_rows_from_nodes(nodes)
            if not rows:
                raise ExtractionError("Selected engine produced no node-derived paragraphs")
            persist_dean_structured_doc(
                session=session,
                lesson=lesson,
                parser_profile=profile,
                selected_engine=selected.engine,
                structure_quality_score=selected.score,
                scan_confidence=scan_confidence,
            )
            persist_dean_structured_nodes(session=session, lesson_id=lesson_id, nodes=nodes, tables=tables)
        else:
            rows = _blocks_to_paragraph_rows(selected.blocks, profile=profile)

        if not rows:
            raise ExtractionError("Selected engine produced no normalized rows")

        session.execute(delete(TranscriptParagraph).where(TranscriptParagraph.lesson_id == lesson_id))
        session.flush()

        paragraph_ids: list[int] = []
        for index, row in enumerate(rows):
            para_text = row["text"]
            paragraph = TranscriptParagraph(
                lesson_id=lesson_id,
                paragraph_index=index,
                hierarchy_path=row["hierarchy_path"],
                content=para_text,
                html_content=row["html_content"],
                is_junk=bool(row.get("is_junk", _is_probable_junk_line(para_text))),
                is_question=bool(row.get("is_question", para_text.endswith("?"))),
                bible_refs=extract_bible_references(para_text),
                word_count=len(para_text.split()),
            )
            session.add(paragraph)
            session.flush()
            paragraph_ids.append(paragraph.id)

        if not lesson.title:
            lesson.title = source_path.stem.replace("_", " ").title()
        lesson.status = "extracted"
        _update_dean_extraction_metadata(
            lesson=lesson,
            session=session,
            candidates=candidates,
            selected=selected,
            scan_confidence=scan_confidence,
            parser_profile=profile,
        )
        session.commit()
        log.info(
            "extraction_complete",
            paragraphs=len(paragraph_ids),
            selected_engine=selected.engine,
            structure_quality_score=selected.score,
        )
        return {
            "status": "success",
            "paragraphs_created": len(paragraph_ids),
            "message": f"Extracted {len(paragraph_ids)} paragraphs using {selected.engine}",
            "selected_engine": selected.engine,
            "structure_quality_score": selected.score,
        }
    except Exception as exc:
        session.rollback()
        lesson.status = "failed"
        session.commit()
        log.error("extraction_failed", error=str(exc))
        return {"status": "error", "paragraphs_created": 0, "message": str(exc)}
