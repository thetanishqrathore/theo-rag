"""SQLAlchemy ORM models for the RAG database."""

import os
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Float,
    BigInteger,
    Index,
    Integer,
    String,
    Text,
    DateTime,
    text,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


def _embedding_vector_dim() -> int:
    raw = os.getenv("QDRANT_EXPECTED_DIM", "1024")
    try:
        dim = int(raw)
    except Exception:
        return 1024
    return dim if dim > 0 else 1024


EMBEDDING_VECTOR_DIM = _embedding_vector_dim()


class Corpus(Base):
    """Corpus isolation boundary."""

    __tablename__ = "corpus"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(Text)


class FileTag(Base):
    """File tag registry."""

    __tablename__ = "file_tags"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    corpus_id = Column(Integer, ForeignKey("corpus.id"), nullable=False)


class SourceFile(Base):
    """Physical source file registry."""

    __tablename__ = "source_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_tag_id = Column(Integer, ForeignKey("file_tags.id"), nullable=False)
    filename = Column(Text, nullable=False)
    raw_path = Column(Text, nullable=False)
    checksum = Column(Text, nullable=False, unique=True)
    ingestion_status = Column(String(20), nullable=False, default="pending")
    source_format = Column(String(20))
    source_kind = Column(String(50))
    ingest_exclude_reason = Column(Text)
    error_log = Column(Text)


class Series(Base):
    """Teaching series within a corpus."""

    __tablename__ = "series"

    id = Column(Integer, primary_key=True)
    title = Column(Text, nullable=False)
    corpus_id = Column(Integer, ForeignKey("corpus.id"), nullable=False)


class TranscriptPart(Base):
    """Transcript parts within a series."""

    __tablename__ = "parts"

    id = Column(Integer, primary_key=True)
    series_id = Column(Integer, ForeignKey("series.id"), nullable=False)
    part_number = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)


class TranscriptChapter(Base):
    """Transcript chapters within a part."""

    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True)
    part_id = Column(Integer, ForeignKey("parts.id"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)


class TranscriptLesson(Base):
    """Transcript lesson records."""

    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True)
    source_file_id = Column(UUID(as_uuid=True), ForeignKey("source_files.id"), nullable=False)
    source_unit_key = Column(Text, nullable=False, server_default=text("'root'"))
    chapter_id = Column(Integer, ForeignKey("chapters.id"))
    # Set even when chapter_id is NULL, so series/part attribution never depends on a chapter.
    part_id = Column(Integer, ForeignKey("parts.id"))
    dean_lesson_id = Column(Integer, ForeignKey("dean_lessons.id"), unique=True)
    lesson_number = Column(Integer, nullable=False)
    title = Column(Text)
    date = Column(Date)
    speaker = Column(Text, nullable=False)
    location = Column(Text)
    transcriber = Column(Text)
    status = Column(String(50), default="pending")
    validation_report = Column(JSONB)
    canonical_html_path = Column(Text)


