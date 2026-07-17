"""Parent chunk creation from validated paragraphs."""

from __future__ import annotations

from typing import List, Any
import re

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    FileTag,
    SourceFile,
    Series,
    TranscriptChapter,
    TranscriptPart,
    DeanBook,
    DeanCollection,
    DeanLesson,
    TranscriptLesson,
    TranscriptParagraph,
    TranscriptParentChunk,
)
from src.processing.course_note_nodes import is_course_note_node_lesson, load_course_note_nodes
from src.processing.extract import _split_long_text
from src.processing.structured_nodes import is_dean_node_lesson, load_dean_structured_nodes
from src.retrieval.citation_utils import build_transcript_citation_ref
from src.utils.logging import setup_logging
from src.utils.config import get_config

logger = structlog.get_logger()
TRANSITION_CUE_PATTERN = re.compile(
    r"^(now|so|but|however|alright|let's|let us|let me|okay|well|therefore|thus)\b",
    re.IGNORECASE,
)
SPEAKER_BOUNDARY_PATTERN = re.compile(r"^[A-Z][A-Z ]{2,}:")
_NODE_TEXT_TYPES = {"heading", "paragraph", "list_item", "blockquote", "note", "table"}


def major_hierarchy_change(path1: str, path2: str) -> bool:
    if not path1 or not path2:
        return False
    major1 = path1.split(".")[0]
    major2 = path2.split(".")[0]
    return major1 != major2


def _starts_with_transition(text: str) -> bool:
    return bool(TRANSITION_CUE_PATTERN.match(text.strip()))


def _is_speaker_boundary(text: str) -> bool:
    return bool(SPEAKER_BOUNDARY_PATTERN.match(text.strip()))


def should_create_chunk_boundary(
    current_chunk: List[TranscriptParagraph],
    next_paragraph: TranscriptParagraph,
    current_word_count: int,
    enable_smart_boundary: bool = False,
) -> bool:
    if not current_chunk:
        return False

    current_path = current_chunk[0].hierarchy_path
    next_path = next_paragraph.hierarchy_path

    if major_hierarchy_change(current_path, next_path):
        return True

    config = get_config()
    if current_word_count + next_paragraph.word_count > config.rag_parent_max_words:
        return True

    if current_word_count < config.rag_parent_min_words:
        return False

    if not enable_smart_boundary:
        if current_word_count > config.rag_parent_min_words and next_path != current_path:
            return True
        return False

    cues = 0
    if next_path != current_path:
        cues += 1
    if _starts_with_transition(next_paragraph.content):
        cues += 1
    if next_paragraph.is_question and current_word_count >= 250:
        cues += 1
    if _is_speaker_boundary(next_paragraph.content):
        cues += 1
    return cues >= 2


def extract_section_title(paragraphs: List[TranscriptParagraph]) -> str:
    if not paragraphs:
        return "Section"

    first = paragraphs[0].content.strip()
    word_count = len(first.split())
    if word_count < 50 and not first.endswith((".", "!", "?")):
        return first

    first_sentence = first.split(".")[0].strip()
    if first_sentence:
        return first_sentence

    path = paragraphs[0].hierarchy_path
    if path:
        return f"Section {path}"

    return " ".join(first.split()[:10])


def build_citation_ref(
    lesson: TranscriptLesson,
    start_idx: int,
    end_idx: int,
    metadata_json: dict[str, Any] | None = None,
) -> str:
    metadata = metadata_json or {}
    if metadata.get("source_tag") == "transcripts":
        return build_transcript_citation_ref(
            lesson=lesson.lesson_number,
            start_para=start_idx,
            end_para=end_idx,
            section_path=metadata.get("section_path"),
            part_number=metadata.get("part_number"),
            part_title=metadata.get("part_title"),
            chapter_number=metadata.get("chapter_number"),
            chapter_title=metadata.get("chapter_title"),
            include_chapter=bool(get_config().transcript_citation_include_chapter),
        )
    return f"Lesson {lesson.lesson_number}, Para {start_idx}-{end_idx}"


