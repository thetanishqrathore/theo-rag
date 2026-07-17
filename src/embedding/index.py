"""Qdrant indexing for child chunk embeddings."""

from __future__ import annotations

from typing import List

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    DeanBook,
    DeanCollection,
    DeanLesson,
    FileTag,
    SourceFile,
    TranscriptChildChunk,
    TranscriptParentChunk,
    TranscriptLesson,
    Corpus,
)
from src.utils.config import get_config
from src.utils.logging import setup_logging
from src.utils.qdrant_client import get_qdrant_client

logger = structlog.get_logger()


def _resolve_vector_size(vectors_config) -> int | None:
    if vectors_config is None:
        return None
    if hasattr(vectors_config, "size"):
        return int(vectors_config.size)
    if isinstance(vectors_config, dict):
        if "size" in vectors_config:
            return int(vectors_config["size"])
        if vectors_config:
            first = next(iter(vectors_config.values()))
            if hasattr(first, "size"):
                return int(first.size)
            if isinstance(first, dict) and "size" in first:
                return int(first["size"])
    return None


def _validate_collection_dimension(collection_name: str, client: QdrantClient, expected_dim: int) -> None:
    info = client.get_collection(collection_name)
    vectors_config = getattr(getattr(info, "config", None), "params", None)
    vectors_config = getattr(vectors_config, "vectors", None)
    configured_dim = _resolve_vector_size(vectors_config)
    if configured_dim is None:
        return
    if int(configured_dim) != int(expected_dim):
        raise ValueError(
            f"Qdrant collection '{collection_name}' dimension mismatch: "
            f"found {configured_dim}, expected {expected_dim}"
        )


def create_collection(corpus: str, client: QdrantClient, expected_dim: int) -> str:
    collection_name = f"{corpus}_chunks"
    collections = client.get_collections().collections
    if any(c.name == collection_name for c in collections):
        _validate_collection_dimension(collection_name, client, expected_dim)
        logger.info("collection_exists", name=collection_name)
        return collection_name

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=expected_dim, distance=Distance.COSINE),
    )
    _validate_collection_dimension(collection_name, client, expected_dim)
    logger.info("collection_created", name=collection_name)
    return collection_name


def fetch_embeddings_for_corpus(corpus: str, session: Session, batch_size: int = 1000):
    corpus_id = session.execute(select(Corpus.id).where(Corpus.name == corpus)).scalar_one_or_none()
    if corpus_id is None:
        return
    stmt = (
        select(
            TranscriptChildChunk.id,
            TranscriptChildChunk.embedding,
            TranscriptChildChunk.chunk_index,
            TranscriptChildChunk.metadata_json,
            TranscriptParentChunk.id,
            TranscriptParentChunk.section_title,
            TranscriptParentChunk.metadata_json,
            TranscriptLesson.id,
            FileTag.name,
            DeanCollection.name,
            DeanBook.book_slug,
            DeanLesson.lesson_code,
            Corpus.name,
        )
        .join(TranscriptParentChunk, TranscriptChildChunk.parent_chunk_id == TranscriptParentChunk.id)
        .join(TranscriptLesson, TranscriptParentChunk.lesson_id == TranscriptLesson.id)
        .join(SourceFile, SourceFile.id == TranscriptLesson.source_file_id)
        .join(FileTag, FileTag.id == SourceFile.file_tag_id)
        .join(Corpus, TranscriptChildChunk.corpus_id == Corpus.id)
        .join(DeanLesson, TranscriptLesson.dean_lesson_id == DeanLesson.id, isouter=True)
        .join(DeanBook, DeanLesson.book_id == DeanBook.id, isouter=True)
        .join(DeanCollection, DeanBook.collection_id == DeanCollection.id, isouter=True)
        .where(TranscriptChildChunk.corpus_id == corpus_id)
        .where(TranscriptChildChunk.embedding.is_not(None))
    )

    result = session.execute(stmt).yield_per(batch_size)
    batch: list[dict] = []
    for row in result:
        batch.append(
            {
                "child_chunk_id": row[0],
                "embedding": row[1],
                "chunk_index": row[2],
                "child_metadata": row[3] or {},
                "parent_id": row[4],
                "section_title": row[5],
                "parent_metadata": row[6] or {},
                "lesson_id": row[7],
                "source_tag": row[8],
                "collection": row[9],
                "book_slug": row[10],
                "lesson_code": row[11],
                "corpus": row[12],
            }
        )
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def prepare_qdrant_points(embeddings_data: List[dict]) -> List[PointStruct]:
    points: list[PointStruct] = []
    for item in embeddings_data:
        child_meta = item.get("child_metadata") or {}
        parent_meta = item.get("parent_metadata") or {}
        node_path = child_meta.get("node_path")
        if not node_path and isinstance(parent_meta, dict):
            node_paths = parent_meta.get("node_paths") or []
            node_path = node_paths[0] if node_paths else None
        page_start = child_meta.get("page_start")
        page_end = child_meta.get("page_end")
        if page_start is None and isinstance(parent_meta, dict):
            page_start = parent_meta.get("page_start")
        if page_end is None and isinstance(parent_meta, dict):
            page_end = parent_meta.get("page_end")
        section_path = child_meta.get("section_path")
        if not section_path and isinstance(parent_meta, dict):
            section_path = parent_meta.get("section_path")
        is_front_matter = bool(child_meta.get("is_front_matter", False))
        if not is_front_matter and isinstance(parent_meta, dict):
            is_front_matter = bool(parent_meta.get("is_front_matter", False))
        points.append(
            PointStruct(
                id=str(item["child_chunk_id"]),
                vector=item["embedding"],
                payload={
                    "parent_id": str(item["parent_id"]),
                    "lesson_id": item["lesson_id"],
                    "corpus": item["corpus"],
                    "source_tag": item.get("source_tag"),
                    "collection": item.get("collection"),
                    "book_slug": item.get("book_slug"),
                    "lesson_code": item.get("lesson_code"),
                    "section_title": item.get("section_title"),
                    "chunk_index": item.get("chunk_index"),
                    "node_path": node_path,
                    "section_path": section_path,
                    "page_start": page_start,
                    "page_end": page_end,
                    "is_front_matter": is_front_matter,
                    "part_number": parent_meta.get("part_number") if isinstance(parent_meta, dict) else None,
                },
            )
        )
    return points