class TranscriptParagraph(Base):
    """Atomic content units."""

    __tablename__ = "paragraphs"

    id = Column(Integer, primary_key=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    paragraph_index = Column(Integer, nullable=False)
    hierarchy_path = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    html_content = Column(Text)
    is_junk = Column(Boolean, default=False)
    is_question = Column(Boolean, default=False)
    bible_refs = Column(ARRAY(Text))
    word_count = Column(Integer, nullable=False)


class TranscriptParentChunk(Base):
    """Parent chunks for citation."""

    __tablename__ = "parent_chunks"
    __table_args__ = (Index("idx_parent_chunks_lesson", "lesson_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    content = Column(Text, nullable=False)
    citation_ref = Column(Text, nullable=False)
    start_para_index = Column(Integer, nullable=False)
    end_para_index = Column(Integer, nullable=False)
    word_count = Column(Integer, nullable=False)
    section_title = Column(Text)
    metadata_json = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class TranscriptChildChunk(Base):
    """Child chunks for retrieval."""

    __tablename__ = "child_chunks"
    __table_args__ = (Index("idx_child_chunks_parent", "parent_chunk_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_chunk_id = Column(UUID(as_uuid=True), ForeignKey("parent_chunks.id"), nullable=False)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    word_count = Column(Integer, nullable=False)
    embedding = Column(Vector(EMBEDDING_VECTOR_DIM), nullable=True)
    corpus_id = Column(Integer, ForeignKey("corpus.id"))
    metadata_json = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class TranscriptChildChunkAudit(Base):
    """Embedding audit metadata for child chunks."""

    __tablename__ = "child_chunk_audit"

    child_chunk_id = Column(UUID(as_uuid=True), ForeignKey("child_chunks.id"), primary_key=True)
    content_hash = Column(String(64), nullable=False)
    char_len = Column(Integer, nullable=False)
    word_count = Column(Integer, nullable=False)
    token_est = Column(Integer, nullable=False)
    embed_model = Column(Text, nullable=False)
    embed_dim = Column(Integer, nullable=False)
    embed_version = Column(String(20), nullable=False)
    experiment_tag = Column(String(50), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class DeanCollection(Base):
    """Top-level Dean collection buckets."""

    __tablename__ = "dean_collections"

    id = Column(Integer, primary_key=True)
    corpus_id = Column(Integer, ForeignKey("corpus.id"), nullable=False)
    name = Column(String(50), nullable=False)
    source_root = Column(Text, nullable=False)


class DeanBook(Base):
    """Dean book/topic folders inside a collection."""

    __tablename__ = "dean_books"

    id = Column(Integer, primary_key=True)
    collection_id = Column(Integer, ForeignKey("dean_collections.id"), nullable=False)
    book_slug = Column(String(100), nullable=False)
    display_name = Column(Text, nullable=False)
    parser_profile = Column(String(100), nullable=False, default="default")


class DeanLesson(Base):
    """Normalized Dean lesson metadata per source file."""

    __tablename__ = "dean_lessons"

    id = Column(Integer, primary_key=True)
    source_file_id = Column(UUID(as_uuid=True), ForeignKey("source_files.id"), nullable=False, unique=True)
    book_id = Column(Integer, ForeignKey("dean_books.id"), nullable=False)
    lesson_number = Column(Integer, nullable=False)
    lesson_code = Column(String(64))
    title = Column(Text)
    source_filename = Column(Text, nullable=False)
    source_rel_path = Column(Text, nullable=False)
    date = Column(Date)
    speaker = Column(Text, nullable=False)
    parser_version = Column(String(32), nullable=False, default="v1")
    metadata_json = Column(JSONB)


class DeanStructuredDoc(Base):
    """Dean structured document-level extraction diagnostics."""

    __tablename__ = "dean_structured_docs"

    lesson_id = Column(Integer, ForeignKey("lessons.id"), primary_key=True)
    source_file_id = Column(UUID(as_uuid=True), ForeignKey("source_files.id"), nullable=False)
    parser_profile = Column(Text)
    selected_engine = Column(Text)
    structure_quality_score = Column(Float)
    scan_confidence = Column(Float)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class DeanStructuredNode(Base):
    """Semantic node tree for structured Dean lessons."""

    __tablename__ = "dean_structured_nodes"

    id = Column(BigInteger, primary_key=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    parent_node_id = Column(BigInteger, ForeignKey("dean_structured_nodes.id"))
    node_path = Column(Text, nullable=False)
    node_type = Column(Text, nullable=False)
    depth = Column(Integer, nullable=False)
    order_index = Column(Integer, nullable=False)
    heading_level = Column(Integer)
    list_kind = Column(Text)
    list_ordinal = Column(Integer)
    text_plain = Column(Text, nullable=False)
    text_html = Column(Text)
    text_markdown = Column(Text)
    page_start = Column(Integer)
    page_end = Column(Integer)
    bbox_json = Column(JSONB)
    quality_flags = Column(JSONB)


class DeanStructuredTable(Base):
    """Normalized table payload linked to structured Dean nodes."""

    __tablename__ = "dean_structured_tables"

    id = Column(BigInteger, primary_key=True)
    node_id = Column(BigInteger, ForeignKey("dean_structured_nodes.id"), nullable=False)
    table_json = Column(JSONB, nullable=False)
    table_html = Column(Text)


# Backward-compatibility aliases retained in Python layer.
DeanTopicalPdfDoc = DeanStructuredDoc
DeanTopicalPdfNode = DeanStructuredNode
DeanTopicalPdfTable = DeanStructuredTable


class CourseNoteVolume(Base):
    """Physical course-notes PDF volume metadata."""

    __tablename__ = "course_note_volumes"

    id = Column(Integer, primary_key=True)
    source_file_id = Column(UUID(as_uuid=True), ForeignKey("source_files.id"), nullable=False, unique=True)
    series_id = Column(Integer, ForeignKey("series.id"), nullable=False)
    part_id = Column(Integer, ForeignKey("parts.id"), nullable=False)
    part_number = Column(Integer, nullable=False)
    part_title = Column(Text, nullable=False)
    volume_code = Column(String(64), nullable=False)
    page_count = Column(Integer, nullable=False)
    metadata_json = Column(JSONB)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class CourseNoteUnit(Base):
    """Logical lesson unit boundaries inside a course-notes PDF volume."""

    __tablename__ = "course_note_units"

    id = Column(Integer, primary_key=True)
    source_file_id = Column(UUID(as_uuid=True), ForeignKey("source_files.id"), nullable=False)
    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    logical_lesson_number = Column(Integer, nullable=False)
    unit_title = Column(Text)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    boundary_confidence = Column(Float, nullable=False, server_default=text("0.0"))
    metadata_json = Column(JSONB)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class CourseNoteNode(Base):
    """Semantic node tree for course-notes volumes."""

    __tablename__ = "course_note_nodes"

    id = Column(BigInteger, primary_key=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    parent_node_id = Column(BigInteger, ForeignKey("course_note_nodes.id"))
    node_path = Column(Text, nullable=False)
    node_type = Column(Text, nullable=False)
    depth = Column(Integer, nullable=False)
    order_index = Column(Integer, nullable=False)
    heading_level = Column(Integer)
    list_kind = Column(Text)
    list_ordinal = Column(Integer)
    text_plain = Column(Text, nullable=False)
    text_html = Column(Text)
    text_markdown = Column(Text)
    page_start = Column(Integer)
    page_end = Column(Integer)
    bbox_json = Column(JSONB)
    quality_flags = Column(JSONB)


class CourseNoteTable(Base):
    """Normalized table payload linked to course-note semantic nodes."""

    __tablename__ = "course_note_tables"

    id = Column(BigInteger, primary_key=True)
    node_id = Column(BigInteger, ForeignKey("course_note_nodes.id"), nullable=False)
    table_json = Column(JSONB, nullable=False)
    table_html = Column(Text)


class RagQueryRun(Base):
    """Telemetry row for each query/stream execution."""

    __tablename__ = "rag_query_runs"
    __table_args__ = (
        Index("idx_rag_query_runs_created_at", "created_at"),
        Index("idx_rag_query_runs_corpus", "corpus"),
        Index("idx_rag_query_runs_model_gen", "model_gen"),
        Index("idx_rag_query_runs_answer_mode", "answer_mode"),
        Index("idx_rag_query_runs_grounded", "grounded"),
        Index("idx_rag_query_runs_refusal", "refusal"),
    )

    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    endpoint = Column(Text, nullable=False)
    user_session_id = Column(Text)
    corpus = Column(Text, nullable=False)
    query_text = Column(Text)
    query_hash = Column(Text, nullable=False)
    model_gen = Column(Text)
    model_embed = Column(Text)
    model_rerank = Column(Text)
    answer_mode = Column(Text)
    grounded = Column(Boolean)
    refusal = Column(Boolean)
    top_k_requested = Column(Integer)
    returned_citations_count = Column(Integer)
    bm25_candidates_count = Column(Integer)
    vector_candidates_count = Column(Integer)
    union_candidates_count = Column(Integer)
    union_candidates_pretrim_count = Column(Integer)
    union_candidates_posttrim_count = Column(Integer)
    reranked_count = Column(Integer)
    num_unique_parents_pre = Column(Integer)
    num_unique_parents_final = Column(Integer)
    num_citations_emitted = Column(Integer)
    num_citations_matched = Column(Integer)
    lexical_overlap_top = Column(Float)
    s1 = Column(Float)
    s5 = Column(Float)
    t_retrieve_ms = Column(Float)
    t_rerank_ms = Column(Float)
    t_expand_ms = Column(Float)
    t_generate_ms = Column(Float)
    t_post_ms = Column(Float)
    t_total_ms = Column(Float)
    diversity_cap_enabled = Column(Boolean)
    local_expansion_enabled = Column(Boolean)
    error_type = Column(Text)
    error_message = Column(Text)
    rag_version = Column(Text)
    status = Column(Text)


class RagRerank(Base):
    """Telemetry rows for reranked candidates."""

    __tablename__ = "rag_rerank"
    __table_args__ = (
        Index("idx_rag_rerank_run_id", "run_id"),
        Index("idx_rag_rerank_parent_id", "parent_chunk_id"),
    )

    id = Column(BigInteger, primary_key=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("rag_query_runs.run_id"), nullable=False)
    child_chunk_id = Column(UUID(as_uuid=True))
    parent_chunk_id = Column(UUID(as_uuid=True))
    rank = Column(Integer, nullable=False)
    rerank_score = Column(Float)
    is_primary = Column(Boolean)
    context_only = Column(Boolean)
    selected_final = Column(Boolean)


class RagEvidenceUsed(Base):
    """Telemetry rows for evidence chunks referenced in the final answer."""

    __tablename__ = "rag_evidence_used"
    __table_args__ = (Index("idx_rag_evidence_used_run_id", "run_id"),)

    id = Column(BigInteger, primary_key=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("rag_query_runs.run_id"), nullable=False)
    evidence_id = Column(UUID(as_uuid=True))
    parent_chunk_id = Column(UUID(as_uuid=True))
    citation_ref = Column(Text)
    position = Column(Integer)
    context_only = Column(Boolean)
    matched_in_text = Column(Boolean)