def _fetch_source_metadata(lesson: TranscriptLesson, session: Session, source_type: str) -> dict[str, Any]:
    base = (
        select(FileTag.name)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .join(TranscriptLesson, TranscriptLesson.source_file_id == SourceFile.id)
        .where(TranscriptLesson.id == lesson.id)
    )

    if source_type == "dean":
        row = session.execute(
            base.add_columns(
                DeanCollection.name,
                DeanBook.book_slug,
                DeanBook.display_name,
                DeanLesson.lesson_code,
            )
            .join(DeanLesson, TranscriptLesson.dean_lesson_id == DeanLesson.id, isouter=True)
            .join(DeanBook, DeanLesson.book_id == DeanBook.id, isouter=True)
            .join(DeanCollection, DeanBook.collection_id == DeanCollection.id, isouter=True)
        ).first()
        if not row or not row[0] or not str(row[0]).startswith("dean_"):
            return {}
        return {
            "source_tag": row[0],
            "collection": row[1],
            "book_slug": row[2],
            "book_display_name": row[3],
            "lesson_code": row[4],
        }

    if source_type == "course_notes":
        row = session.execute(
            base.add_columns(
                TranscriptPart.part_number,
                TranscriptPart.title,
                Series.title,
            )
            .join(TranscriptChapter, TranscriptLesson.chapter_id == TranscriptChapter.id, isouter=True)
            .join(TranscriptPart, TranscriptChapter.part_id == TranscriptPart.id, isouter=True)
            .join(Series, TranscriptPart.series_id == Series.id, isouter=True)
        ).first()
        if not row:
            return {}
        return {
            "source_tag": row[0],
            "part_number": row[1],
            "part_title": row[2],
            "series_title": row[3],
        }

    if source_type == "transcripts":
        row = session.execute(
            base.add_columns(
                Series.title,
                TranscriptPart.part_number,
                TranscriptPart.title,
                TranscriptChapter.chapter_number,
                TranscriptChapter.title,
            )
            .join(TranscriptChapter, TranscriptLesson.chapter_id == TranscriptChapter.id, isouter=True)
            .join(TranscriptPart, TranscriptChapter.part_id == TranscriptPart.id, isouter=True)
            .join(Series, TranscriptPart.series_id == Series.id, isouter=True)
        ).first()
        if not row:
            return {}
        payload: dict[str, Any] = {
            "source_tag": row[0],
            "series_title": row[1],
            "lesson_number": lesson.lesson_number,
        }
        if row[2] is not None:
            payload["part_number"] = row[2]
        if row[3]:
            payload["part_title"] = row[3]
        if row[4] is not None and int(row[4]) > 0:
            payload["chapter_number"] = row[4]
            if row[5]:
                payload["chapter_title"] = row[5]
        return payload

    raise ValueError(f"Unsupported source_type: {source_type}")


def _detect_source_type(lesson: TranscriptLesson, session: Session) -> str | None:
    source_tag = session.execute(
        select(FileTag.name)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .join(TranscriptLesson, TranscriptLesson.source_file_id == SourceFile.id)
        .where(TranscriptLesson.id == lesson.id)
    ).scalar_one_or_none()
    if source_tag == "course_notes":
        return "course_notes"
    if source_tag == "transcripts":
        return "transcripts"
    if source_tag and str(source_tag).startswith("dean_"):
        return "dean"
    return None


def create_parent_chunk(
    paragraphs: List[TranscriptParagraph],
    lesson: TranscriptLesson,
    session: Session,
    metadata_json: dict[str, Any] | None = None,
) -> str:
    content = "\n\n".join(p.content for p in paragraphs)
    word_count = sum(p.word_count for p in paragraphs)
    section_title = extract_section_title(paragraphs)
    start_idx = paragraphs[0].paragraph_index
    end_idx = paragraphs[-1].paragraph_index
    metadata_payload = dict(metadata_json or {})
    metadata_payload.setdefault("section_path", paragraphs[0].hierarchy_path if paragraphs else None)
    metadata_payload.setdefault("section_title", section_title)
    citation_ref = build_citation_ref(lesson, start_idx, end_idx, metadata_json=metadata_payload)

    chunk = TranscriptParentChunk(
        lesson_id=lesson.id,
        content=content,
        citation_ref=citation_ref,
        start_para_index=start_idx,
        end_para_index=end_idx,
        word_count=word_count,
        section_title=section_title,
        metadata_json=metadata_payload,
    )
    session.add(chunk)
    session.flush()
    return str(chunk.id)


def create_parent_chunk_from_payload(
    lesson: TranscriptLesson,
    content: str,
    section_title: str,
    start_idx: int,
    end_idx: int,
    session: Session,
    metadata_json: dict[str, Any] | None = None,
) -> str:
    word_count = len(content.split())
    metadata_payload = dict(metadata_json or {})
    metadata_payload.setdefault("section_path", metadata_payload.get("section_path"))
    metadata_payload.setdefault("section_title", section_title or "Section")
    citation_ref = build_citation_ref(lesson, start_idx, end_idx, metadata_json=metadata_payload)
    chunk = TranscriptParentChunk(
        lesson_id=lesson.id,
        content=content,
        citation_ref=citation_ref,
        start_para_index=start_idx,
        end_para_index=end_idx,
        word_count=word_count,
        section_title=section_title or "Section",
        metadata_json=metadata_payload,
    )
    session.add(chunk)
    session.flush()
    return str(chunk.id)


