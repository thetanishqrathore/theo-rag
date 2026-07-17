"""Structured node persistence helpers for Dean node-backed lessons."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import (
    DeanStructuredDoc,
    DeanStructuredNode,
    DeanStructuredTable,
    FileTag,
    SourceFile,
    TranscriptLesson,
)

DEAN_NODE_TAGS = {
    "dean_bible_books",
    "dean_topical_html",
    "dean_topical_pdfs",
    "dean_topical_doc",
    "dean_topical_rtf",
}


@dataclass
class StructuredNodeInput:
    """Normalized semantic node for Dean structured lessons."""

    node_path: str
    node_type: str
    depth: int
    order_index: int
    text_plain: str
    text_html: str | None = None
    text_markdown: str | None = None
    heading_level: int | None = None
    list_kind: str | None = None
    list_ordinal: int | None = None
    parent_order_index: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    bbox_json: dict[str, Any] | None = None
    quality_flags: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredTableInput:
    """Table payload extracted from structured node stream."""

    node_order_index: int
    table_json: dict[str, Any]
    table_html: str | None = None


def _tag_name_for_lesson(lesson: TranscriptLesson, session: Session) -> str:
    tag_name = session.execute(
        select(FileTag.name)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .where(SourceFile.id == lesson.source_file_id)
    ).scalar_one_or_none()
    return tag_name or ""


def is_topical_pdf_lesson(lesson: TranscriptLesson, session: Session) -> bool:
    """Return True when lesson comes from dean_topical_pdfs tag."""
    return _tag_name_for_lesson(lesson, session) == "dean_topical_pdfs"


def is_dean_node_lesson(lesson: TranscriptLesson, session: Session) -> bool:
    """Return True when lesson uses the Dean node-backed structure path."""
    return _tag_name_for_lesson(lesson, session) in DEAN_NODE_TAGS


def is_topical_node_lesson(lesson: TranscriptLesson, session: Session) -> bool:
    """Backward-compatible alias for previous helper name."""
    return is_dean_node_lesson(lesson, session)


def persist_dean_structured_doc(
    session: Session,
    lesson: TranscriptLesson,
    parser_profile: str,
    selected_engine: str | None,
    structure_quality_score: float | None,
    scan_confidence: float | None,
) -> None:
    """Upsert document-level structured extraction diagnostics."""
    existing = session.execute(
        select(DeanStructuredDoc).where(DeanStructuredDoc.lesson_id == lesson.id)
    ).scalar_one_or_none()
    if existing is None:
        existing = DeanStructuredDoc(
            lesson_id=lesson.id,
            source_file_id=lesson.source_file_id,
        )
        session.add(existing)

    existing.parser_profile = parser_profile
    existing.selected_engine = selected_engine
    existing.structure_quality_score = structure_quality_score
    existing.scan_confidence = scan_confidence


def persist_dean_structured_nodes(
    session: Session,
    lesson_id: int,
    nodes: list[StructuredNodeInput],
    tables: list[StructuredTableInput] | None = None,
) -> None:
    """Replace structured node/table rows for a Dean lesson."""
    session.execute(delete(DeanStructuredNode).where(DeanStructuredNode.lesson_id == lesson_id))
    session.flush()

    order_to_node_id: dict[int, int] = {}
    order_to_depth: dict[int, int] = {}
    for node in sorted(nodes, key=lambda item: item.order_index):
        parent_id = None
        parent_depth = None
        if node.parent_order_index is not None:
            parent_id = order_to_node_id.get(node.parent_order_index)
            parent_depth = order_to_depth.get(node.parent_order_index)
        node_depth = max(1, int(node.depth))
        if parent_depth is not None:
            # Enforce strict tree depth monotonicity for integrity checks.
            node_depth = max(node_depth, int(parent_depth) + 1)
        model = DeanStructuredNode(
            lesson_id=lesson_id,
            parent_node_id=parent_id,
            node_path=node.node_path,
            node_type=node.node_type,
            depth=node_depth,
            order_index=node.order_index,
            heading_level=node.heading_level,
            list_kind=node.list_kind,
            list_ordinal=node.list_ordinal,
            text_plain=node.text_plain,
            text_html=node.text_html,
            text_markdown=node.text_markdown,
            page_start=node.page_start,
            page_end=node.page_end,
            bbox_json=node.bbox_json,
            quality_flags=node.quality_flags or {},
        )
        session.add(model)
        session.flush()
        order_to_node_id[node.order_index] = model.id
        order_to_depth[node.order_index] = int(model.depth)

    if not tables:
        return

    for table in tables:
        node_id = order_to_node_id.get(table.node_order_index)
        if not node_id:
            continue
        session.add(
            DeanStructuredTable(
                node_id=node_id,
                table_json=table.table_json,
                table_html=table.table_html,
            )
        )


def load_dean_structured_nodes(session: Session, lesson_id: int) -> list[DeanStructuredNode]:
    """Fetch structured node rows in deterministic order."""
    return (
        session.execute(
            select(DeanStructuredNode)
            .where(DeanStructuredNode.lesson_id == lesson_id)
            .order_by(DeanStructuredNode.order_index)
        )
        .scalars()
        .all()
    )


def load_topical_nodes(session: Session, lesson_id: int) -> list[DeanStructuredNode]:
    """Backward-compatible alias for previous helper name."""
    return load_dean_structured_nodes(session, lesson_id)


def derive_paragraph_rows_from_nodes(nodes: list[StructuredNodeInput]) -> list[dict[str, Any]]:
    """Build compatibility paragraph rows from semantic nodes."""
    rows: list[dict[str, Any]] = []
    paragraph_like = {"heading", "paragraph", "list_item", "blockquote", "note", "table"}
    ordered = sorted(nodes, key=lambda item: item.order_index)
    for node in ordered:
        if node.node_type not in paragraph_like:
            continue
        text = (node.text_plain or "").strip()
        if not text:
            continue
        rows.append(
            {
                "text": text,
                "hierarchy_path": node.node_path or "1",
                "html_content": node.text_html,
                "is_question": text.endswith("?"),
            }
        )
    return rows

