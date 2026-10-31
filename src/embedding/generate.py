"""Embedding generation for child chunks."""

from __future__ import annotations

from functools import lru_cache
from typing import List
import hashlib
import re

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session
import torch
from sentence_transformers import SentenceTransformer

from src.db.models import (
    TranscriptLesson,
    TranscriptParentChunk,
    TranscriptChildChunk,
    TranscriptChildChunkAudit,
)
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()
EMBED_NORMALIZATION_VERSION = "v1"


@lru_cache(maxsize=1)
def load_embedding_model() -> SentenceTransformer:
    # Cached: batch runs embed thousands of lessons in one process.
    config = get_config()
    model = SentenceTransformer(config.embedding_model)
    if torch.cuda.is_available():
        model = model.to("cuda")
    return model


def generate_embeddings_batch(
    texts: List[str], model: SentenceTransformer, batch_size: int = 256
) -> List[List[float]]:
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return embeddings.tolist()


def normalize_for_embedding(text: str) -> str:
    cleaned = text.replace("\r\n", "\n")
    cleaned = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", cleaned)
    cleaned = re.sub(r"([\-_.]{4,})", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def apply_document_prefix(text: str) -> str:
    config = get_config()
    if not config.embedding_use_prefix:
        return text
    prefix = (config.embedding_doc_prefix or "").strip()
    if not prefix:
        return text
    return f"{prefix} {text}".strip()


def validate_embedding_dimension(model: SentenceTransformer) -> int:
    config = get_config()
    dim = int(model.get_sentence_embedding_dimension() or 0)
    expected = int(config.qdrant_expected_dim)
    if dim != expected:
        raise ValueError(
            f"Embedding dimension mismatch for model '{config.embedding_model}': "
            f"got {dim}, expected {expected} (QDRANT_EXPECTED_DIM)"
        )
    return dim


def estimate_tokens(word_count: int) -> int:
    return max(1, int(word_count * 1.3))


def store_embeddings(
    child_chunk_ids: List[str],
    vectors: List[List[float]],
    audits: List[TranscriptChildChunkAudit],
    session: Session,
) -> int:
    session.bulk_update_mappings(
        TranscriptChildChunk,
        [{"id": child_id, "embedding": vector} for child_id, vector in zip(child_chunk_ids, vectors)],
    )
    for audit in audits:
        session.merge(audit)
    return len(child_chunk_ids)


def process_lesson_embeddings(lesson_id: int, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="process_lesson_embeddings", lesson_id=lesson_id)
    config = get_config()

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        return {"status": "error", "embeddings_generated": 0, "model_name": "", "message": "Lesson not found"}
    if lesson.status != "child_chunked":
        return {
            "status": "error",
            "embeddings_generated": 0,
            "model_name": "",
            "message": "Lesson not child_chunked",
        }

    parents = (
        session.execute(select(TranscriptParentChunk.id).where(TranscriptParentChunk.lesson_id == lesson_id))
        .scalars()
        .all()
    )
    if not parents:
        return {"status": "error", "embeddings_generated": 0, "model_name": "", "message": "No parents found"}

    child_chunks = (
        session.execute(
            select(TranscriptChildChunk.id, TranscriptChildChunk.content)
            .where(TranscriptChildChunk.parent_chunk_id.in_(parents))
            .where(TranscriptChildChunk.embedding.is_(None))
        )
        .all()
    )
    if not child_chunks:
        lesson.status = "embedded"
        session.commit()
        return {
            "status": "success",
            "embeddings_generated": 0,
            "model_name": config.embedding_model,
            "message": "No unembedded child chunks",
        }

    model = load_embedding_model()
    model_dim = validate_embedding_dimension(model)
    raw_texts = [row[1] for row in child_chunks]
    ids = [row[0] for row in child_chunks]
    normalized_texts = [normalize_for_embedding(text) for text in raw_texts]
    embed_inputs = [apply_document_prefix(text) for text in normalized_texts]

    vectors = generate_embeddings_batch(embed_inputs, model)
    embed_dim = len(vectors[0]) if vectors else model_dim
    if vectors and embed_dim != model_dim:
        raise ValueError(
            f"Embedding batch dimension mismatch for model '{config.embedding_model}': "
            f"got {embed_dim}, expected {model_dim}"
        )
    audits: list[TranscriptChildChunkAudit] = []
    for child_id, text in zip(ids, normalized_texts):
        word_count = len(text.split())
        audits.append(
            TranscriptChildChunkAudit(
                child_chunk_id=child_id,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                char_len=len(text),
                word_count=word_count,
                token_est=estimate_tokens(word_count),
                embed_model=config.embedding_model,
                embed_dim=embed_dim,
                embed_version=EMBED_NORMALIZATION_VERSION,
                experiment_tag=config.rag_experiment_tag,
            )
        )
    stored = store_embeddings(ids, vectors, audits, session)
    lesson.status = "embedded"
    session.commit()

    log.info(
        "embeddings_generated",
        count=stored,
        model=config.embedding_model,
        embed_version=EMBED_NORMALIZATION_VERSION,
        experiment_tag=config.rag_experiment_tag,
    )
    return {
        "status": "success",
        "embeddings_generated": stored,
        "model_name": config.embedding_model,
        "message": f"Generated embeddings for {stored} child chunks",
    }
