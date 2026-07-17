"""Single-document extraction for clough course-notes volumes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import CourseNoteVolume, FileTag, SourceFile, TranscriptLesson, TranscriptParagraph
from src.ingestion.ingest_course_notes import LESSON_MARKER_RE
from src.processing.course_note_nodes import is_course_note_node_lesson, persist_course_note_nodes
from src.processing.extract import (
    _blocks_to_topical_nodes,
    _extract_blocks_with_pymupdf,
    _extract_candidates,
    _score_candidate,
    _select_best_candidate,
)
from src.processing.structured_nodes import derive_paragraph_rows_from_nodes
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()

_FRONT_MATTER_HEADING_RE = re.compile(
    r"^\s*(?:table\s+of\s+contents|contents|preface|introduction|foreword|index)\b",
    re.IGNORECASE,
)


def _tag_name_for_lesson(lesson_id: int, session: Session) -> str | None:
    return session.execute(
        select(FileTag.name)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .join(TranscriptLesson, TranscriptLesson.source_file_id == SourceFile.id)
        .where(TranscriptLesson.id == lesson_id)
    ).scalar_one_or_none()


def _detect_course_note_profile(blocks: list[Any]) -> str:
    if not blocks:
        return "article_pdf"
    headings = sum(1 for block in blocks if block.kind == "heading")
    list_items = sum(1 for block in blocks if block.kind == "list_item")
    total_words = sum(len((block.text or "").split()) for block in blocks)
    average_words = total_words / max(len(blocks), 1)
    structure_ratio = (headings + list_items) / max(len(blocks), 1)

    if structure_ratio >= 0.2 or list_items >= 12:
        return "chafer_lesson_pdf"
    if average_words >= 26 and headings <= max(3, len(blocks) // 20):
        return "article_pdf"
    return "legacy_html_export_pdf"


def _annotate_front_matter(nodes: list[Any]) -> tuple[list[Any], int]:
    if not nodes:
        return nodes, 0
    ordered = sorted(nodes, key=lambda item: item.order_index)
    first_lesson_order = None
    for node in ordered:
        text = (node.text_plain or "").strip()
        if LESSON_MARKER_RE.match(text):
            first_lesson_order = node.order_index
            break

    if first_lesson_order is None:
        front_like = 0
        for node in ordered[:20]:
            text = (node.text_plain or "").strip()
            if _FRONT_MATTER_HEADING_RE.match(text):
                front_like += 1
        if front_like > 0:
            # Conservative fallback: mark only early prefix as front matter.
            first_lesson_order = min(ordered[-1].order_index + 1, 12)

    flagged = 0
    for node in ordered:
        is_front = bool(first_lesson_order is not None and node.order_index < first_lesson_order)
        flags = dict(node.quality_flags or {})
        flags["is_front_matter"] = is_front
        node.quality_flags = flags
        if is_front:
            flagged += 1
    return ordered, flagged


def _update_volume_metadata(
    session: Session,
    lesson: TranscriptLesson,
    parser_profile: str,
    selected_engine: str,
    structure_quality_score: float,
    scan_confidence: float,
    candidates: list[Any],
) -> None:
    volume = session.execute(
        select(CourseNoteVolume).where(CourseNoteVolume.source_file_id == lesson.source_file_id)
    ).scalar_one_or_none()
    if volume is None:
        return
    metadata = dict(volume.metadata_json or {})
    metadata["parser_profile"] = parser_profile
    metadata["selected_engine"] = selected_engine
    metadata["selected_engine_reason"] = "highest_structure_quality_score"
    metadata["structure_quality_score"] = structure_quality_score
    metadata["scan_confidence"] = scan_confidence
    metadata["ocr_used"] = bool(
        next((candidate.ocr_used for candidate in candidates if candidate.engine == selected_engine), False)
    )
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
    volume.metadata_json = metadata


def process_course_note_extraction(lesson_id: int, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="process_course_note_extraction", lesson_id=lesson_id)

    if _tag_name_for_lesson(lesson_id, session) != "course_notes":
        return {"status": "error", "paragraphs_created": 0, "message": "Lesson is not tagged as course_notes"}

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        return {"status": "error", "paragraphs_created": 0, "message": "Lesson not found"}
    if not is_course_note_node_lesson(lesson, session):
        return {"status": "error", "paragraphs_created": 0, "message": "Lesson is not course_notes node-backed"}

    source_row = session.execute(
        select(SourceFile.raw_path, SourceFile.source_format).where(SourceFile.id == lesson.source_file_id)
    ).first()
    if not source_row:
        lesson.status = "failed"
        session.commit()
        return {"status": "error", "paragraphs_created": 0, "message": "Source file not found"}

    source_path = Path(source_row[0])
    source_format = ((source_row[1] or source_path.suffix.lstrip(".") or "").lower()).strip()
    if not source_path.exists():
        lesson.status = "failed"
        session.commit()
        return {"status": "error", "paragraphs_created": 0, "message": "Source file not found on disk"}

    try:
        scan_confidence = 0.0
        pymupdf_blocks, _, scan_confidence = _extract_blocks_with_pymupdf(source_path)
        initial_profile = _detect_course_note_profile(pymupdf_blocks)
        force_engine = (get_config().course_notes_engine_force or "").strip().lower() or None
        candidates = _extract_candidates(
            source_path,
            profile=initial_profile,
            scan_confidence=scan_confidence,
            source_format=source_format,
            force_engine_override=force_engine,
        )
        selected = _select_best_candidate(candidates)
        detected_profile = _detect_course_note_profile(selected.blocks)
        for idx, candidate in enumerate(candidates):
            if not candidate.blocks:
                continue
            rescored, metrics = _score_candidate(candidate, profile=detected_profile)
            candidates[idx].score = rescored
            candidates[idx].metrics = metrics
        selected = _select_best_candidate(candidates)

        nodes, tables = _blocks_to_topical_nodes(selected.blocks, profile=detected_profile)
        nodes, front_count = _annotate_front_matter(nodes)
        rows = derive_paragraph_rows_from_nodes(nodes)
        if not rows:
            raise ValueError("Selected engine produced no node-derived rows")

        session.execute(delete(TranscriptParagraph).where(TranscriptParagraph.lesson_id == lesson_id))
        session.flush()

        persist_course_note_nodes(session=session, lesson_id=lesson_id, nodes=nodes, tables=tables)

        paragraph_count = 0
        paragraph_index = 0
        for row in rows:
            content = (row["text"] or "").strip()
            if not content:
                continue
            session.add(
                TranscriptParagraph(
                    lesson_id=lesson_id,
                    paragraph_index=paragraph_index,
                    hierarchy_path=row["hierarchy_path"],
                    content=content,
                    html_content=row.get("html_content"),
                    is_junk=False,
                    is_question=bool(row.get("is_question", content.endswith("?"))),
                    bible_refs=[],
                    word_count=len(content.split()),
                )
            )
            paragraph_count += 1
            paragraph_index += 1

        if paragraph_count == 0:
            raise ValueError("No paragraph rows generated from course-note nodes")

        lesson.status = "extracted"
        _update_volume_metadata(
            session=session,
            lesson=lesson,
            parser_profile=detected_profile,
            selected_engine=selected.engine,
            structure_quality_score=selected.score,
            scan_confidence=scan_confidence,
            candidates=candidates,
        )
        session.commit()
        log.info(
            "course_note_extraction_complete",
            paragraphs=paragraph_count,
            nodes=len(nodes),
            front_matter_nodes=front_count,
            selected_engine=selected.engine,
        )
        return {
            "status": "success",
            "paragraphs_created": paragraph_count,
            "message": f"Extracted {paragraph_count} paragraphs from course-note volume",
            "selected_engine": selected.engine,
            "structure_quality_score": selected.score,
            "front_matter_nodes": front_count,
        }
    except Exception as exc:
        session.rollback()
        lesson.status = "failed"
        session.commit()
        log.error("course_note_extraction_failed", error=str(exc))
        return {"status": "error", "paragraphs_created": 0, "message": str(exc)}
