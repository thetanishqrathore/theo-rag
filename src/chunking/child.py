"""Child chunk creation for retrieval."""

from __future__ import annotations

from typing import List, Set
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import TranscriptLesson, TranscriptParentChunk, TranscriptChildChunk, Corpus, FileTag, SourceFile
from src.processing.structured_nodes import DEAN_NODE_TAGS
from src.utils.logging import setup_logging
from src.utils.config import get_config

logger = structlog.get_logger()

DEFAULT_CHILD_TARGET = 160
DEFAULT_CHILD_OVERLAP = 25
DEFAULT_CHILD_MIN = 90
DEFAULT_CHILD_MAX = 240
DEFAULT_SNAP_WINDOW = 30


def _clean_boundary_word(word: str) -> str:
    return word.rstrip("\"'”’)]}")


def _clean_next_word(word: str) -> str:
    return word.lstrip("\"'“‘([{")


def _is_sentence_boundary(word: str, next_word: str) -> bool:
    cleaned = _clean_boundary_word(word)
    if not cleaned.endswith((".", "?", "!", ":", ";")):
        return False
    next_clean = _clean_next_word(next_word)
    return next_clean[:1].isupper()


def _extract_words_and_boundaries(text: str) -> tuple[List[str], Set[int]]:
    words: list[str] = []
    boundaries: set[int] = set()
    offset = 0
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        para_words = para.split()
        if not para_words:
            continue
        for idx in range(len(para_words) - 1):
            if _is_sentence_boundary(para_words[idx], para_words[idx + 1]):
                boundaries.add(offset + idx)
        boundaries.add(offset + len(para_words) - 1)
        words.extend(para_words)
        offset += len(para_words)
    return words, boundaries


def create_sliding_window_chunks(
    text: str,
    window_size: int = DEFAULT_CHILD_TARGET,
    overlap: int = DEFAULT_CHILD_OVERLAP,
    min_size: int = DEFAULT_CHILD_MIN,
    max_size: int = DEFAULT_CHILD_MAX,
    snap_window: int = DEFAULT_SNAP_WINDOW,
    enable_sentence_snap: bool = False,
) -> List[str]:
    words = text.split()
    chunks: list[str] = []
    if not words:
        return chunks

    start = 0
    while start < len(words):
        end = min(start + window_size, len(words))
        chunk_words = words[start:end]
        if len(chunk_words) < min_size and chunks:
            chunks[-1] = chunks[-1] + " " + " ".join(chunk_words)
            break
        chunks.append(" ".join(chunk_words))
        if end == len(words):
            break
        start = max(end - overlap, 0)
    return chunks


def create_sentence_snap_chunks(
    text: str,
    target_size: int = DEFAULT_CHILD_TARGET,
    overlap: int = DEFAULT_CHILD_OVERLAP,
    min_size: int = DEFAULT_CHILD_MIN,
    max_size: int = DEFAULT_CHILD_MAX,
    snap_window: int = DEFAULT_SNAP_WINDOW,
) -> List[str]:
    words, boundaries = _extract_words_and_boundaries(text)
    chunks: list[str] = []
    if not words:
        return chunks

    start = 0
    total_words = len(words)
    while start < total_words:
        remaining = total_words - start
        if remaining <= min_size and chunks:
            chunks[-1] = chunks[-1] + " " + " ".join(words[start:])
            break

        ideal_end = min(start + target_size, total_words)
        if ideal_end - start > max_size:
            ideal_end = start + max_size

        window_start = max(start + min_size - 1, ideal_end - snap_window)
        window_end = min(total_words - 1, ideal_end + snap_window)

        snap_end = None
        if boundaries:
            candidates = [b for b in boundaries if window_start <= b <= window_end]
            if candidates:
                snap_end = min(candidates, key=lambda b: abs((b + 1) - ideal_end))

        if snap_end is None:
            end = min(start + max_size, total_words)
        else:
            end = snap_end + 1

        if end <= start:
            end = min(start + max_size, total_words)

        chunk_words = words[start:end]
        if len(chunk_words) < min_size and chunks:
            chunks[-1] = chunks[-1] + " " + " ".join(chunk_words)
            break

        chunks.append(" ".join(chunk_words))
        if end >= total_words:
            break
        start = max(end - overlap, 0)

    return chunks


