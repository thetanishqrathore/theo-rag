"""Canonical HTML generation."""

from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
from html import escape
from typing import List
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    CourseNoteNode,
    DeanBook,
    DeanCollection,
    DeanLesson,
    DeanStructuredNode,
    Corpus,
    FileTag,
    SourceFile,
    TranscriptLesson,
    TranscriptParagraph,
    TranscriptPart,
    TranscriptChapter,
    Series,
)
from src.processing.course_note_nodes import is_course_note_node_lesson, load_course_note_nodes
from src.processing.structured_nodes import is_dean_node_lesson, load_dean_structured_nodes
from src.utils.config import get_config


def _source_context_for_lesson(lesson: TranscriptLesson, session: Session) -> tuple[str, str, str, str]:
    row = session.execute(
        select(
            SourceFile.filename,
            SourceFile.raw_path,
            FileTag.name,
            Corpus.name,
        )
        .join(FileTag, SourceFile.file_tag_id == FileTag.id)
        .join(Corpus, FileTag.corpus_id == Corpus.id)
        .where(SourceFile.id == lesson.source_file_id)
    ).first()
    if not row:
        return f"lesson_{lesson.id}.pdf", "", "unknown"
    return str(row[0]), str(row[1] or ""), str(row[2] or "unknown"), str(row[3] or "unknown")


def _source_subdirs(raw_path: str, raw_root: str, corpus_name: str, tag_name: str) -> list[str]:
    normalized = str(raw_path or "").replace("\\", "/")
    parts = [segment for segment in PurePosixPath(normalized).parts if segment not in {"", "/"}]
    if not parts:
        return []

    if corpus_name in parts:
        idx = parts.index(corpus_name)
        subdirs = parts[idx + 1 : -1]
    else:
        root_parts = [segment for segment in PurePosixPath(raw_root.replace("\\", "/")).parts if segment not in {"", "/"}]
        subdirs = []
        if len(parts) >= len(root_parts) and parts[: len(root_parts)] == root_parts:
            remaining = parts[len(root_parts) :]
            if remaining and remaining[0] == corpus_name:
                remaining = remaining[1:]
            subdirs = remaining[:-1]
        elif len(parts) > 1:
            subdirs = parts[:-1]

    if subdirs and subdirs[0] == tag_name:
        subdirs = subdirs[1:]
    return subdirs


def _resolve_canonical_collision(rel_path: Path, lesson_id: int, session: Session) -> Path:
    candidate = rel_path.as_posix()
    existing = session.execute(
        select(TranscriptLesson.id).where(
            TranscriptLesson.canonical_html_path == candidate,
            TranscriptLesson.id != lesson_id,
        )
    ).scalar_one_or_none()
    if existing is None:
        return rel_path
    return rel_path.with_name(f"{rel_path.stem}__{lesson_id}{rel_path.suffix}")


def _canonical_paths_for_lesson(lesson: TranscriptLesson, session: Session, canonical_root: Path) -> tuple[Path, str]:
    config = get_config()
    source_filename, raw_path, tag_name, corpus_name = _source_context_for_lesson(lesson, session)
    stem = Path(source_filename).stem or f"lesson_{lesson.id}"
    subdirs = _source_subdirs(raw_path, config.raw_data_path, corpus_name, tag_name)
    rel_path = Path(corpus_name, tag_name, *subdirs, f"{stem}.html")
    rel_path = _resolve_canonical_collision(rel_path, lesson.id, session)
    out_path = canonical_root / rel_path
    return out_path, rel_path.as_posix()


def group_paragraphs_by_hierarchy(paragraphs: List[TranscriptParagraph]) -> List[dict]:
    sections: list[dict] = []
    current_path = None
    current_paras: list[TranscriptParagraph] = []

    for para in paragraphs:
        if current_path is None:
            current_path = para.hierarchy_path
        if para.hierarchy_path != current_path:
            sections.append({"hierarchy_path": current_path, "paragraphs": current_paras})
            current_path = para.hierarchy_path
            current_paras = []
        current_paras.append(para)

    if current_paras:
        sections.append({"hierarchy_path": current_path, "paragraphs": current_paras})
    return sections