def upload_points_to_qdrant(
    collection_name: str, points: List[PointStruct], client: QdrantClient, batch_size: int = 1000
) -> int:
    config = get_config()
    expected_dim = int(config.qdrant_expected_dim)
    total_uploaded = 0
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        for point in batch:
            vector = point.vector
            if vector is None:
                raise ValueError(f"Point '{point.id}' has null vector")
            if len(vector) != expected_dim:
                raise ValueError(
                    f"Point '{point.id}' vector size mismatch: got {len(vector)}, expected {expected_dim}"
                )
        client.upsert(collection_name=collection_name, points=batch)
        total_uploaded += len(batch)
        logger.debug("batch_uploaded", uploaded=total_uploaded, total=len(points))
    return total_uploaded


def verify_qdrant_index(corpus: str, client: QdrantClient) -> dict:
    expected_dim = int(get_config().qdrant_expected_dim)
    collection_name = f"{corpus}_chunks"
    _validate_collection_dimension(collection_name, client, expected_dim=expected_dim)
    info = client.get_collection(collection_name)
    return {
        "collection_exists": True,
        "points_count": info.points_count,
        "vectors_count": info.vectors_count,
        "status": "ok",
    }


def verify_qdrant_parity(corpus: str, session: Session, client: QdrantClient) -> dict:
    corpus_id = session.execute(select(Corpus.id).where(Corpus.name == corpus)).scalar_one_or_none()
    if corpus_id is None:
        return {"status": "error", "error": f"Unknown corpus '{corpus}'"}
    db_total = int(
        session.execute(
            select(func.count(TranscriptChildChunk.id))
            .where(TranscriptChildChunk.corpus_id == corpus_id)
            .where(TranscriptChildChunk.embedding.is_not(None))
        ).scalar_one()
        or 0
    )
    verify = verify_qdrant_index(corpus, client)
    qdrant_points = int(verify.get("points_count") or 0)
    if db_total == 0:
        ratio = 1.0 if qdrant_points == 0 else 0.0
    else:
        ratio = qdrant_points / db_total
    return {
        "db_embedded_child_chunks": db_total,
        "qdrant_points": qdrant_points,
        "parity_ratio": round(ratio, 4),
        "within_1pct": abs(1.0 - ratio) <= 0.01,
    }


def index_corpus_to_qdrant(corpus: str, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="index_corpus_to_qdrant", corpus=corpus)

    config = get_config()
    expected_dim = int(config.qdrant_expected_dim)
    client = get_qdrant_client(timeout=30.0)

    collection_name = create_collection(corpus, client, expected_dim=expected_dim)
    uploaded = 0
    has_rows = False
    for batch in fetch_embeddings_for_corpus(corpus, session):
        if not batch:
            continue
        has_rows = True
        points = prepare_qdrant_points(batch)
        uploaded += upload_points_to_qdrant(collection_name, points, client)

    if not has_rows:
        return {
            "status": "error",
            "collection_name": collection_name,
            "points_indexed": 0,
            "message": "No embeddings found",
        }
    verify = verify_qdrant_index(corpus, client)
    parity = verify_qdrant_parity(corpus, session, client)
    log.info("qdrant_indexed", points=uploaded, verify=verify, parity=parity)

    return {
        "status": "success",
        "collection_name": collection_name,
        "points_indexed": uploaded,
        "parity": parity,
        "message": f"Indexed {uploaded} points in collection '{collection_name}'",
    }
