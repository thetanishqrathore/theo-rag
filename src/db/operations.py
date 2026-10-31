"""Database operations for ingestion."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
import structlog

from src.db.models import (
    Corpus,
    FileTag,
    SourceFile,
    Series,
    TranscriptPart,
    TranscriptChapter,
    TranscriptLesson,
    DeanCollection,
    DeanBook,
    DeanLesson,
)

logger = structlog.get_logger()


def check_duplicate_checksum(session: Session, checksum: str) -> str | None:
    """Check if source file with this checksum exists."""
    if not checksum:
        raise ValueError("Checksum is required")
    result = session.execute(select(SourceFile.id).where(SourceFile.checksum == checksum)).first()
    if result:
        logger.info("duplicate_checksum_found", source_file_id=str(result[0]), checksum=checksum[:8])
        return str(result[0])
    return None


def _get_or_create_id(
    session: Session,
    model,
    insert_values: dict[str, Any],
    conflict_fields: tuple[str, ...],
    lookup_values: dict[str, Any],
    created_event: str | None = None,
    created_fields: dict[str, Any] | None = None,
) -> int:
    stmt = (
        insert(model)
        .values(**insert_values)
        .on_conflict_do_nothing(index_elements=[getattr(model, field) for field in conflict_fields])
        .returning(model.id)
    )
    result = session.execute(stmt).scalar_one_or_none()
    if result is not None:
        if created_event:
            logger.info(created_event, **(created_fields or {}))
        return int(result)
    filters = [getattr(model, field) == value for field, value in lookup_values.items()]
    existing = session.execute(select(model.id).where(*filters)).scalar_one()
    return int(existing)


def get_or_create_series(session: Session, corpus_name: str, series_name: str) -> int:
    corpus = session.execute(select(Corpus).where(Corpus.name == corpus_name)).scalar_one_or_none()
    if not corpus:
        raise ValueError(f"Corpus not found: {corpus_name}")
    return _get_or_create_id(
        session=session,
        model=Series,
        insert_values={"corpus_id": corpus.id, "title": series_name},
        conflict_fields=("corpus_id", "title"),
        lookup_values={"corpus_id": corpus.id, "title": series_name},
        created_event="series_created",
        created_fields={"series_name": series_name},
    )


def get_or_create_part(session: Session, series_id: int, part_number: int, part_title: str) -> int:
    if part_number <= 0:
        raise ValueError("part_number must be positive")
    return _get_or_create_id(
        session=session,
        model=TranscriptPart,
        insert_values={"series_id": series_id, "part_number": part_number, "title": part_title},
        conflict_fields=("series_id", "part_number"),
        lookup_values={"series_id": series_id, "part_number": part_number},
        created_event="part_created",
        created_fields={"part_number": part_number},
    )


def get_or_create_chapter(
    session: Session, part_id: int, chapter_number: int, chapter_title: str
) -> int:
    return _get_or_create_id(
        session=session,
        model=TranscriptChapter,
        insert_values={"part_id": part_id, "chapter_number": chapter_number, "title": chapter_title},
        conflict_fields=("part_id", "chapter_number"),
        lookup_values={"part_id": part_id, "chapter_number": chapter_number},
        created_event="chapter_created",
        created_fields={"chapter_number": chapter_number},
    )


def get_file_tag_id(session: Session, corpus_name: str, tag_name: str) -> int:
    row = session.execute(
        select(FileTag.id)
        .join(Corpus, FileTag.corpus_id == Corpus.id)
        .where(Corpus.name == corpus_name, FileTag.name == tag_name)
    ).first()
    if row:
        return row[0]
    raise ValueError(f"File tag '{tag_name}' not found for corpus '{corpus_name}'.")


def insert_source_file(
    session: Session,
    file_tag_id: int,
    filename: str,
    raw_path: str,
    checksum: str,
    ingestion_status: str = "pending",
    source_format: str | None = None,
    source_kind: str | None = None,
    ingest_exclude_reason: str | None = None,
    error_log: str | None = None,
) -> str:
    source_file = SourceFile(
        file_tag_id=file_tag_id,
        filename=filename,
        raw_path=raw_path,
        checksum=checksum,
        ingestion_status=ingestion_status,
        source_format=source_format,
        source_kind=source_kind,
        ingest_exclude_reason=ingest_exclude_reason,
        error_log=error_log,
    )
    session.add(source_file)
    session.flush()
    return str(source_file.id)


def insert_transcript_lesson(
    session: Session,
    source_file_id: str,
    chapter_id: int | None,
    lesson_number: int,
    metadata: dict,
    source_unit_key: str = "root",
    dean_lesson_id: int | None = None,
    part_id: int | None = None,
) -> int:
    lesson = TranscriptLesson(
        source_file_id=source_file_id,
        source_unit_key=source_unit_key,
        chapter_id=chapter_id,
        part_id=part_id,
        dean_lesson_id=dean_lesson_id,
        lesson_number=lesson_number,
        title=metadata.get("lesson_title"),
        date=metadata.get("date"),
        speaker=metadata["speaker"],
        location=metadata.get("location"),
        transcriber=metadata.get("transcriber"),
        status="pending",
    )
    session.add(lesson)
    session.flush()
    logger.info("lesson_inserted", lesson_id=lesson.id, lesson_number=lesson.lesson_number)
    return lesson.id


def get_or_create_dean_collection(session: Session, corpus_name: str, name: str, source_root: str) -> int:
    corpus = session.execute(select(Corpus).where(Corpus.name == corpus_name)).scalar_one_or_none()
    if not corpus:
        raise ValueError(f"Corpus not found: {corpus_name}")
    return _get_or_create_id(
        session=session,
        model=DeanCollection,
        insert_values={"corpus_id": corpus.id, "name": name, "source_root": source_root},
        conflict_fields=("corpus_id", "name"),
        lookup_values={"corpus_id": corpus.id, "name": name},
    )


def get_or_create_dean_book(
    session: Session,
    collection_id: int,
    book_slug: str,
    display_name: str,
    parser_profile: str = "default",
) -> int:
    return _get_or_create_id(
        session=session,
        model=DeanBook,
        insert_values={
            "collection_id": collection_id,
            "book_slug": book_slug,
            "display_name": display_name,
            "parser_profile": parser_profile,
        },
        conflict_fields=("collection_id", "book_slug"),
        lookup_values={"collection_id": collection_id, "book_slug": book_slug},
    )


def insert_dean_lesson(
    session: Session,
    source_file_id: str,
    book_id: int,
    lesson_number: int,
    lesson_code: str | None,
    title: str | None,
    source_filename: str,
    source_rel_path: str,
    speaker: str,
    date=None,
    parser_version: str = "v1",
    metadata_json: dict | None = None,
) -> int:
    dean_lesson = DeanLesson(
        source_file_id=source_file_id,
        book_id=book_id,
        lesson_number=lesson_number,
        lesson_code=lesson_code,
        title=title,
        source_filename=source_filename,
        source_rel_path=source_rel_path,
        date=date,
        speaker=speaker,
        parser_version=parser_version,
        metadata_json=metadata_json,
    )
    session.add(dean_lesson)
    session.flush()
    return dean_lesson.id