def build_metadata_html(
    lesson: TranscriptLesson,
    part: TranscriptPart | None,
    series: Series | None,
    chapter: TranscriptChapter | None,
    dean_lesson: DeanLesson | None = None,
    dean_book: DeanBook | None = None,
    dean_collection: DeanCollection | None = None,
) -> str:
    chapter_number = chapter.chapter_number if chapter else None
    show_numbered_chapter = chapter is not None and chapter_number is not None and int(chapter_number) > 0
    parts: list[str] = ["<div class=\"metadata\">"]
    if series:
        parts.append(f"<h1>{escape(series.title)}</h1>")
    if part:
        parts.append(f"<h2>Part {part.part_number}: {escape(part.title)}</h2>")
    if show_numbered_chapter:
        parts.append(f"<h3>Chapter {chapter.chapter_number}: {escape(chapter.title)}</h3>")
    if dean_collection:
        parts.append(f"<h2>Collection: {escape(dean_collection.name)}</h2>")
    if dean_book:
        parts.append(f"<h3>Book: {escape(dean_book.display_name)}</h3>")
    title = lesson.title or ""
    parts.append(f"<h4>Lesson {lesson.lesson_number}: {escape(title)}</h4>")
    if dean_lesson and dean_lesson.lesson_code:
        parts.append(f"<p>Lesson Code: {escape(dean_lesson.lesson_code)}</p>")
    if lesson.date:
        parts.append(f"<p>Date: {lesson.date.isoformat()}</p>")
    if lesson.speaker:
        parts.append(f"<p>Speaker: {escape(lesson.speaker)}</p>")
    if lesson.location:
        parts.append(f"<p>Location: {escape(lesson.location)}</p>")
    if lesson.transcriber:
        parts.append(f"<p>Transcriber: {escape(lesson.transcriber)}</p>")
    parts.append("</div>")
    return "\n".join(parts)


def highlight_bible_refs(text: str, bible_refs: list[str]) -> str:
    escaped = escape(text)
    if not bible_refs:
        return escaped
    for ref in bible_refs:
        escaped = escaped.replace(escape(ref), f"<span class='bible-ref'>{escape(ref)}</span>")
    return escaped


def create_html_document(
    metadata_html: str,
    sections: List[dict],
    lesson: TranscriptLesson,
    part: TranscriptPart | None,
    chapter: TranscriptChapter | None,
) -> str:
    title = f"Lesson {lesson.lesson_number} - {lesson.speaker}"
    part_title = "" if not part else f"Part {part.part_number}, "
    chapter_title = (
        f"Chapter {chapter.chapter_number}, "
        if chapter and chapter.chapter_number is not None and int(chapter.chapter_number) > 0
        else ""
    )
    html = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "  <meta charset=\"UTF-8\">",
        f"  <title>{escape(part_title + chapter_title + title)}</title>",
        "  <style>",
        "    body { font-family: Georgia, serif; line-height: 1.6; max-width: 800px; margin: 40px auto; padding: 0 20px; }",
        "    .metadata { background: #f5f5f5; padding: 15px; margin-bottom: 30px; border-left: 4px solid #333; }",
        "    .section { margin: 30px 0; }",
        "    .para { margin: 15px 0; }",
        "    .list { margin: 10px 0 10px 24px; }",
        "    .node { margin: 8px 0; }",
        "    .hierarchy-1 {}",
        "    .hierarchy-2 { margin-left: 20px; }",
        "    .hierarchy-3 { margin-left: 40px; }",
        "    .hierarchy-4 { margin-left: 60px; }",
        "    .bible-ref { color: #0066cc; font-weight: bold; }",
        "    .question { font-style: italic; color: #444; }",
        "  </style>",
        "</head>",
        "<body>",
        metadata_html,
        "<article class=\"lesson-content\">",
    ]

    tag_re = re.compile(r"^\s*<([a-z0-9]+)\b", re.IGNORECASE)

    def _inject_attrs(fragment: str, attrs: dict[str, str]) -> str:
        marker = " ".join(f'{key}="{escape(value)}"' for key, value in attrs.items())
        match = re.match(r"^\s*<([a-z0-9]+)\b", fragment, re.IGNORECASE)
        if not match:
            return fragment
        tag = match.group(1)
        return re.sub(rf"^\s*<{tag}\b", f"<{tag} {marker}", fragment, count=1, flags=re.IGNORECASE)

    for section in sections:
        path = section["hierarchy_path"]
        level = len(path.split("."))
        html.append(f"<section class=\"section hierarchy-{level}\" data-path=\"{escape(path)}\">")
        list_open = False
        for para in section["paragraphs"]:
            classes = ["para"]
            if para.is_question:
                classes.append("question")
            attrs = {
                "class": "node " + " ".join(classes),
                "data-para-id": str(para.id),
                "data-hierarchy": path,
            }
            fragment = (para.html_content or "").strip()
            if not fragment:
                text = highlight_bible_refs(para.content, para.bible_refs or [])
                fragment = f"<p>{text}</p>"
            else:
                tag_match = tag_re.match(fragment)
                if not tag_match:
                    text = highlight_bible_refs(para.content, para.bible_refs or [])
                    fragment = f"<p>{text}</p>"

            tag_match = tag_re.match(fragment)
            tag_name = (tag_match.group(1).lower() if tag_match else "p")

            if tag_name == "li":
                if not list_open:
                    html.append("<ul class=\"list\">")
                    list_open = True
                html.append(_inject_attrs(fragment, attrs))
                continue

            if list_open:
                html.append("</ul>")
                list_open = False

            html.append(_inject_attrs(fragment, attrs))
        if list_open:
            html.append("</ul>")
        html.append("</section>")

    html.append("</article>")
    html.append(f"<footer><p>Lesson ID: {lesson.id} | Status: {lesson.status}</p></footer>")
    html.append("</body></html>")

    return "\n".join(html)


