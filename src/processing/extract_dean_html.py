"""Dean HTML extraction into structured paragraphs with hierarchy preservation."""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from typing import List

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DeanLesson, FileTag, SourceFile, TranscriptLesson, TranscriptParagraph
from src.processing.profiles import detect_dean_html_profile
from src.processing.patterns import COPYRIGHT_RE, PAGE_MARKER_RE, REPEATED_WS_RE
from src.processing.extract import (
    RawBlock,
    _DocHtmlBlockParser,
    _blocks_to_topical_nodes,
    _parse_doc_paragraph_style_depths,
    extract_bible_references,
)
from src.processing.structured_nodes import (
    derive_paragraph_rows_from_nodes,
    is_dean_node_lesson,
    persist_dean_structured_doc,
    persist_dean_structured_nodes,
)
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", flags=re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", flags=re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_BLOCK_RE = re.compile(r"<(p|h1|h2|h3|h4|li|blockquote)[^>]*>(.*?)</\1>", flags=re.IGNORECASE | re.DOTALL)
_STRONG_BREAK_RE = re.compile(r"(?:<br\s*/?>\s*){2,}", flags=re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = REPEATED_WS_RE
_PAGE_MARKER_RE = PAGE_MARKER_RE
_COPYRIGHT_RE = COPYRIGHT_RE
_NUMERIC_SECTION_SPLIT_RE = re.compile(
    r"(?=\s(?:\d{1,2}\.\s+|[A-Za-z]\)\s+|[IVXLC]{1,6}\.\s+|[IVXLC]{1,6}\)\s+))"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

_SOFT_MIN_WORDS = 220
_SOFT_MAX_WORDS = 450
_HARD_MAX_WORDS = 950
_BOILERPLATE_WORD_LIMIT = 18
_BOILERPLATE_REPEAT_THRESHOLD = 3
_DEDUPE_WORD_LIMIT = 120


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _clean_block(html_fragment: str) -> str:
    fragment = _BR_RE.sub("\n", html_fragment)
    text = _TAG_RE.sub(" ", fragment)
    text = unescape(text)
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    cleaned_lines: list[str] = []
    for line in lines:
        if not line:
            continue
        line = _PAGE_MARKER_RE.sub("", line).strip()
        if not line:
            continue
        if _COPYRIGHT_RE.search(line):
            continue
        cleaned_lines.append(line)
    return _WS_RE.sub(" ", " ".join(cleaned_lines)).strip()


def _split_fragment_on_breaks(fragment: str) -> list[str]:
    pieces = _STRONG_BREAK_RE.split(fragment)
    if len(pieces) == 1:
        return [fragment]
    return [piece for piece in pieces if piece and piece.strip()]


def _hard_split_words(text: str, max_words: int = _SOFT_MAX_WORDS) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    chunks: list[str] = []
    for idx in range(0, len(words), max_words):
        chunk = " ".join(words[idx : idx + max_words]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _split_by_sentences(text: str) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if len(sentences) <= 1:
        return _hard_split_words(text)

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if current and current_words >= _SOFT_MIN_WORDS and current_words + sentence_words > _SOFT_MAX_WORDS:
            chunks.append(" ".join(current).strip())
            current = [sentence]
            current_words = sentence_words
            continue
        current.append(sentence)
        current_words += sentence_words
        if current_words >= _SOFT_MAX_WORDS:
            chunks.append(" ".join(current).strip())
            current = []
            current_words = 0

    if current:
        chunks.append(" ".join(current).strip())

    normalized: list[str] = []
    for chunk in chunks:
        if len(chunk.split()) > _HARD_MAX_WORDS:
            normalized.extend(_hard_split_words(chunk))
        else:
            normalized.append(chunk)
    return normalized


def _split_oversize_block(text: str) -> list[str]:
    word_count = len(text.split())
    if word_count <= _SOFT_MAX_WORDS:
        return [text]

    marker_parts = [part.strip() for part in _NUMERIC_SECTION_SPLIT_RE.split(text) if part.strip()]
    candidate_parts = marker_parts if len(marker_parts) > 1 else [text]

    split_parts: list[str] = []
    for part in candidate_parts:
        if len(part.split()) <= _SOFT_MAX_WORDS:
            split_parts.append(part)
            continue
        split_parts.extend(_split_by_sentences(part))

    final_parts: list[str] = []
    for part in split_parts:
        if len(part.split()) > _HARD_MAX_WORDS:
            final_parts.extend(_hard_split_words(part))
        else:
            final_parts.append(part)
    return [part for part in final_parts if part]


def _normalize_block_key(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = _WS_RE.sub(" ", normalized).strip()
    return normalized


def _dedupe_boilerplate_blocks(blocks: list[str]) -> list[str]:
    short_counts: dict[str, int] = {}
    for block in blocks:
        words = len(block.split())
        if words > _BOILERPLATE_WORD_LIMIT:
            continue
        key = _normalize_block_key(block)
        if not key:
            continue
        short_counts[key] = short_counts.get(key, 0) + 1

    seen_keys: set[str] = set()
    deduped: list[str] = []
    for block in blocks:
        key = _normalize_block_key(block)
        if not key:
            continue
        words = len(block.split())
        if words <= _BOILERPLATE_WORD_LIMIT and short_counts.get(key, 0) >= _BOILERPLATE_REPEAT_THRESHOLD:
            continue
        if key in seen_keys and words <= _DEDUPE_WORD_LIMIT:
            continue
        seen_keys.add(key)
        deduped.append(block)
    return deduped


def _legacy_extract_blocks(html: str) -> List[str]:
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    cleaned = _COMMENT_RE.sub(" ", cleaned)
    raw_fragments: list[str] = []
    for _, fragment in _BLOCK_RE.findall(cleaned):
        raw_fragments.extend(_split_fragment_on_breaks(fragment))

    cleaned_blocks = [_clean_block(fragment) for fragment in raw_fragments]
    cleaned_blocks = [block for block in cleaned_blocks if block and block != "\xa0"]

    split_blocks: list[str] = []
    for block in cleaned_blocks:
        split_blocks.extend(_split_oversize_block(block))

    split_blocks = [block for block in split_blocks if block and block != "\xa0"]
    return _dedupe_boilerplate_blocks(split_blocks)


def _tag_and_profile_for_lesson(lesson_id: int, session: Session) -> tuple[str, str]:
    tag = session.execute(
        select(FileTag.name)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .join(TranscriptLesson, TranscriptLesson.source_file_id == SourceFile.id)
        .where(TranscriptLesson.id == lesson_id)
    ).scalar_one_or_none()
    return tag or "", detect_dean_html_profile(tag)


def _extract_semantic_blocks(html: str) -> list[RawBlock]:
    # Inline <script> (e.g. the overLIB tooltip library in some Word exports) is not
    # lesson text. <style> stays: paragraph depths are read from its Mso classes.
    html = _SCRIPT_RE.sub(" ", html)
    parser = _DocHtmlBlockParser(_parse_doc_paragraph_style_depths(html))
    parser.feed(html)
    blocks = [block for block in parser.blocks if (block.text or "").strip()]
    return blocks


def _fallback_rows_from_legacy_blocks(blocks: list[str]) -> list[dict]:
    rows: list[dict] = []
    for idx, block in enumerate(blocks):
        rows.append(
            {
                "text": block,
                "hierarchy_path": str(idx + 1),
                "html_content": f"<p>{block}</p>",
                "is_question": block.endswith("?"),
            }
        )
    return rows


def _update_dean_metadata(
    lesson: TranscriptLesson,
    session: Session,
    parser_profile: str,
    selected_engine: str,
    structure_quality_score: float,
) -> None:
    if not lesson.dean_lesson_id:
        return
    dean_lesson = session.execute(
        select(DeanLesson).where(DeanLesson.id == lesson.dean_lesson_id)
    ).scalar_one_or_none()
    if not dean_lesson:
        return
    metadata = dict(dean_lesson.metadata_json or {})
    metadata["parser_profile"] = metadata.get("parser_profile") or parser_profile
    metadata["selected_engine"] = selected_engine
    metadata["selected_engine_reason"] = "dean_html_semantic_parser"
    metadata["structure_quality_score"] = structure_quality_score
    metadata["scan_confidence"] = 0.0
    metadata["ocr_used"] = False
    metadata.setdefault("engine_candidates", [])
    metadata["engine_candidates"] = [
        {
            "engine": selected_engine,
            "score": structure_quality_score,
            "metrics": {
                "hierarchy_paths": None,
                "html_mode": get_config().dean_html_structure_mode,
            },
            "ocr_used": False,
            "error": None,
        }
    ]
    dean_lesson.metadata_json = metadata


def process_dean_lesson_extraction(lesson_id: int, session: Session) -> dict:
    """Extract normalized paragraph rows from Dean HTML sources."""
    setup_logging()
    log = logger.bind(function="process_dean_lesson_extraction", lesson_id=lesson_id)

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        return {"status": "error", "paragraphs_created": 0, "message": "Lesson not found"}

    source = session.execute(
        select(SourceFile.raw_path).where(SourceFile.id == lesson.source_file_id)
    ).scalar_one_or_none()
    if not source:
        lesson.status = "failed"
        session.commit()
        return {"status": "error", "paragraphs_created": 0, "message": "Source file not found"}

    html_path = Path(source)
    if not html_path.exists():
        lesson.status = "failed"
        session.commit()
        return {"status": "error", "paragraphs_created": 0, "message": "HTML not found"}

    try:
        html = _read_text(html_path)
        source_tag, parser_profile = _tag_and_profile_for_lesson(lesson_id, session)
        html_mode = (get_config().dean_html_structure_mode or "node").strip().lower()

        semantic_blocks = _extract_semantic_blocks(html)
        selected_engine = "dean_html_semantic_parser"
        structure_quality_score = 0.0
        rows: list[dict]
        nodes = []
        tables = []

        if semantic_blocks and html_mode == "node":
            nodes, tables = _blocks_to_topical_nodes(semantic_blocks, profile=parser_profile)
            rows = derive_paragraph_rows_from_nodes(nodes)
            unique_paths = {node.node_path for node in nodes if getattr(node, "node_path", None)}
            structure_quality_score = round(min(1.0, len(unique_paths) / 8.0), 4) if unique_paths else 0.5
        else:
            rows = []

        if not rows:
            legacy_blocks = _legacy_extract_blocks(html)
            if not legacy_blocks:
                raise ValueError("No extractable paragraph blocks found in HTML source")
            rows = _fallback_rows_from_legacy_blocks(legacy_blocks)
            selected_engine = "dean_html_legacy_parser"
            structure_quality_score = 0.35

        session.query(TranscriptParagraph).filter(TranscriptParagraph.lesson_id == lesson_id).delete()
        session.flush()

        paragraph_ids: list[int] = []
        paragraph_index = 0
        for row in rows:
            text = (row.get("text") or "").strip()
            if len(text) < 2:
                continue
            para = TranscriptParagraph(
                lesson_id=lesson_id,
                paragraph_index=paragraph_index,
                hierarchy_path=row.get("hierarchy_path") or str(paragraph_index + 1),
                content=text,
                html_content=row.get("html_content"),
                is_junk=False,
                is_question=bool(row.get("is_question", text.endswith("?"))),
                bible_refs=extract_bible_references(text),
                word_count=len(text.split()),
            )
            session.add(para)
            session.flush()
            paragraph_ids.append(para.id)
            paragraph_index += 1

        if not paragraph_ids:
            raise ValueError("No valid paragraphs created after cleaning")

        node_mode_enabled = bool(
            get_config().dean_topical_node_mode
            and html_mode == "node"
            and is_dean_node_lesson(lesson, session)
            and nodes
        )
        if node_mode_enabled:
            persist_dean_structured_doc(
                session=session,
                lesson=lesson,
                parser_profile=parser_profile,
                selected_engine=selected_engine,
                structure_quality_score=structure_quality_score,
                scan_confidence=0.0,
            )
            persist_dean_structured_nodes(
                session=session,
                lesson_id=lesson_id,
                nodes=nodes,
                tables=tables,
            )

        lesson.status = "extracted"
        if not lesson.title:
            lesson.title = html_path.stem.replace("_", " ")
        _update_dean_metadata(
            lesson=lesson,
            session=session,
            parser_profile=parser_profile,
            selected_engine=selected_engine,
            structure_quality_score=structure_quality_score,
        )
        session.commit()
        log.info(
            "dean_extraction_complete",
            paragraphs=len(paragraph_ids),
            source_tag=source_tag,
            selected_engine=selected_engine,
            structure_quality_score=structure_quality_score,
        )
        return {
            "status": "success",
            "paragraphs_created": len(paragraph_ids),
            "message": f"Extracted {len(paragraph_ids)} paragraphs using {selected_engine}",
            "selected_engine": selected_engine,
            "structure_quality_score": structure_quality_score,
        }
    except Exception as exc:
        session.rollback()
        lesson.status = "failed"
        session.commit()
        log.error("dean_extraction_failed", error=str(exc))
        return {"status": "error", "paragraphs_created": 0, "message": str(exc)}