def create_node_group_chunks(
    text: str,
    target_size: int = DEFAULT_CHILD_TARGET,
    min_size: int = DEFAULT_CHILD_MIN,
    max_size: int = DEFAULT_CHILD_MAX,
) -> List[str]:
    """Chunk by semantic block boundaries before falling back to token windows."""
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    if not blocks:
        return []

    chunks: list[str] = []
    current_blocks: list[str] = []
    current_words = 0
    for block in blocks:
        words = block.split()
        block_count = len(words)
        if block_count > max_size:
            if current_blocks:
                chunks.append("\n\n".join(current_blocks))
                current_blocks = []
                current_words = 0
            start = 0
            while start < block_count:
                end = min(start + target_size, block_count)
                piece = " ".join(words[start:end]).strip()
                if piece:
                    chunks.append(piece)
                start = end
            continue
        if current_blocks and current_words + block_count > max_size:
            chunks.append("\n\n".join(current_blocks))
            current_blocks = [block]
            current_words = block_count
            continue
        current_blocks.append(block)
        current_words += block_count
        if current_words >= target_size:
            chunks.append("\n\n".join(current_blocks))
            current_blocks = []
            current_words = 0

    if current_blocks:
        tail = "\n\n".join(current_blocks)
        if chunks and len(tail.split()) < min_size:
            chunks[-1] = f"{chunks[-1]}\n\n{tail}"
        else:
            chunks.append(tail)
    return [chunk for chunk in chunks if chunk.strip()]


def create_child_chunks_for_parent(parent_id: str, parent_text: str, session: Session) -> List[str]:
    config = get_config()
    parent_meta_row = session.execute(
        select(
            FileTag.name,
            TranscriptParentChunk.section_title,
            TranscriptParentChunk.metadata_json,
        )
        .join(TranscriptLesson, TranscriptParentChunk.lesson_id == TranscriptLesson.id)
        .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
        .join(FileTag, SourceFile.file_tag_id == FileTag.id)
        .where(TranscriptParentChunk.id == parent_id)
    ).first()
    parent_tag = parent_meta_row[0] if parent_meta_row else None
    parent_section = parent_meta_row[1] if parent_meta_row else None
    parent_metadata = parent_meta_row[2] if parent_meta_row and parent_meta_row[2] else {}
    use_node_aware = bool(
        (
            config.dean_topical_node_mode
            and config.dean_topical_retrieval_mode == "node_aware"
            and (parent_tag or "") in DEAN_NODE_TAGS
        )
        or (
            config.course_notes_node_mode
            and (parent_tag or "") == "course_notes"
        )
    )
    corpus_id = session.execute(
        select(Corpus.id)
        .join(FileTag, FileTag.corpus_id == Corpus.id)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .join(TranscriptLesson, TranscriptLesson.source_file_id == SourceFile.id)
        .join(TranscriptParentChunk, TranscriptParentChunk.lesson_id == TranscriptLesson.id)
        .where(TranscriptParentChunk.id == parent_id)
    ).scalar_one_or_none()
    if corpus_id is None:
        corpus_id = session.execute(select(Corpus.id).where(Corpus.name == get_config().corpus_name)).scalar_one()
    min_size = max(10, config.rag_child_tail_min_words)
    if use_node_aware:
        chunks = create_node_group_chunks(
            parent_text,
            target_size=config.rag_child_size_words,
            min_size=min_size,
            max_size=config.rag_child_max_words,
        )
    elif config.rag_chunk_child_sentence_snap:
        chunks = create_sentence_snap_chunks(
            parent_text,
            target_size=config.rag_child_size_words,
            overlap=config.rag_child_overlap_words,
            min_size=min_size,
            max_size=config.rag_child_max_words,
            snap_window=config.rag_child_snap_window,
        )
    else:
        chunks = create_sliding_window_chunks(
            parent_text,
            window_size=config.rag_child_size_words,
            overlap=config.rag_child_overlap_words,
            min_size=min_size,
            max_size=config.rag_child_max_words,
        )
    heading_prefix = ""
    if use_node_aware and config.dean_chunk_child_heading_prefix and parent_section:
        heading_prefix = f"Section: {parent_section.strip()}\n\n"

    ids: list[str] = []
    children: list[TranscriptChildChunk] = []
    for idx, chunk_text in enumerate(chunks):
        content = f"{heading_prefix}{chunk_text}" if heading_prefix else chunk_text
        node_paths = []
        if isinstance(parent_metadata, dict):
            node_paths = list(parent_metadata.get("node_paths") or [])
        metadata_payload = {
            "source_tag": parent_tag,
            "parent_section": parent_section,
            "section_path": parent_metadata.get("section_path") if isinstance(parent_metadata, dict) else None,
            "is_node_aware": use_node_aware,
            "node_path": node_paths[0] if node_paths else None,
            "is_front_matter": bool(parent_metadata.get("is_front_matter", False)) if isinstance(parent_metadata, dict) else False,
        }
        if isinstance(parent_metadata, dict):
            if parent_metadata.get("chapter_number") is not None:
                metadata_payload["chapter_number"] = parent_metadata.get("chapter_number")
            if parent_metadata.get("chapter_title"):
                metadata_payload["chapter_title"] = parent_metadata.get("chapter_title")
            if parent_metadata.get("page_start") is not None:
                metadata_payload["page_start"] = parent_metadata.get("page_start")
            if parent_metadata.get("page_end") is not None:
                metadata_payload["page_end"] = parent_metadata.get("page_end")
        child_id = uuid.uuid4()
        child = TranscriptChildChunk(
            id=child_id,
            parent_chunk_id=parent_id,
            content=content,
            chunk_index=idx,
            word_count=len(content.split()),
            corpus_id=corpus_id,
            metadata_json=metadata_payload,
        )
        children.append(child)
        ids.append(str(child_id))
    if children:
        session.add_all(children)
        session.flush()
    return ids