def _finalize_node_section_chunks(
    title: str,
    units: list[dict[str, Any]],
    target_words: int,
    max_words: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current_units: list[dict[str, Any]] = []
    current_words = 0

    def flush() -> None:
        nonlocal current_units, current_words
        if not current_units:
            return
        text = "\n\n".join(unit["text"] for unit in current_units if unit["text"].strip()).strip()
        if not text:
            current_units = []
            current_words = 0
            return
        node_paths = sorted({unit["node_path"] for unit in current_units if unit["node_path"]})
        chunks.append(
            {
                "section_title": title,
                "content": text,
                "start_idx": min(unit["start_idx"] for unit in current_units),
                "end_idx": max(unit["end_idx"] for unit in current_units),
                "node_paths": node_paths,
                "page_start": min((unit.get("page_start") or 0) for unit in current_units) or None,
                "page_end": max((unit.get("page_end") or 0) for unit in current_units) or None,
                "is_front_matter": bool(any(unit.get("is_front_matter") for unit in current_units)),
            }
        )
        current_units = []
        current_words = 0

    for unit in units:
        words = len(unit["text"].split())
        if words > max_words:
            pieces = _split_long_text(unit["text"], max_words=max_words)
            for piece in pieces:
                split_unit = {
                    "text": piece,
                    "node_path": unit["node_path"],
                    "start_idx": unit["start_idx"],
                    "end_idx": unit["end_idx"],
                    "page_start": unit.get("page_start"),
                    "page_end": unit.get("page_end"),
                    "is_front_matter": bool(unit.get("is_front_matter")),
                }
                piece_words = len(piece.split())
                if current_units and current_words + piece_words > max_words and current_words >= target_words:
                    flush()
                current_units.append(split_unit)
                current_words += piece_words
            continue

        if current_units and current_words + words > max_words and current_words >= target_words:
            flush()
        current_units.append(unit)
        current_words += words

    flush()

    if len(chunks) >= 2:
        tail_words = len(chunks[-1]["content"].split())
        if tail_words < max(90, target_words // 3):
            prev = chunks[-2]
            tail = chunks[-1]
            prev["content"] = f"{prev['content']}\n\n{tail['content']}".strip()
            prev["end_idx"] = max(prev["end_idx"], tail["end_idx"])
            prev["node_paths"] = sorted(set(prev["node_paths"]) | set(tail["node_paths"]))
            if prev.get("page_start") is None:
                prev["page_start"] = tail.get("page_start")
            elif tail.get("page_start") is not None:
                prev["page_start"] = min(prev["page_start"], tail["page_start"])
            if prev.get("page_end") is None:
                prev["page_end"] = tail.get("page_end")
            elif tail.get("page_end") is not None:
                prev["page_end"] = max(prev["page_end"], tail["page_end"])
            prev["is_front_matter"] = bool(prev.get("is_front_matter") or tail.get("is_front_matter"))
            chunks.pop()

    if len(chunks) > 1:
        for idx, chunk in enumerate(chunks, start=1):
            chunk["section_title"] = f"{title} (Part {idx})"

    return chunks


def _build_node_sections_from_nodes(
    nodes: list[Any],
    target_words: int,
    max_words: int,
) -> list[dict[str, Any]]:
    ordered_nodes = sorted(nodes, key=lambda n: n.order_index)
    sections: list[dict[str, Any]] = []
    current_title: str | None = None
    current_units: list[dict[str, Any]] = []

    def flush_section() -> None:
        nonlocal current_title, current_units
        if not current_units:
            return
        title = current_title or "Section"
        sections.extend(_finalize_node_section_chunks(title, current_units, target_words, max_words))
        current_title = None
        current_units = []

    for node in ordered_nodes:
        if node.node_type not in _NODE_TEXT_TYPES:
            continue
        text = (node.text_plain or "").strip()
        if not text:
            continue

        if node.node_type == "heading":
            flags = node.quality_flags if isinstance(node.quality_flags, dict) else {}
            flush_section()
            current_title = text
            current_units.append(
                {
                    "text": text,
                    "node_path": node.node_path,
                    "start_idx": int(node.order_index),
                    "end_idx": int(node.order_index),
                    "page_start": node.page_start,
                    "page_end": node.page_end,
                    "is_front_matter": bool(flags.get("is_front_matter")),
                }
            )
            continue

        if current_title is None:
            current_title = f"Section {node.node_path.split('.')[0] if node.node_path else '1'}"
        flags = node.quality_flags if isinstance(node.quality_flags, dict) else {}
        current_units.append(
            {
                "text": text,
                "node_path": node.node_path,
                "start_idx": int(node.order_index),
                "end_idx": int(node.order_index),
                "page_start": node.page_start,
                "page_end": node.page_end,
                "is_front_matter": bool(flags.get("is_front_matter")),
            }
        )

    flush_section()
    sections.sort(key=lambda item: item["start_idx"])
    return sections


def merge_into_parent_chunk(
    chunk_id: str, paragraphs: List[TranscriptParagraph], lesson: TranscriptLesson, session: Session
) -> None:
    chunk = session.execute(
        select(TranscriptParentChunk).where(TranscriptParentChunk.id == chunk_id)
    ).scalar_one()
    append_text = "\n\n".join(p.content for p in paragraphs)
    chunk.content = f"{chunk.content}\n\n{append_text}"
    chunk.end_para_index = paragraphs[-1].paragraph_index
    chunk.word_count = chunk.word_count + sum(p.word_count for p in paragraphs)
    chunk.citation_ref = build_citation_ref(
        lesson,
        chunk.start_para_index,
        chunk.end_para_index,
        metadata_json=chunk.metadata_json or {},
    )


def _resolve_source_metadata(lesson: TranscriptLesson, session: Session) -> dict[str, Any]:
    source_type = _detect_source_type(lesson, session)
    if not source_type:
        return {}
    return _fetch_source_metadata(lesson, session, source_type)


def _average_parent_chunk_size(lesson_id: int, session: Session) -> float:
    sizes = (
        session.execute(
            select(TranscriptParentChunk.word_count).where(TranscriptParentChunk.lesson_id == lesson_id)
        )
        .scalars()
        .all()
    )
    return (sum(sizes) / len(sizes)) if sizes else 0.0


def _chunk_node_aware(
    lesson: TranscriptLesson,
    session: Session,
    source_meta: dict[str, Any],
    config,
    use_course_note_node_aware: bool,
    log,
) -> dict:
    nodes = (
        load_course_note_nodes(session, lesson.id)
        if use_course_note_node_aware
        else load_dean_structured_nodes(session, lesson.id)
    )
    sections = _build_node_sections_from_nodes(
        nodes,
        target_words=max(120, int(config.dean_chunk_parent_node_target_words)),
        max_words=max(180, int(config.dean_chunk_parent_node_max_words)),
    )
    if not sections:
        return {
            "status": "error",
            "parent_chunks_created": 0,
            "avg_chunk_size": 0,
            "message": "No structured node sections found",
        }

    chunk_ids: list[str] = []
    for section in sections:
        section_meta = {
            **source_meta,
            "is_node_aware": True,
            "node_paths": section["node_paths"],
            "section_path": section["node_paths"][0] if section.get("node_paths") else None,
            "page_start": section.get("page_start"),
            "page_end": section.get("page_end"),
            "is_front_matter": bool(section.get("is_front_matter", False)),
            "section_title": section["section_title"],
        }
        chunk_ids.append(
            create_parent_chunk_from_payload(
                lesson=lesson,
                content=section["content"],
                section_title=section["section_title"],
                start_idx=section["start_idx"],
                end_idx=section["end_idx"],
                session=session,
                metadata_json=section_meta,
            )
        )

    lesson.status = "chunked"
    session.commit()
    avg_size = _average_parent_chunk_size(lesson.id, session)
    log.info(
        "parent_chunking_complete_node_aware",
        count=len(chunk_ids),
        avg_size=avg_size,
        retrieval_mode="node_aware",
        source_tag=source_meta.get("source_tag"),
    )
    return {
        "status": "success",
        "parent_chunks_created": len(chunk_ids),
        "avg_chunk_size": avg_size,
        "message": f"Created {len(chunk_ids)} node-aware parent chunks",
    }


def _chunk_paragraph_based(
    lesson: TranscriptLesson,
    lesson_id: int,
    session: Session,
    source_meta: dict[str, Any],
    config,
    log,
) -> dict:
    paragraphs = (
        session.execute(
            select(TranscriptParagraph)
            .where(TranscriptParagraph.lesson_id == lesson_id)
            .order_by(TranscriptParagraph.paragraph_index)
        )
        .scalars()
        .all()
    )
    if not paragraphs:
        return {"status": "error", "parent_chunks_created": 0, "avg_chunk_size": 0, "message": "No paragraphs found"}

    current_chunk: list[TranscriptParagraph] = []
    current_word_count = 0
    chunk_ids: list[str] = []

    for para in paragraphs:
        if should_create_chunk_boundary(
            current_chunk,
            para,
            current_word_count,
            enable_smart_boundary=config.rag_chunk_parent_smart_boundary,
        ):
            chunk_meta = {
                **source_meta,
                "is_node_aware": False,
                "section_path": current_chunk[0].hierarchy_path if current_chunk else None,
                "section_title": extract_section_title(current_chunk) if current_chunk else "Section",
            }
            chunk_ids.append(create_parent_chunk(current_chunk, lesson, session, metadata_json=chunk_meta))
            current_chunk = [para]
            current_word_count = para.word_count
        else:
            current_chunk.append(para)
            current_word_count += para.word_count

    if current_chunk:
        if current_word_count < config.rag_parent_tail_min_words and chunk_ids:
            prev_chunk = session.execute(
                select(TranscriptParentChunk).where(TranscriptParentChunk.id == chunk_ids[-1])
            ).scalar_one()
            prev_path = session.execute(
                select(TranscriptParagraph.hierarchy_path).where(
                    TranscriptParagraph.lesson_id == lesson.id,
                    TranscriptParagraph.paragraph_index == prev_chunk.end_para_index,
                )
            ).scalar_one_or_none()
            if prev_path == current_chunk[0].hierarchy_path:
                merge_into_parent_chunk(chunk_ids[-1], current_chunk, lesson, session)
            else:
                chunk_meta = {
                    **source_meta,
                    "is_node_aware": False,
                    "section_path": current_chunk[0].hierarchy_path,
                    "section_title": extract_section_title(current_chunk),
                }
                chunk_ids.append(create_parent_chunk(current_chunk, lesson, session, metadata_json=chunk_meta))
        else:
            chunk_meta = {
                **source_meta,
                "is_node_aware": False,
                "section_path": current_chunk[0].hierarchy_path,
                "section_title": extract_section_title(current_chunk),
            }
            chunk_ids.append(create_parent_chunk(current_chunk, lesson, session, metadata_json=chunk_meta))

    lesson.status = "chunked"
    session.commit()
    avg_size = _average_parent_chunk_size(lesson.id, session) if chunk_ids else 0
    log.info(
        "parent_chunking_complete",
        count=len(chunk_ids),
        avg_size=avg_size,
        experiment_tag=config.rag_experiment_tag,
        smart_boundary=config.rag_chunk_parent_smart_boundary,
    )
    return {
        "status": "success",
        "parent_chunks_created": len(chunk_ids),
        "avg_chunk_size": avg_size,
        "message": f"Created {len(chunk_ids)} parent chunks from {len(paragraphs)} paragraphs",
    }


def process_lesson_chunking(lesson_id: int, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="process_lesson_chunking", lesson_id=lesson_id)
    config = get_config()

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        return {"status": "error", "parent_chunks_created": 0, "avg_chunk_size": 0, "message": "Lesson not found"}
    if lesson.status != "validated":
        return {"status": "error", "parent_chunks_created": 0, "avg_chunk_size": 0, "message": "Lesson not validated"}

    existing = (
        session.execute(select(TranscriptParentChunk.id).where(TranscriptParentChunk.lesson_id == lesson.id)).first()
    )
    if existing:
        return {"status": "error", "parent_chunks_created": 0, "avg_chunk_size": 0, "message": "Parent chunks already exist"}

    source_meta = _resolve_source_metadata(lesson, session)
    use_dean_node_aware = bool(
        config.dean_topical_retrieval_mode == "node_aware"
        and config.dean_topical_node_mode
        and is_dean_node_lesson(lesson, session)
    )
    use_course_note_node_aware = bool(config.course_notes_node_mode and is_course_note_node_lesson(lesson, session))
    if use_dean_node_aware or use_course_note_node_aware:
        return _chunk_node_aware(
            lesson=lesson,
            session=session,
            source_meta=source_meta,
            config=config,
            use_course_note_node_aware=use_course_note_node_aware,
            log=log,
        )

    return _chunk_paragraph_based(
        lesson=lesson,
        lesson_id=lesson_id,
        session=session,
        source_meta=source_meta,
        config=config,
        log=log,
    )
