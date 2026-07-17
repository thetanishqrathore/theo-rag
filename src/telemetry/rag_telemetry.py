"""Telemetry helpers for RAG query evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from typing import Iterable
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from src.db.models import RagEvidenceUsed, RagQueryRun, RagRerank
from src.utils.config import get_config

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RunPayload:
    run_id: str
    endpoint: str
    user_session_id: str | None
    corpus: str
    query_text: str
    model_gen: str | None
    model_embed: str | None
    model_rerank: str | None
    top_k_requested: int | None
    returned_citations_count: int | None = None
    bm25_candidates_count: int | None = None
    vector_candidates_count: int | None = None
    union_candidates_count: int | None = None
    union_candidates_pretrim_count: int | None = None
    union_candidates_posttrim_count: int | None = None
    reranked_count: int | None = None
    num_unique_parents_pre: int | None = None
    num_unique_parents_final: int | None = None
    num_citations_emitted: int | None = None
    num_citations_matched: int | None = None
    lexical_overlap_top: float | None = None
    s1: float | None = None
    s5: float | None = None
    t_retrieve_ms: float | None = None
    t_rerank_ms: float | None = None
    t_expand_ms: float | None = None
    t_generate_ms: float | None = None
    t_post_ms: float | None = None
    t_total_ms: float | None = None
    diversity_cap_enabled: bool | None = None
    local_expansion_enabled: bool | None = None
    answer_mode: str | None = None
    grounded: bool | None = None
    refusal: bool | None = None
    grounding_reason: str | None = None
    citation_retry_used: bool | None = None
    citation_retry_succeeded: bool | None = None
    citation_automap_used: bool | None = None
    citation_automap_succeeded: bool | None = None
    small_talk_bypass: bool | None = None
    error_type: str | None = None
    error_message: str | None = None
    rag_version: str | None = None
    status: str | None = None
    query_hash: str | None = None


@dataclass(frozen=True)
class RerankRowPayload:
    child_chunk_id: str | UUID | None
    parent_chunk_id: str | UUID | None
    rank: int | None
    rerank_score: float | None
    is_primary: bool | None
    context_only: bool | None
    selected_final: bool | None


@dataclass(frozen=True)
class EvidenceRowPayload:
    evidence_id: str | UUID | None
    parent_chunk_id: str | UUID | None
    citation_ref: str | None
    position: int | None
    context_only: bool | None
    matched_in_text: bool | None


def _hash_query(query: str) -> str:
    normalized = " ".join(query.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _safe_uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


_RUN_PAYLOAD_FIELDS = {field.name for field in fields(RunPayload)}


def _as_run_payload(payload: RunPayload | dict) -> RunPayload:
    if isinstance(payload, RunPayload):
        return payload
    filtered = {key: payload.get(key) for key in _RUN_PAYLOAD_FIELDS if key in payload}
    return RunPayload(**filtered)


def _as_rerank_payload(row: RerankRowPayload | dict) -> RerankRowPayload:
    if isinstance(row, RerankRowPayload):
        return row
    return RerankRowPayload(
        child_chunk_id=row.get("child_chunk_id"),
        parent_chunk_id=row.get("parent_chunk_id"),
        rank=row.get("rank"),
        rerank_score=row.get("rerank_score"),
        is_primary=row.get("is_primary"),
        context_only=row.get("context_only"),
        selected_final=row.get("selected_final"),
    )


def _as_evidence_payload(row: EvidenceRowPayload | dict) -> EvidenceRowPayload:
    if isinstance(row, EvidenceRowPayload):
        return row
    return EvidenceRowPayload(
        evidence_id=row.get("evidence_id"),
        parent_chunk_id=row.get("parent_chunk_id"),
        citation_ref=row.get("citation_ref"),
        position=row.get("position"),
        context_only=row.get("context_only"),
        matched_in_text=row.get("matched_in_text"),
    )


def create_run_payload(
    *,
    run_id: str,
    endpoint: str,
    user_session_id: str | None,
    corpus: str,
    query_text: str,
    model_gen: str | None,
    model_embed: str | None,
    model_rerank: str | None,
    top_k_requested: int | None,
    returned_citations_count: int | None = None,
    bm25_candidates_count: int | None = None,
    vector_candidates_count: int | None = None,
    union_candidates_count: int | None = None,
    union_candidates_pretrim_count: int | None = None,
    union_candidates_posttrim_count: int | None = None,
    reranked_count: int | None = None,
    num_unique_parents_pre: int | None = None,
    num_unique_parents_final: int | None = None,
    num_citations_emitted: int | None = None,
    num_citations_matched: int | None = None,
    lexical_overlap_top: float | None = None,
    s1: float | None = None,
    s5: float | None = None,
    t_retrieve_ms: float | None = None,
    t_rerank_ms: float | None = None,
    t_expand_ms: float | None = None,
    t_generate_ms: float | None = None,
    t_post_ms: float | None = None,
    t_total_ms: float | None = None,
    diversity_cap_enabled: bool | None = None,
    local_expansion_enabled: bool | None = None,
    answer_mode: str | None = None,
    grounded: bool | None = None,
    refusal: bool | None = None,
    grounding_reason: str | None = None,
    citation_retry_used: bool | None = None,
    citation_retry_succeeded: bool | None = None,
    citation_automap_used: bool | None = None,
    citation_automap_succeeded: bool | None = None,
    small_talk_bypass: bool | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    rag_version: str | None = None,
    status: str | None = None,
) -> RunPayload:
    config = get_config()
    store_text = config.telemetry_store_query_text
    return RunPayload(
        run_id=run_id,
        endpoint=endpoint,
        user_session_id=user_session_id,
        corpus=corpus,
        query_text=query_text if store_text else "",
        query_hash=_hash_query(query_text),
        model_gen=model_gen,
        model_embed=model_embed,
        model_rerank=model_rerank,
        answer_mode=answer_mode,
        grounded=grounded,
        refusal=refusal,
        grounding_reason=grounding_reason,
        citation_retry_used=citation_retry_used,
        citation_retry_succeeded=citation_retry_succeeded,
        citation_automap_used=citation_automap_used,
        citation_automap_succeeded=citation_automap_succeeded,
        small_talk_bypass=small_talk_bypass,
        top_k_requested=top_k_requested,
        returned_citations_count=returned_citations_count,
        bm25_candidates_count=bm25_candidates_count,
        vector_candidates_count=vector_candidates_count,
        union_candidates_count=union_candidates_count,
        union_candidates_pretrim_count=union_candidates_pretrim_count,
        union_candidates_posttrim_count=union_candidates_posttrim_count,
        reranked_count=reranked_count,
        num_unique_parents_pre=num_unique_parents_pre,
        num_unique_parents_final=num_unique_parents_final,
        num_citations_emitted=num_citations_emitted,
        num_citations_matched=num_citations_matched,
        lexical_overlap_top=lexical_overlap_top,
        s1=s1,
        s5=s5,
        t_retrieve_ms=t_retrieve_ms,
        t_rerank_ms=t_rerank_ms,
        t_expand_ms=t_expand_ms,
        t_generate_ms=t_generate_ms,
        t_post_ms=t_post_ms,
        t_total_ms=t_total_ms,
        diversity_cap_enabled=diversity_cap_enabled,
        local_expansion_enabled=local_expansion_enabled,
        error_type=error_type,
        error_message=error_message,
        rag_version=rag_version or config.rag_version,
        status=status,
    )


def insert_query_run(session: Session, payload: RunPayload | dict) -> None:
    config = get_config()
    if not config.enable_telemetry:
        return
    try:
        run = _as_run_payload(payload)
        run_id = _safe_uuid(run.run_id)
        if run_id is None:
            logger.warning("telemetry_insert_failed", extra={"error": f"invalid run_id: {run.run_id}"})
            return
        row = RagQueryRun(
            run_id=run_id,
            endpoint=run.endpoint,
            user_session_id=run.user_session_id,
            corpus=run.corpus,
            query_text=run.query_text or None,
            query_hash=run.query_hash or "",
            model_gen=run.model_gen,
            model_embed=run.model_embed,
            model_rerank=run.model_rerank,
            answer_mode=run.answer_mode,
            grounded=run.grounded,
            refusal=run.refusal,
            top_k_requested=run.top_k_requested,
            returned_citations_count=run.returned_citations_count,
            bm25_candidates_count=run.bm25_candidates_count,
            vector_candidates_count=run.vector_candidates_count,
            union_candidates_count=run.union_candidates_count,
            union_candidates_pretrim_count=run.union_candidates_pretrim_count,
            union_candidates_posttrim_count=run.union_candidates_posttrim_count,
            reranked_count=run.reranked_count,
            num_unique_parents_pre=run.num_unique_parents_pre,
            num_unique_parents_final=run.num_unique_parents_final,
            num_citations_emitted=run.num_citations_emitted,
            num_citations_matched=run.num_citations_matched,
            lexical_overlap_top=run.lexical_overlap_top,
            s1=run.s1,
            s5=run.s5,
            t_retrieve_ms=run.t_retrieve_ms,
            t_rerank_ms=run.t_rerank_ms,
            t_expand_ms=run.t_expand_ms,
            t_generate_ms=run.t_generate_ms,
            t_post_ms=run.t_post_ms,
            t_total_ms=run.t_total_ms,
            diversity_cap_enabled=run.diversity_cap_enabled,
            local_expansion_enabled=run.local_expansion_enabled,
            error_type=run.error_type,
            error_message=run.error_message,
            rag_version=run.rag_version,
            status=run.status,
        )
        session.add(row)
        session.flush()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("telemetry_insert_failed", extra={"error": str(exc)})


def insert_rerank_rows(
    session: Session,
    run_id: str,
    rows: Iterable[dict | RerankRowPayload],
) -> None:
    config = get_config()
    if not config.enable_telemetry:
        return
    rows_list = list(rows)
    if not rows_list:
        return
    run_uuid = _safe_uuid(run_id)
    if run_uuid is None:
        logger.warning("telemetry_rerank_insert_failed", extra={"error": f"invalid run_id: {run_id}"})
        return
    try:
        orm_rows: list[RagRerank] = []
        for row in rows_list:
            payload = _as_rerank_payload(row)
            if payload.rank is None:
                continue
            orm_rows.append(
                RagRerank(
                    run_id=run_uuid,
                    child_chunk_id=_safe_uuid(payload.child_chunk_id),
                    parent_chunk_id=_safe_uuid(payload.parent_chunk_id),
                    rank=payload.rank,
                    rerank_score=payload.rerank_score,
                    is_primary=payload.is_primary,
                    context_only=payload.context_only,
                    selected_final=payload.selected_final,
                )
            )
        if not orm_rows:
            return
        session.add_all(orm_rows)
        session.flush()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("telemetry_rerank_insert_failed", extra={"error": str(exc)})


def insert_evidence_used_rows(
    session: Session,
    run_id: str,
    rows: Iterable[dict | EvidenceRowPayload],
) -> None:
    config = get_config()
    if not config.enable_telemetry:
        return
    rows_list = list(rows)
    if not rows_list:
        return
    run_uuid = _safe_uuid(run_id)
    if run_uuid is None:
        logger.warning("telemetry_evidence_insert_failed", extra={"error": f"invalid run_id: {run_id}"})
        return
    try:
        orm_rows: list[RagEvidenceUsed] = []
        for row in rows_list:
            payload = _as_evidence_payload(row)
            orm_rows.append(
                RagEvidenceUsed(
                    run_id=run_uuid,
                    evidence_id=_safe_uuid(payload.evidence_id),
                    parent_chunk_id=_safe_uuid(payload.parent_chunk_id),
                    citation_ref=payload.citation_ref,
                    position=payload.position,
                    context_only=payload.context_only,
                    matched_in_text=payload.matched_in_text,
                )
            )
        session.add_all(orm_rows)
        session.flush()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.warning("telemetry_evidence_insert_failed", extra={"error": str(exc)})