def process_lesson_child_chunking(lesson_id: int, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="process_lesson_child_chunking", lesson_id=lesson_id)

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        return {"status": "error", "parents": 0, "children": 0, "message": "Lesson not found"}
    if lesson.status != "chunked":
        return {"status": "error", "parents": 0, "children": 0, "message": "Lesson not chunked"}

    parents = (
        session.execute(
            select(TranscriptParentChunk.id, TranscriptParentChunk.content)
            .where(TranscriptParentChunk.lesson_id == lesson_id)
        )
        .all()
    )
    if not parents:
        return {"status": "error", "parents": 0, "children": 0, "message": "No parent chunks found"}

    total_children = 0
    mid_sentence_cuts = 0
    for parent_id, parent_text in parents:
        child_ids = create_child_chunks_for_parent(str(parent_id), parent_text, session)
        chunks = (
            session.execute(
                select(TranscriptChildChunk.content)
                .where(TranscriptChildChunk.parent_chunk_id == parent_id)
                .order_by(TranscriptChildChunk.chunk_index)
            )
            .scalars()
            .all()
        )
        for chunk in chunks:
            last_word = chunk.rstrip().split()[-1] if chunk else ""
            if not last_word.endswith((".", "?", "!", ":", ";")):
                mid_sentence_cuts += 1
        total_children += len(child_ids)

    lesson.status = "child_chunked"
    session.commit()

    config = get_config()
    cut_rate = (mid_sentence_cuts / total_children) if total_children else 0
    log.info(
        "child_chunking_complete",
        parents=len(parents),
        children=total_children,
        mid_sentence_cut_rate=round(cut_rate, 4),
        experiment_tag=config.rag_experiment_tag,
        sentence_snap=config.rag_chunk_child_sentence_snap,
    )
    return {
        "status": "success",
        "parents": len(parents),
        "children": total_children,
        "message": f"Created {total_children} child chunks from {len(parents)} parents",
    }
