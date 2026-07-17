"""Structured node helpers for clough course-notes lessons."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.models import CourseNoteNode, CourseNoteTable, FileTag, SourceFile, TranscriptLesson
from src.processing.structured_nodes import StructuredNodeInput, StructuredTableInput


def _tag_name_for_lesson(lesson: TranscriptLesson, session: Session) -> str:
    tag_name = session.execute(
        select(FileTag.name)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .where(SourceFile.id == lesson.source_file_id)
    ).scalar_one_or_none()
    return tag_name or ""


def is_course_note_node_lesson(lesson: TranscriptLesson, session: Session) -> bool:
    """Return True when lesson is from the clough course_notes tag."""
    return _tag_name_for_lesson(lesson, session) == "course_notes"


def load_course_note_nodes(session: Session, lesson_id: int) -> list[CourseNoteNode]:
    """Fetch course-note nodes in deterministic order."""
    return (
        session.execute(
            select(CourseNoteNode)
            .where(CourseNoteNode.lesson_id == lesson_id)
            .order_by(CourseNoteNode.order_index)
        )
        .scalars()
        .all()
    )


def persist_course_note_nodes(
    session: Session,
    lesson_id: int,
    nodes: list[StructuredNodeInput],
    tables: list[StructuredTableInput] | None = None,
) -> None:
    """Replace course-note node and table rows for a lesson."""
    session.execute(
        delete(CourseNoteTable).where(
            CourseNoteTable.node_id.in_(
                select(CourseNoteNode.id).where(CourseNoteNode.lesson_id == lesson_id)
            )
        )
    )
    session.execute(delete(CourseNoteNode).where(CourseNoteNode.lesson_id == lesson_id))
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
            node_depth = max(node_depth, int(parent_depth) + 1)

        model = CourseNoteNode(
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
            CourseNoteTable(
                node_id=node_id,
                table_json=table.table_json,
                table_html=table.table_html,
            )
        )
