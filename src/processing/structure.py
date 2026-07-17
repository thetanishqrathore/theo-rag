"""Structure detection and hierarchy assignment."""

from __future__ import annotations

from typing import List

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import TranscriptLesson, TranscriptParagraph
from src.processing.course_note_nodes import is_course_note_node_lesson
from src.processing.patterns import APPENDIX_HEADING_RE, CHAPTER_HEADING_RE, LESSON_HEADING_RE
from src.processing.structured_nodes import is_dean_node_lesson
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()

TRANSITION_PHRASES = [
    "now",
    "let me",
    "let's turn to",
    "finally",
    "in summary",
    "to summarize",
    "moving on",
    "next",
]


def starts_with_transition(text: str) -> bool:
    text_lower = text.lower()
    return any(text_lower.startswith(phrase) for phrase in TRANSITION_PHRASES)


def _looks_like_heading(content: str, html_content: str | None) -> bool:
    fragment = (html_content or "").lstrip().lower()
    if fragment.startswith("<h"):
        return True
    text = (content or "").strip()
    if not text:
        return False
    words = text.split()
    if len(words) > 24:
        return False
    if CHAPTER_HEADING_RE.match(text) or LESSON_HEADING_RE.match(text) or APPENDIX_HEADING_RE.match(text):
        return True
    if text.endswith(":") and len(words) <= 14:
        return True
    if text.isupper() and len(words) <= 14:
        return True
    return False


def detect_section_boundaries(paragraphs: List[dict]) -> List[int]:
    boundaries = [0]
    config = get_config()
    heading_boundaries = [
        para["paragraph_index"]
        for para in paragraphs
        if para["paragraph_index"] > 0 and _looks_like_heading(para["content"], para.get("html_content"))
    ]

    if config.transcript_heading_boundary_enabled and heading_boundaries:
        boundaries.extend(heading_boundaries)

    if not heading_boundaries:
        return sorted(set(boundaries))

    for para in paragraphs:
        idx = para["paragraph_index"]
        if idx == 0:
            continue
        if para["word_count"] >= 30 or para["is_question"] or not starts_with_transition(para["content"]):
            continue
        if heading_boundaries and min(abs(idx - marker) for marker in heading_boundaries) <= 2:
            continue
        boundaries.append(idx)
    return sorted(set(boundaries))


def assign_hierarchy_paths(paragraphs: List[dict], section_boundaries: List[int]) -> dict[int, str]:
    boundaries = sorted(section_boundaries)
    paths: dict[int, str] = {}
    section_idx = 0
    for para in paragraphs:
        idx = para["paragraph_index"]
        while section_idx + 1 < len(boundaries) and idx >= boundaries[section_idx + 1]:
            section_idx += 1
        paths[idx] = str(section_idx + 1)
    return paths


def update_paragraph_hierarchy(lesson_id: int, hierarchy_map: dict[int, str], session: Session) -> None:
    paragraphs = session.execute(
        select(TranscriptParagraph).where(TranscriptParagraph.lesson_id == lesson_id)
    ).scalars()
    for paragraph in paragraphs:
        paragraph.hierarchy_path = hierarchy_map.get(paragraph.paragraph_index, paragraph.hierarchy_path)
    session.commit()


def process_lesson_structure(lesson_id: int, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="process_lesson_structure", lesson_id=lesson_id)

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        return {"status": "error", "sections_detected": 0, "message": "Lesson not found"}

    rows = session.execute(
        select(
            TranscriptParagraph.paragraph_index,
            TranscriptParagraph.content,
            TranscriptParagraph.html_content,
            TranscriptParagraph.word_count,
            TranscriptParagraph.is_question,
        ).where(TranscriptParagraph.lesson_id == lesson_id).order_by(TranscriptParagraph.paragraph_index)
    ).all()

    paragraphs = [
        {
            "paragraph_index": row[0],
            "content": row[1],
            "html_content": row[2],
            "word_count": row[3],
            "is_question": row[4],
        }
        for row in rows
    ]

    if not paragraphs:
        lesson.status = "failed"
        session.commit()
        return {"status": "error", "sections_detected": 0, "message": "No paragraphs found"}

    # Preserve extractor-provided hierarchy for node-backed course notes.
    if get_config().course_notes_node_mode and is_course_note_node_lesson(lesson, session):
        paths = [
            p.hierarchy_path
            for p in session.execute(
                select(TranscriptParagraph).where(TranscriptParagraph.lesson_id == lesson_id)
            ).scalars()
            if p.hierarchy_path
        ]
        top_sections = {path.split(".")[0] for path in paths}
        lesson.status = "structured"
        session.commit()
        log.info("structure_preserved_course_notes", sections=len(top_sections))
        return {
            "status": "success",
            "sections_detected": len(top_sections),
            "message": f"Preserved course-note hierarchy with {len(top_sections)} sections",
        }

    # Preserve extractor-provided hierarchy for node-backed Dean lessons.
    if is_dean_node_lesson(lesson, session):
        paths = [
            p.hierarchy_path
            for p in session.execute(
                select(TranscriptParagraph).where(TranscriptParagraph.lesson_id == lesson_id)
            ).scalars()
            if p.hierarchy_path
        ]
        top_sections = {path.split(".")[0] for path in paths}
        lesson.status = "structured"
        session.commit()
        log.info("structure_preserved_node_backed", sections=len(top_sections))
        return {
            "status": "success",
            "sections_detected": len(top_sections),
            "message": f"Preserved extractor hierarchy with {len(top_sections)} sections",
        }

    # Preserve extractor-provided hierarchy when it already contains nested paths.
    existing_paths = [
        p.hierarchy_path
        for p in session.execute(
            select(TranscriptParagraph).where(TranscriptParagraph.lesson_id == lesson_id)
        ).scalars()
    ]
    if any("." in path for path in existing_paths if path):
        top_sections = {path.split(".")[0] for path in existing_paths if path}
        lesson.status = "structured"
        session.commit()
        log.info("structure_preserved", sections=len(top_sections))
        return {
            "status": "success",
            "sections_detected": len(top_sections),
            "message": f"Preserved extractor hierarchy with {len(top_sections)} sections",
        }

    boundaries = detect_section_boundaries(paragraphs)
    hierarchy_map = assign_hierarchy_paths(paragraphs, boundaries)
    update_paragraph_hierarchy(lesson_id, hierarchy_map, session)

    lesson.status = "structured"
    session.commit()
    log.info("structure_complete", sections=len(boundaries))

    return {
        "status": "success",
        "sections_detected": len(boundaries),
        "message": f"Detected {len(boundaries)} sections",
    }