def create_html_document_from_nodes(
    metadata_html: str,
    nodes: List[DeanStructuredNode | CourseNoteNode],
    lesson: TranscriptLesson,
    part: TranscriptPart | None,
    chapter: TranscriptChapter | None,
) -> str:
    """Render canonical HTML directly from topical semantic nodes."""
    title = f"Lesson {lesson.lesson_number} - {lesson.speaker}"
    part_title = "" if not part else f"Part {part.part_number}, "
    chapter_title = (
        f"Chapter {chapter.chapter_number}, "
        if chapter and chapter.chapter_number is not None and int(chapter.chapter_number) > 0
        else ""
    )
    html = [
        "<!DOCTYPE html>",
        "<html lang=\"en\">",
        "<head>",
        "  <meta charset=\"UTF-8\">",
        f"  <title>{escape(part_title + chapter_title + title)}</title>",
        "  <style>",
        "    body { font-family: Georgia, serif; line-height: 1.6; max-width: 860px; margin: 40px auto; padding: 0 20px; }",
        "    .metadata { background: #f5f5f5; padding: 15px; margin-bottom: 30px; border-left: 4px solid #333; }",
        "    .section { margin: 30px 0; }",
        "    .node { margin: 8px 0; }",
        "    .list { margin: 10px 0 10px 24px; }",
        "    .hierarchy-1 {}",
        "    .hierarchy-2 { margin-left: 20px; }",
        "    .hierarchy-3 { margin-left: 40px; }",
        "    .hierarchy-4 { margin-left: 60px; }",
        "    .bible-ref { color: #0066cc; font-weight: bold; }",
        "  </style>",
        "</head>",
        "<body>",
        metadata_html,
        "<article class=\"lesson-content\">",
    ]

    tag_re = re.compile(r"^\s*<([a-z0-9]+)\b", re.IGNORECASE)

    def _inject_attrs(fragment: str, attrs: dict[str, str]) -> str:
        marker = " ".join(f'{key}="{escape(value)}"' for key, value in attrs.items())
        match = re.match(r"^\s*<([a-z0-9]+)\b", fragment, re.IGNORECASE)
        if not match:
            return fragment
        tag = match.group(1)
        return re.sub(rf"^\s*<{tag}\b", f"<{tag} {marker}", fragment, count=1, flags=re.IGNORECASE)

    def _list_tag(list_kind: str | None) -> str:
        if (list_kind or "").lower() == "ol":
            return "ol"
        if (list_kind or "").lower() == "alpha":
            return "ol type=\"A\""
        if (list_kind or "").lower() == "roman":
            return "ol type=\"I\""
        return "ul"

    current_root = None
    open_lists: list[tuple[int, str]] = []
    current_root_front_matter = False

    def _close_all_lists() -> None:
        nonlocal open_lists
        while open_lists:
            _depth, tag = open_lists.pop()
            html.append(f"</{tag}>")

    for node in nodes:
        path = node.node_path or "1"
        root = path.split(".")[0] or "1"
        depth = max(1, int(node.depth or len(path.split("."))))
        flags = node.quality_flags if isinstance(node.quality_flags, dict) else {}
        is_front_matter = bool(flags.get("is_front_matter"))

        if current_root != root:
            _close_all_lists()
            if current_root is not None:
                html.append("</section>")
            current_root = root
            current_root_front_matter = is_front_matter
            section_classes = "section hierarchy-1"
            if current_root_front_matter:
                section_classes += " front-matter"
            html.append(
                f"<section class=\"{section_classes}\" data-path=\"{escape(root)}\" "
                f"data-front-matter=\"{'true' if current_root_front_matter else 'false'}\">"
            )

        attrs = {
            "class": "node",
            "data-node-id": str(node.id),
            "data-hierarchy": path,
            "data-node-type": node.node_type,
            "data-front-matter": "true" if is_front_matter else "false",
        }

        if node.node_type == "list":
            while open_lists and open_lists[-1][0] >= depth:
                _depth, tag = open_lists.pop()
                html.append(f"</{tag}>")
            tag_def = _list_tag(node.list_kind)
            tag_name = "ol" if tag_def.startswith("ol") else "ul"
            html.append(f"<{tag_def} class=\"list\" data-hierarchy=\"{escape(path)}\">")
            open_lists.append((depth, tag_name))
            continue

        if node.node_type == "list_item":
            if not open_lists:
                html.append(f"<ul class=\"list\" data-hierarchy=\"{escape(path)}\">")
                open_lists.append((depth, "ul"))
            fragment = (node.text_html or "").strip()
            if not fragment:
                fragment = f"<li>{escape(node.text_plain or '')}</li>"
            html.append(_inject_attrs(fragment, attrs))
            continue

        _close_all_lists()
        fragment = (node.text_html or "").strip()
        if not fragment:
            if node.node_type == "heading":
                level = max(2, min(4, int(node.heading_level or 2)))
                fragment = f"<h{level}>{escape(node.text_plain or '')}</h{level}>"
            elif node.node_type == "blockquote":
                fragment = f"<blockquote>{escape(node.text_plain or '')}</blockquote>"
            elif node.node_type == "table":
                fragment = f"<p>{escape(node.text_plain or '')}</p>"
            else:
                fragment = f"<p>{escape(node.text_plain or '')}</p>"
        html.append(_inject_attrs(fragment, attrs))

    _close_all_lists()
    if current_root is not None:
        html.append("</section>")

    html.append("</article>")
    html.append(f"<footer><p>Lesson ID: {lesson.id} | Status: {lesson.status}</p></footer>")
    html.append("</body></html>")
    return "\n".join(html)


def generate_canonical_html(lesson_id: int, session: Session, require_validated: bool = True) -> Path:
    """Write canonical HTML. require_validated=False regenerates it for an already
    chunked/embedded lesson (e.g. after a metadata repair); content is unchanged."""
    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        raise ValueError(f"Lesson not found: {lesson_id}")
    if require_validated and lesson.status != "validated":
        raise ValueError(f"Lesson {lesson_id} is not validated")

    paragraphs = (
        session.execute(
            select(TranscriptParagraph)
            .where(TranscriptParagraph.lesson_id == lesson_id)
            .order_by(TranscriptParagraph.paragraph_index)
        )
        .scalars()
        .all()
    )

    chapter = None
    part = None
    series = None
    dean_lesson = None
    dean_book = None
    dean_collection = None
    if lesson.chapter_id:
        chapter = session.execute(select(TranscriptChapter).where(TranscriptChapter.id == lesson.chapter_id)).scalar_one_or_none()
        if chapter:
            part = session.execute(select(TranscriptPart).where(TranscriptPart.id == chapter.part_id)).scalar_one_or_none()
    if part is None and lesson.part_id:
        part = session.execute(select(TranscriptPart).where(TranscriptPart.id == lesson.part_id)).scalar_one_or_none()
    if part:
        series = session.execute(select(Series).where(Series.id == part.series_id)).scalar_one_or_none()
    if lesson.dean_lesson_id:
        dean_lesson = session.execute(select(DeanLesson).where(DeanLesson.id == lesson.dean_lesson_id)).scalar_one_or_none()
        if dean_lesson:
            dean_book = session.execute(select(DeanBook).where(DeanBook.id == dean_lesson.book_id)).scalar_one_or_none()
        if dean_book:
            dean_collection = session.execute(
                select(DeanCollection).where(DeanCollection.id == dean_book.collection_id)
            ).scalar_one_or_none()

    metadata_html = build_metadata_html(
        lesson,
        part,
        series,
        chapter,
        dean_lesson=dean_lesson,
        dean_book=dean_book,
        dean_collection=dean_collection,
    )
    config = get_config()
    html_doc: str
    structured_nodes: list[DeanStructuredNode | CourseNoteNode] = []
    if config.course_notes_canonical_from_nodes and is_course_note_node_lesson(lesson, session):
        structured_nodes = load_course_note_nodes(session, lesson.id)
    elif config.dean_topical_canonical_from_nodes and is_dean_node_lesson(lesson, session):
        structured_nodes = load_dean_structured_nodes(session, lesson.id)
    if structured_nodes:
        html_doc = create_html_document_from_nodes(metadata_html, structured_nodes, lesson, part, chapter)
    else:
        sections = group_paragraphs_by_hierarchy(paragraphs)
        html_doc = create_html_document(metadata_html, sections, lesson, part, chapter)

    out_dir = Path(config.canonical_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path, rel_path = _canonical_paths_for_lesson(lesson, session, out_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")

    lesson.canonical_html_path = rel_path
    session.commit()

    return out_path
