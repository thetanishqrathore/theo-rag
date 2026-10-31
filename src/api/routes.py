"""API routes for retrieval and generation."""

from __future__ import annotations

import json
import re
import time
import threading
from uuid import uuid4
from typing import Optional, Generator
from urllib.parse import quote

import structlog
from fastapi import APIRouter, HTTPException, Depends, Request as FastAPIRequest
from fastapi.responses import StreamingResponse
from sqlalchemy import func, text, select
from sqlalchemy.orm import Session
from sentence_transformers import SentenceTransformer, CrossEncoder

from src.api.models import (
    QueryRequest,
    QueryResponse,
    CitationData,
    EnrichCitationsRequest,
    HealthResponse,
    CollectionsResponse,
    StatsResponse,
    EvidenceResponse,
    ModelPullRequest,
    ModelKeepAliveRequest,
)
from src.corpus.registry import CORPUS_IDS, CORPUS_REGISTRY, is_valid_corpus
from src.db.models import (
    Corpus,
    DeanBook,
    DeanCollection,
    DeanLesson,
    FileTag,
    SourceFile,
    Series,
    TranscriptPart,
    TranscriptChapter,
    TranscriptLesson,
    TranscriptParentChunk,
    TranscriptChildChunk,
)
from src.db.session import get_db, get_sessionmaker
from src.generation.generate import (
    build_small_talk_reply,
    generate_grounded_answer,
    is_small_talk_query,
    stream_grounded_answer,
)
from src.retrieval.retrieve import retrieve_context_with_stats
from src.retrieval.citation_utils import build_citation_path, lesson_label
from src.utils.config import get_config
from src.utils.ollama_client import get_ollama_client, helper_num_ctx, warm_ollama_model, maybe_warm_ollama_model
from src.utils.qdrant_client import get_qdrant_client
from src.utils.logging import setup_logging
from src.telemetry.rag_telemetry import (
    create_run_payload,
    insert_evidence_used_rows,
    insert_query_run,
    insert_rerank_rows,
)
from src.observability.context import set_request_context
from src.observability.logging import log_event

logger = structlog.get_logger()
router = APIRouter()

_EMBEDDER: Optional[SentenceTransformer] = None
_RERANKER: Optional[CrossEncoder] = None
_GENERATION_SEMAPHORE: Optional[threading.BoundedSemaphore] = None
_GENERATION_SEMAPHORE_LIMIT: int = 0


def set_models(embedder: SentenceTransformer, reranker: CrossEncoder) -> None:
    """Store globally loaded models for reuse in requests."""
    global _EMBEDDER
    global _RERANKER
    _EMBEDDER = embedder
    _RERANKER = reranker


def _get_generation_semaphore() -> tuple[threading.BoundedSemaphore, int]:
    global _GENERATION_SEMAPHORE
    global _GENERATION_SEMAPHORE_LIMIT

    config = get_config()
    limit = max(1, config.rag_generation_max_concurrency)
    if _GENERATION_SEMAPHORE is None or _GENERATION_SEMAPHORE_LIMIT != limit:
        _GENERATION_SEMAPHORE = threading.BoundedSemaphore(value=limit)
        _GENERATION_SEMAPHORE_LIMIT = limit
    return _GENERATION_SEMAPHORE, limit


def validate_corpus(corpus: str) -> None:
    if not is_valid_corpus(corpus):
        supported = ", ".join(f"'{name}'" for name in CORPUS_IDS)
        raise HTTPException(status_code=400, detail=f"Invalid corpus. Supported values: {supported}.")


def validate_query(query: str) -> None:
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    if len(query) > 2000:
        raise HTTPException(status_code=400, detail="Query too long. Maximum 2000 characters allowed.")
    if len(query.strip()) < 3:
        raise HTTPException(status_code=400, detail="Query too short. Please provide a more detailed question.")


def _canonical_rel_path(canonical_path: str) -> str:
    normalized = (canonical_path or "").replace("\\", "/").strip()
    if not normalized or re.match(r"^https?://", normalized, flags=re.IGNORECASE):
        return ""
    marker = "/data/canonical/"
    idx = normalized.find(marker)
    if idx >= 0:
        normalized = normalized[idx + len(marker) :]
    elif normalized.startswith("data/canonical/"):
        normalized = normalized[len("data/canonical/") :]
    return normalized.lstrip("/").strip()


def _canonical_url(canonical_path: str) -> str:
    rel_path = _canonical_rel_path(canonical_path)
    if not rel_path:
        return ""
    encoded = [quote(segment, safe="") for segment in rel_path.split("/") if segment]
    return f"/static/{'/'.join(encoded)}" if encoded else ""


def _citation_field(item: dict, field: str):
    citation_payload = item.get("citation")
    if isinstance(citation_payload, dict):
        value = citation_payload.get(field)
        if value is not None:
            return value
    elif citation_payload is not None:
        value = getattr(citation_payload, field, None)
        if value is not None:
            return value
    return item.get(field)


def _citation_data_from_item(item: dict) -> CitationData:
    child_chunk_id = item.get("chunk_id") or item.get("child_chunk_id") or item.get("evidence_id")
    parent_chunk_id = item.get("parent_chunk_id") or item.get("parent_id")
    lesson = _citation_field(item, "lesson")
    full_text = item.get("full_text")
    if full_text is None:
        full_text = item.get("parent_text") or ""
    return CitationData(
        series=str(_citation_field(item, "series") or ""),
        part=str(_citation_field(item, "part") or ""),
        lesson=lesson if lesson is not None else "",
        date=str(_citation_field(item, "date") or ""),
        speaker=str(_citation_field(item, "speaker") or ""),
        child_chunk_id=str(child_chunk_id or ""),
        parent_chunk_id=str(parent_chunk_id or ""),
        child_chunk_index=item.get("child_chunk_index"),
        is_primary=bool(item.get("is_primary", True)),
        context_only=bool(item.get("context_only", False)),
        source_tag=item.get("source_tag"),
        collection=item.get("collection"),
        book_slug=item.get("book_slug"),
        lesson_code=item.get("lesson_code"),
        node_path=item.get("node_path"),
        section_path=item.get("section_path"),
        page_start=item.get("page_start"),
        page_end=item.get("page_end"),
        is_front_matter=bool(item.get("is_front_matter", False)),
        citation_ref=item.get("citation_ref"),
        full_text=full_text,
        parent_text=item.get("parent_text"),
        child_text=item.get("child_text"),
        file_path=str(_citation_field(item, "file_path") or ""),
    )


def categorize_error(exc: Exception) -> tuple[int, str, str]:
    exc_str = str(exc).lower()

    if isinstance(exc, ConnectionError) or "connection" in exc_str:
        if "ollama" in exc_str:
            return 503, "ollama_unavailable", "The language model service is currently unavailable. Please try again later."
        if "qdrant" in exc_str:
            return 503, "search_unavailable", "The search service is currently unavailable. Please try again later."
        if "postgres" in exc_str or "database" in exc_str:
            return 503, "database_unavailable", "The database service is currently unavailable. Please try again later."
        return 503, "service_unavailable", "A required service is currently unavailable. Please try again later."

    if "timeout" in exc_str or "timed out" in exc_str:
        return 504, "timeout", "The request took too long to process. Please try a simpler question."

    if "model" in exc_str and ("not found" in exc_str or "does not exist" in exc_str):
        return 400, "model_not_found", "The requested model is not available. Please select a different model."

    if "out of memory" in exc_str or "oom" in exc_str:
        return 503, "resource_exhausted", "The server is currently under heavy load. Please try again later."

    return 500, "internal_error", "An unexpected error occurred while processing your request."


def _build_run_payload(
    *,
    run_id: str,
    endpoint: str,
    user_session_id: str | None,
    corpus: str,
    query_text: str,
    model_name: str | None,
    top_k_requested: int | None,
    retrieval_stats: dict | None = None,
    metrics: dict | None = None,
    returned_citations_count: int | None = None,
    num_citations_emitted: int | None = None,
    num_citations_matched: int | None = None,
    t_retrieve_ms: float | None = None,
    t_generate_ms: float | None = None,
    t_post_ms: float | None = None,
    t_total_ms: float | None = None,
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
    status: str | None = None,
):
    retrieval_stats = retrieval_stats or {}
    metrics = metrics or {}
    config = get_config()
    return create_run_payload(
        run_id=run_id,
        endpoint=endpoint,
        user_session_id=user_session_id,
        corpus=corpus,
        query_text=query_text,
        model_gen=model_name,
        model_embed=config.embedding_model,
        model_rerank=config.reranker_model,
        top_k_requested=top_k_requested,
        returned_citations_count=returned_citations_count,
        bm25_candidates_count=retrieval_stats.get("bm25_candidates_count"),
        vector_candidates_count=retrieval_stats.get("vector_candidates_count"),
        union_candidates_count=retrieval_stats.get("union_candidates_count"),
        union_candidates_pretrim_count=retrieval_stats.get("union_candidates_pretrim_count"),
        union_candidates_posttrim_count=retrieval_stats.get("union_candidates_posttrim_count"),
        reranked_count=retrieval_stats.get("reranked_count"),
        num_unique_parents_pre=retrieval_stats.get("num_unique_parents_pre"),
        num_unique_parents_final=retrieval_stats.get("num_unique_parents_final"),
        num_citations_emitted=num_citations_emitted,
        num_citations_matched=num_citations_matched,
        lexical_overlap_top=metrics.get("overlap"),
        s1=metrics.get("s1"),
        s5=metrics.get("s5"),
        t_retrieve_ms=t_retrieve_ms,
        t_rerank_ms=retrieval_stats.get("t_rerank_ms"),
        t_expand_ms=retrieval_stats.get("t_expand_ms"),
        t_generate_ms=t_generate_ms,
        t_post_ms=t_post_ms,
        t_total_ms=t_total_ms,
        diversity_cap_enabled=retrieval_stats.get("diversity_cap_enabled"),
        local_expansion_enabled=retrieval_stats.get("local_expansion_enabled"),
        answer_mode=answer_mode,
        grounded=grounded,
        refusal=refusal,
        grounding_reason=grounding_reason,
        citation_retry_used=citation_retry_used,
        citation_retry_succeeded=citation_retry_succeeded,
        citation_automap_used=citation_automap_used,
        citation_automap_succeeded=citation_automap_succeeded,
        small_talk_bypass=small_talk_bypass,
        error_type=error_type,
        error_message=error_message,
        status=status,
    )


def _persist_telemetry_rows(
    session: Session,
    *,
    run_id: str,
    telemetry_payload,
    rerank_rows: list[dict] | None = None,
    evidence_rows: list[dict] | None = None,
) -> None:
    insert_query_run(session, telemetry_payload)
    if rerank_rows:
        insert_rerank_rows(session, run_id, rerank_rows)
    if evidence_rows:
        insert_evidence_used_rows(session, run_id, evidence_rows)


def _persist_telemetry_with_new_session(
    SessionLocal,
    *,
    run_id: str,
    telemetry_payload,
    rerank_rows: list[dict] | None = None,
    evidence_rows: list[dict] | None = None,
    log=None,
) -> None:
    telemetry_session = SessionLocal()
    try:
        _persist_telemetry_rows(
            telemetry_session,
            run_id=run_id,
            telemetry_payload=telemetry_payload,
            rerank_rows=rerank_rows,
            evidence_rows=evidence_rows,
        )
        telemetry_session.commit()
    except Exception as exc:  # noqa: BLE001
        telemetry_session.rollback()
        if log is not None:
            log.warning("stream_telemetry_failed", error=str(exc))
    finally:
        telemetry_session.close()


@router.post("/api/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    http_request: FastAPIRequest,
    session: Session = Depends(get_db),
) -> QueryResponse:
    setup_logging()
    log = logger.bind(function="api_query", corpus=request.corpus)
    validate_corpus(request.corpus)
    set_request_context(corpus=request.corpus)

    if is_small_talk_query(request.query):
        answer, follow_ups = build_small_talk_reply(request.corpus)
        run_id = str(uuid4())
        telemetry_payload = _build_run_payload(
            run_id=run_id,
            endpoint="/api/query",
            user_session_id=http_request.headers.get("X-Session-Id"),
            corpus=request.corpus,
            query_text=request.query,
            model_name=None,
            top_k_requested=request.top_k,
            returned_citations_count=0,
            num_citations_emitted=0,
            num_citations_matched=0,
            t_retrieve_ms=0.0,
            t_generate_ms=0.0,
            t_post_ms=0.0,
            t_total_ms=0.0,
            answer_mode="narrow",
            grounded=False,
            refusal=False,
            grounding_reason="small_talk",
            citation_retry_used=False,
            citation_retry_succeeded=False,
            citation_automap_used=False,
            citation_automap_succeeded=False,
            small_talk_bypass=True,
            status="completed",
        )
        _persist_telemetry_rows(session, run_id=run_id, telemetry_payload=telemetry_payload)
        session.commit()
        return QueryResponse(
            answer=answer,
            grounded=False,
            refusal=False,
            citations=[],
            source_count=0,
            retrieval_time_ms=0.0,
            generation_time_ms=0.0,
            follow_up_questions=follow_ups,
            warmup_ms=0.0,
            model_warmup_performed=False,
            grounding_reason="small_talk",
            citation_retry_used=False,
            citation_retry_succeeded=False,
            citation_automap_used=False,
            citation_automap_succeeded=False,
            citation_fallback_reason=None,
            citation_strategy_version=None,
            num_citations_emitted=0,
            num_citations_matched=0,
        )

    validate_query(request.query)

    semaphore, limit = _get_generation_semaphore()
    acquired = semaphore.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail=f"Server is busy processing other generation requests (limit={limit}). Please retry shortly.",
        )

    run_id = str(uuid4())
    user_session_id = http_request.headers.get("X-Session-Id")
    total_start = time.perf_counter()
    retrieval_stats: dict = {}
    retrieval_time_ms: float | None = None
    generation_time_ms: float | None = None
    post_time_ms: float | None = None
    model_name: str | None = request.model or get_config().ollama_model

    try:
        history_dicts = [{"role": turn.role, "content": turn.content} for turn in request.history] if request.history else None
        retrieval_start = time.perf_counter()
        parent_chunks, retrieval_stats = retrieve_context_with_stats(
            query=request.query,
            corpus=request.corpus,
            top_k=request.top_k,
            session=session,
            embedder=_EMBEDDER,
            reranker=_RERANKER,
            retrieval_mode=request.retrieval_mode,
            history=history_dicts,
        )
        retrieval_time_ms = (time.perf_counter() - retrieval_start) * 1000
        log.info("retrieval_complete", results=len(parent_chunks), ms=retrieval_time_ms)

        generation_start = time.perf_counter()
        config = get_config()
        model_name = request.model or config.ollama_model
        set_request_context(model=model_name)
        warmed, warmup_ms = maybe_warm_ollama_model(model_name, keep_alive=config.ollama_keepalive)
        if warmed:
            log.info("model_warmed", model=model_name, ms=warmup_ms)
        result = generate_grounded_answer(
            query=request.query,
            parent_chunks=parent_chunks,
            corpus=request.corpus,
            model=model_name,
            history=history_dicts,
        )
        generation_time_ms = (time.perf_counter() - generation_start) * 1000
        log.info("generation_complete", ms=generation_time_ms)
        refusal = bool(result.refusal)
        answer_mode = result.answer_mode or ("refuse" if refusal else "narrow")
        grounded = bool(result.grounding_score)

        post_start = time.perf_counter()
        raw_citations = result.citation_items or []
        citations = [_citation_data_from_item(item) for item in raw_citations if isinstance(item, dict)]
        post_time_ms = (time.perf_counter() - post_start) * 1000
        total_time_ms = (time.perf_counter() - total_start) * 1000
        matched_chunk_ids = {
            str(chunk_id)
            for chunk_id in (result.matched_evidence_ids or [])
            if chunk_id
        }
        if not matched_chunk_ids:
            matched_chunk_ids = {
                str(item.get("chunk_id") or item.get("child_chunk_id") or item.get("evidence_id"))
                for item in raw_citations
                if isinstance(item, dict)
                and (item.get("chunk_id") or item.get("child_chunk_id") or item.get("evidence_id"))
                and not bool(item.get("context_only", False))
            }

        metrics = result.sufficiency_metrics or {"s1": None, "s5": None, "overlap": None}

        telemetry_payload = _build_run_payload(
            run_id=run_id,
            endpoint="/api/query",
            user_session_id=user_session_id,
            corpus=request.corpus,
            query_text=request.query,
            model_name=model_name,
            top_k_requested=request.top_k,
            retrieval_stats=retrieval_stats,
            metrics=metrics,
            returned_citations_count=result.num_citations_emitted if result.num_citations_emitted is not None else len(citations),
            num_citations_emitted=result.num_citations_emitted if result.num_citations_emitted is not None else len(citations),
            num_citations_matched=result.num_citations_matched if result.num_citations_matched is not None else len(matched_chunk_ids),
            t_retrieve_ms=retrieval_time_ms,
            t_generate_ms=generation_time_ms,
            t_post_ms=post_time_ms,
            t_total_ms=total_time_ms,
            answer_mode=answer_mode,
            grounded=grounded,
            refusal=refusal,
            grounding_reason=result.grounding_reason,
            citation_retry_used=result.citation_retry_used,
            citation_retry_succeeded=result.citation_retry_succeeded,
            citation_automap_used=result.citation_automap_used,
            citation_automap_succeeded=result.citation_automap_succeeded,
            small_talk_bypass=False,
        )
        primary_ranked = [chunk for chunk in parent_chunks if chunk.is_primary and not chunk.context_only]
        rerank_rows = [
            {
                "child_chunk_id": chunk.child_chunk_id,
                "parent_chunk_id": chunk.parent_chunk_id,
                "rank": idx + 1,
                "rerank_score": chunk.rerank_score,
                "is_primary": True,
                "context_only": False,
                "selected_final": True,
            }
            for idx, chunk in enumerate(primary_ranked)
        ]
        evidence_rows = [
            {
                "evidence_id": item.child_chunk_id,
                "parent_chunk_id": item.parent_chunk_id,
                "citation_ref": item.citation_ref,
                "position": idx + 1,
                "context_only": bool(item.context_only),
                "matched_in_text": str(item.child_chunk_id) in matched_chunk_ids,
            }
            for idx, item in enumerate(citations)
        ]
        _persist_telemetry_rows(
            session,
            run_id=run_id,
            telemetry_payload=telemetry_payload,
            rerank_rows=rerank_rows,
            evidence_rows=evidence_rows,
        )
        session.commit()

        return QueryResponse(
            answer=result.answer,
            grounded=grounded,
            refusal=refusal,
            citations=citations,
            source_count=len(citations),
            retrieval_time_ms=retrieval_time_ms,
            generation_time_ms=generation_time_ms,
            follow_up_questions=result.follow_up_questions,
            warmup_ms=warmup_ms if warmed else 0,
            model_warmup_performed=warmed,
            grounding_reason=result.grounding_reason,
            citation_retry_used=result.citation_retry_used,
            citation_retry_succeeded=result.citation_retry_succeeded,
            citation_automap_used=result.citation_automap_used,
            citation_automap_succeeded=result.citation_automap_succeeded,
            citation_fallback_reason=result.citation_fallback_reason,
            citation_strategy_version=result.citation_strategy_version,
            num_citations_emitted=result.num_citations_emitted,
            num_citations_matched=result.num_citations_matched if result.num_citations_matched is not None else len(matched_chunk_ids),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        status_code, error_type, user_message = categorize_error(exc)
        log.error("query_failed", error=str(exc), error_type=error_type, status=status_code)
        try:
            abort_payload = _build_run_payload(
                run_id=run_id,
                endpoint="/api/query",
                user_session_id=user_session_id,
                corpus=request.corpus,
                query_text=request.query,
                model_name=model_name,
                top_k_requested=request.top_k,
                retrieval_stats=retrieval_stats,
                t_retrieve_ms=retrieval_time_ms,
                t_generate_ms=generation_time_ms,
                t_post_ms=post_time_ms,
                t_total_ms=(time.perf_counter() - total_start) * 1000,
                small_talk_bypass=False,
                error_type=error_type,
                error_message=str(exc),
                status="aborted",
            )
            _persist_telemetry_rows(session, run_id=run_id, telemetry_payload=abort_payload)
            session.commit()
        except Exception as telemetry_exc:  # noqa: BLE001
            session.rollback()
            log.warning("query_abort_telemetry_failed", error=str(telemetry_exc))
        raise HTTPException(status_code=status_code, detail=user_message) from exc
    finally:
        semaphore.release()


@router.post("/api/query/stream")
def query_stream(
    request: QueryRequest,
    http_request: FastAPIRequest,
    session: Session = Depends(get_db),
) -> StreamingResponse:
    setup_logging()
    log = logger.bind(function="api_query_stream", corpus=request.corpus)

    def _validation_error_response(exc: HTTPException) -> StreamingResponse:
        def error_gen() -> Generator[str, None, None]:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
            yield f"event: error\ndata: {json.dumps({'error': detail, 'error_type': 'validation_error'})}\n\n"

        return StreamingResponse(error_gen(), media_type="text/event-stream")

    try:
        validate_corpus(request.corpus)
    except HTTPException as e:
        return _validation_error_response(e)

    if is_small_talk_query(request.query):
        def small_talk_gen() -> Generator[str, None, None]:
            run_id = str(uuid4())
            user_session_id = http_request.headers.get("X-Session-Id")
            answer, follow_ups = build_small_talk_reply(request.corpus)
            SessionLocal = get_sessionmaker()
            telemetry_payload = _build_run_payload(
                run_id=run_id,
                endpoint="/api/query/stream",
                user_session_id=user_session_id,
                corpus=request.corpus,
                query_text=request.query,
                model_name=None,
                top_k_requested=request.top_k,
                returned_citations_count=0,
                num_citations_emitted=0,
                num_citations_matched=0,
                t_retrieve_ms=0.0,
                t_generate_ms=0.0,
                t_post_ms=0.0,
                t_total_ms=0.0,
                answer_mode="narrow",
                grounded=False,
                refusal=False,
                grounding_reason="small_talk",
                citation_retry_used=False,
                citation_retry_succeeded=False,
                citation_automap_used=False,
                citation_automap_succeeded=False,
                small_talk_bypass=True,
                status="completed",
            )
            _persist_telemetry_with_new_session(
                SessionLocal,
                run_id=run_id,
                telemetry_payload=telemetry_payload,
                log=log,
            )
            yield f"event: message\ndata: {json.dumps({'type': 'citations', 'items': [], 'retrieval_time_ms': 0.0})}\n\n"
            yield f"event: message\ndata: {json.dumps({'type': 'token', 'content': answer})}\n\n"
            yield (
                "event: message\ndata: "
                + json.dumps(
                    {
                        "type": "final",
                        "answer_mode": "narrow",
                        "grounded": False,
                        "refusal": False,
                        "answer_text_final": answer,
                        "grounding_reason": "small_talk",
                        "citation_retry_used": False,
                        "citation_retry_succeeded": False,
                        "citation_automap_used": False,
                        "citation_automap_succeeded": False,
                        "citation_fallback_reason": None,
                        "citation_strategy_version": None,
                        "num_citations_emitted": 0,
                        "num_citations_matched": 0,
                        "generation_time_ms": 0.0,
                        "follow_ups": follow_ups,
                        "used_evidence_ids": [],
                    }
                )
                + "\n\n"
            )
        return StreamingResponse(
            small_talk_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        validate_query(request.query)
    except HTTPException as e:
        return _validation_error_response(e)

    def event_generator() -> Generator[str, None, None]:
        run_id: str | None = None
        model_name: str | None = None
        user_session_id = http_request.headers.get("X-Session-Id")
        SessionLocal = get_sessionmaker()
        semaphore, limit = _get_generation_semaphore()
        acquired = False
        try:
            acquired = semaphore.acquire(blocking=False)
            if not acquired:
                yield f"event: error\ndata: {json.dumps({'error': f'Server is busy processing other generation requests (limit={limit}). Please retry shortly.', 'error_type': 'busy'})}\n\n"
                return
            total_start = time.perf_counter()
            run_id = str(uuid4())
            set_request_context(corpus=request.corpus)
            history_dicts = (
                [{"role": turn.role, "content": turn.content} for turn in request.history]
                if request.history
                else None
            )
            config = get_config()
            model_name = request.model or config.ollama_model
            set_request_context(model=model_name)
            log_event("stream_warmup_start", span="stream")
            yield (
                "event: message\ndata: "
                + json.dumps(
                    {
                        "type": "status",
                        "phase": "warmup",
                        "message": f"Checking model {model_name} readiness...",
                    }
                )
                + "\n\n"
            )
            warmed, _warmup_ms = maybe_warm_ollama_model(model_name, keep_alive=config.ollama_keepalive)
            if warmed:
                yield (
                    "event: message\ndata: "
                    + json.dumps(
                        {
                            "type": "status",
                            "phase": "warmup",
                            "message": f"Model {model_name} ready.",
                        }
                    )
                    + "\n\n"
                )
                log_event("stream_warmup_end", span="stream", warmed=True, warmup_ms=_warmup_ms)
            else:
                yield (
                    "event: message\ndata: "
                    + json.dumps(
                        {
                            "type": "status",
                            "phase": "warmup",
                            "message": f"Model {model_name} already warm.",
                        }
                    )
                    + "\n\n"
                )
                log_event("stream_warmup_end", span="stream", warmed=False)
            log_event("stream_open", span="stream")

            def telemetry_hook(payload: dict) -> None:
                retrieval_stats = payload.get("retrieval_stats", {}) or {}
                sufficiency = payload.get("sufficiency", {}) or {}
                metrics = sufficiency.get("metrics", {}) or {}
                if run_id is None:
                    return
                telemetry_payload = _build_run_payload(
                    run_id=run_id,
                    endpoint="/api/query/stream",
                    user_session_id=user_session_id,
                    corpus=request.corpus,
                    query_text=request.query,
                    model_name=model_name,
                    top_k_requested=request.top_k,
                    retrieval_stats=retrieval_stats,
                    metrics=metrics,
                    returned_citations_count=payload.get("num_citations_emitted"),
                    num_citations_emitted=payload.get("num_citations_emitted"),
                    num_citations_matched=payload.get("num_citations_matched"),
                    t_retrieve_ms=payload.get("retrieval_time_ms"),
                    t_generate_ms=payload.get("generation_time_ms"),
                    t_post_ms=payload.get("t_post_ms"),
                    t_total_ms=payload.get("t_total_ms"),
                    answer_mode=payload.get("answer_mode"),
                    grounded=payload.get("grounded"),
                    refusal=payload.get("refusal"),
                    grounding_reason=payload.get("grounding_reason"),
                    citation_retry_used=payload.get("citation_retry_used"),
                    citation_retry_succeeded=payload.get("citation_retry_succeeded"),
                    citation_automap_used=payload.get("citation_automap_used"),
                    citation_automap_succeeded=payload.get("citation_automap_succeeded"),
                    small_talk_bypass=payload.get("small_talk_bypass"),
                    status=payload.get("status") or "completed",
                )
                _persist_telemetry_with_new_session(
                    SessionLocal,
                    run_id=run_id,
                    telemetry_payload=telemetry_payload,
                    rerank_rows=payload.get("rerank_rows", []),
                    evidence_rows=payload.get("evidence_rows", []),
                    log=log,
                )

            for event in stream_grounded_answer(
                query=request.query,
                corpus=request.corpus,
                session=session,
                embedder=_EMBEDDER,
                reranker=_RERANKER,
                model=model_name,
                history=history_dicts,
                top_k=request.top_k,
                retrieval_mode=request.retrieval_mode,
                run_id=run_id,
                telemetry_hook=telemetry_hook,
            ):
                yield f"event: message\ndata: {json.dumps(event)}\n\n"
            log_event("stream_complete_sent", span="stream")

        except Exception as exc:  # noqa: BLE001
            status_code, error_type, user_message = categorize_error(exc)
            log.error("stream_failed", error=str(exc), error_type=error_type)
            log_event("stream_error_sent", span="stream", error_type=error_type)
            try:
                if run_id is None:
                    return
                telemetry_payload = _build_run_payload(
                    run_id=run_id,
                    endpoint="/api/query/stream",
                    user_session_id=user_session_id,
                    corpus=request.corpus,
                    query_text=request.query,
                    model_name=model_name,
                    top_k_requested=request.top_k,
                    error_type=error_type,
                    error_message=str(exc),
                    status="aborted",
                )
                _persist_telemetry_with_new_session(
                    SessionLocal,
                    run_id=run_id,
                    telemetry_payload=telemetry_payload,
                    log=log,
                )
            except Exception as telemetry_exc:  # noqa: BLE001
                log.warning("stream_abort_telemetry_failed", error=str(telemetry_exc))
            yield f"event: error\ndata: {json.dumps({'error': user_message, 'error_type': error_type})}\n\n"
        finally:
            if acquired:
                semaphore.release()
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/health", response_model=HealthResponse)
async def health(session: Session = Depends(get_db)) -> HealthResponse:
    setup_logging()
    log = logger.bind(function="api_health")
    config = get_config()
    services = {"postgres": "error", "qdrant": "error", "ollama": "error"}

    try:
        session.execute(text("SELECT 1"))
        services["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        log.error("health_postgres_error", error=str(exc))

    try:
        client = get_qdrant_client(timeout=5.0)
        client.get_collections()
        services["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        log.error("health_qdrant_error", error=str(exc))

    try:
        ollama_client = get_ollama_client(timeout=5)
        ollama_client.list()
        services["ollama"] = "ok"
    except Exception as exc:  # noqa: BLE001
        log.error("health_ollama_error", error=str(exc))

    if all(status == "ok" for status in services.values()):
        overall = "healthy"
    elif any(status == "ok" for status in services.values()):
        overall = "degraded"
    else:
        overall = "unhealthy"

    return HealthResponse(status=overall, services=services)


@router.get("/api/collections", response_model=CollectionsResponse)
async def get_collections(session: Session = Depends(get_db)) -> CollectionsResponse:
    setup_logging()
    log = logger.bind(function="api_collections")
    try:
        collections = []
        corpus_subq = (
            select(Corpus.id.label("corpus_id"), Corpus.name.label("corpus_name"))
            .where(Corpus.name.in_(CORPUS_IDS))
            .subquery()
        )
        lessons_subq = (
            select(
                FileTag.corpus_id.label("corpus_id"),
                func.count(func.distinct(TranscriptLesson.id)).label("lesson_count"),
            )
            .select_from(TranscriptLesson)
            .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
            .join(FileTag, SourceFile.file_tag_id == FileTag.id)
            .group_by(FileTag.corpus_id)
            .subquery()
        )
        parents_subq = (
            select(
                FileTag.corpus_id.label("corpus_id"),
                func.count(func.distinct(TranscriptParentChunk.id)).label("parent_count"),
            )
            .select_from(TranscriptParentChunk)
            .join(TranscriptLesson, TranscriptParentChunk.lesson_id == TranscriptLesson.id)
            .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
            .join(FileTag, SourceFile.file_tag_id == FileTag.id)
            .group_by(FileTag.corpus_id)
            .subquery()
        )
        children_subq = (
            select(
                FileTag.corpus_id.label("corpus_id"),
                func.count(func.distinct(TranscriptChildChunk.id)).label("child_count"),
            )
            .select_from(TranscriptChildChunk)
            .join(TranscriptParentChunk, TranscriptChildChunk.parent_chunk_id == TranscriptParentChunk.id)
            .join(TranscriptLesson, TranscriptParentChunk.lesson_id == TranscriptLesson.id)
            .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
            .join(FileTag, SourceFile.file_tag_id == FileTag.id)
            .group_by(FileTag.corpus_id)
            .subquery()
        )
        corpus_rows = session.execute(
            select(
                corpus_subq.c.corpus_id,
                corpus_subq.c.corpus_name,
                func.coalesce(lessons_subq.c.lesson_count, 0),
                func.coalesce(parents_subq.c.parent_count, 0),
                func.coalesce(children_subq.c.child_count, 0),
            )
            .select_from(corpus_subq)
            .outerjoin(lessons_subq, lessons_subq.c.corpus_id == corpus_subq.c.corpus_id)
            .outerjoin(parents_subq, parents_subq.c.corpus_id == corpus_subq.c.corpus_id)
            .outerjoin(children_subq, children_subq.c.corpus_id == corpus_subq.c.corpus_id)
            .order_by(corpus_subq.c.corpus_name.asc())
        ).all()
        for corpus_id, corpus_name, lesson_count, _parent_count, child_count in corpus_rows:
            collections.append(
                {
                    "name": corpus_name,
                    "display_name": CORPUS_REGISTRY[corpus_name].display_name if corpus_name in CORPUS_REGISTRY else corpus_name,
                    "lessons": lesson_count,
                    "chunks": child_count,
                    "status": "ready" if lesson_count else "empty",
                }
            )
        return CollectionsResponse(collections=collections)
    except Exception as exc:  # noqa: BLE001
        log.error("collections_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Collections query failed.") from exc


@router.get("/api/models")
async def get_models() -> dict:
    setup_logging()
    log = logger.bind(function="api_models")
    config = get_config()

    try:
        ollama_client = get_ollama_client(timeout=10)
        models_response = ollama_client.list()
        models = [model.get("name", model.get("model", "unknown")) for model in models_response.get("models", [])]
        return {"models": models, "default": config.ollama_model, "secondary": config.secondary_ollama_model}
    except Exception as exc:  # noqa: BLE001
        log.error("models_fetch_failed", error=str(exc))
        return {"models": [], "default": config.ollama_model, "secondary": config.secondary_ollama_model, "error": str(exc)}


@router.post("/api/models/pull")
def pull_model(request: ModelPullRequest) -> StreamingResponse:
    setup_logging()
    log = logger.bind(function="api_models_pull", model=request.name)

    def event_generator() -> Generator[str, None, None]:
        try:
            client = get_ollama_client(timeout=120)
            for chunk in client.pull(request.name, stream=True):
                yield json.dumps(chunk) + "\n"
        except Exception as exc:  # noqa: BLE001
            log.error("model_pull_failed", error=str(exc))
            yield json.dumps({"error": str(exc)}) + "\n"

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/models/keepalive")
def keepalive_model(request: ModelKeepAliveRequest) -> dict:
    setup_logging()
    log = logger.bind(function="api_models_keepalive", model=request.name)

    try:
        warm_ollama_model(request.name, keep_alive=request.keep_alive)
        return {"status": "ok", "model": request.name}
    except Exception as exc:  # noqa: BLE001
        log.error("model_keepalive_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to warm model.") from exc


def _parse_enrichment_response(raw_text: str) -> dict:
    if not raw_text:
        return {"summary": "", "relevance": ""}
    cleaned = raw_text.strip().strip("`")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {"summary": "", "relevance": ""}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"summary": "", "relevance": ""}
    summary = str(data.get("summary", "")).strip()
    relevance = str(data.get("relevance", "")).strip()
    return {"summary": summary, "relevance": relevance}


@router.post("/api/enrich_citations")
def enrich_citations(request: EnrichCitationsRequest) -> dict:
    setup_logging()
    log = logger.bind(function="api_enrich_citations")
    config = get_config()

    model_name = request.model or config.secondary_ollama_model or config.ollama_model
    if not request.citations:
        return {"model": model_name, "insights": {}}

    try:
        client = get_ollama_client(timeout=120)
        models_response = client.list()
        model_names = [m.get("name", m.get("model", "")) for m in models_response.get("models", [])]
        if model_name not in model_names:
            raise HTTPException(status_code=404, detail={"error": "model_not_found", "model": model_name})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("enrich_list_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to query Ollama models.") from exc

    def _enrich_one(item: CitationData) -> tuple[str, dict]:
        prompt = (
            "You are a helpful research assistant.\n"
            f"User Query: {request.query}\n"
            f"Source Text: {item.text}\n\n"
            "Task:\n"
            "Summarize: In 1 sentence, what is the main topic of this text?\n"
            "Relevance: In 1 sentence, explain exactly how this text answers the user's query.\n"
            "Return JSON only: {\"summary\": \"...\", \"relevance\": \"...\"}"
        )
        try:
            response = client.generate(
                model=model_name,
                prompt=prompt,
                options={"num_ctx": helper_num_ctx(model_name, 4096), "temperature": 0.2},
                keep_alive=config.ollama_keepalive,
            )
            raw_text = response.get("response", "")
            return item.id, _parse_enrichment_response(raw_text)
        except Exception as exc:  # noqa: BLE001
            log.error("enrich_generate_failed", error=str(exc), citation=item.id)
            return item.id, {"summary": "", "relevance": ""}

    from concurrent.futures import ThreadPoolExecutor

    insights: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_enrich_one, item): item for item in request.citations}
        for future in futures:
            citation_id, result = future.result()
            insights[citation_id] = result

    return {"model": model_name, "insights": insights}


@router.get("/api/stats", response_model=StatsResponse)
async def get_stats(session: Session = Depends(get_db)) -> StatsResponse:
    setup_logging()
    log = logger.bind(function="api_stats")
    config = get_config()
    try:
        total_lessons = session.execute(select(func.count(TranscriptLesson.id))).scalar() or 0
        total_parents = session.execute(select(func.count(TranscriptParentChunk.id))).scalar() or 0
        total_children = session.execute(select(func.count(TranscriptChildChunk.id))).scalar() or 0
        total_embeddings = session.execute(
            select(func.count(TranscriptChildChunk.id)).where(TranscriptChildChunk.embedding.is_not(None))
        ).scalar() or 0

        qdrant_points_by_corpus: dict[str, int] = {}
        try:
            client = get_qdrant_client(timeout=5.0)
            for corpus_name in CORPUS_IDS:
                collection_name = f"{corpus_name}_chunks"
                try:
                    count = client.count(collection_name=collection_name, exact=True).count
                except Exception as exc:  # noqa: BLE001
                    log.error("stats_qdrant_corpus_error", corpus=corpus_name, error=str(exc))
                    count = 0
                qdrant_points_by_corpus[corpus_name] = int(count or 0)
        except Exception as exc:  # noqa: BLE001
            log.error("stats_qdrant_error", error=str(exc))
        qdrant_points = sum(qdrant_points_by_corpus.values())

        return StatsResponse(
            total_lessons=total_lessons,
            total_parent_chunks=total_parents,
            total_child_chunks=total_children,
            total_embeddings=total_embeddings,
            qdrant_points=qdrant_points,
            qdrant_points_by_corpus=qdrant_points_by_corpus,
            models_loaded={
                "embedding": config.embedding_model,
                "reranker": config.reranker_model,
                "generation": config.ollama_model,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.error("stats_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Stats query failed.") from exc


@router.get("/api/evidence/{evidence_id}", response_model=EvidenceResponse)
def get_evidence(evidence_id: str, session: Session = Depends(get_db)) -> EvidenceResponse:
    setup_logging()
    log = logger.bind(function="api_evidence")
    try:
        child = session.execute(
            select(TranscriptChildChunk).where(TranscriptChildChunk.id == evidence_id)
        ).scalar_one_or_none()
        if not child:
            raise HTTPException(status_code=404, detail="Evidence not found.")

        parent = session.execute(
            select(TranscriptParentChunk).where(TranscriptParentChunk.id == child.parent_chunk_id)
        ).scalar_one()

        lesson_row = session.execute(
            select(
                TranscriptLesson.lesson_number,
                TranscriptLesson.date,
                TranscriptLesson.speaker,
                TranscriptLesson.canonical_html_path,
                TranscriptPart.part_number,
                TranscriptPart.title,
                Series.title,
                DeanCollection.name,
                DeanBook.display_name,
                DeanLesson.lesson_code,
            )
            .join(TranscriptChapter, TranscriptLesson.chapter_id == TranscriptChapter.id, isouter=True)
            .join(TranscriptPart, func.coalesce(TranscriptChapter.part_id, TranscriptLesson.part_id) == TranscriptPart.id, isouter=True)
            .join(Series, TranscriptPart.series_id == Series.id, isouter=True)
            .join(DeanLesson, TranscriptLesson.dean_lesson_id == DeanLesson.id, isouter=True)
            .join(DeanBook, DeanLesson.book_id == DeanBook.id, isouter=True)
            .join(DeanCollection, DeanBook.collection_id == DeanCollection.id, isouter=True)
            .where(TranscriptLesson.id == parent.lesson_id)
        ).first()

        lesson_number = lesson_row[0] if lesson_row else 0
        part_number = lesson_row[4] if lesson_row else None
        part_title = lesson_row[5] if lesson_row else None
        series_title = lesson_row[6] if lesson_row else ""
        date_str = lesson_row[1].isoformat() if lesson_row and lesson_row[1] else ""
        speaker = lesson_row[2] if lesson_row else ""
        file_path = lesson_row[3] if lesson_row else ""
        canonical_rel_path = _canonical_rel_path(file_path)
        canonical_url = _canonical_url(file_path)
        dean_collection_name = lesson_row[7] if lesson_row else None
        dean_book_name = lesson_row[8] if lesson_row else None
        dean_lesson_code = lesson_row[9] if lesson_row else None

        lesson_for_path: int | str = lesson_number
        lesson_text = lesson_label(lesson_for_path)
        label = f"{lesson_text} · Chunk {child.chunk_index}"
        part_label = f"Part {part_number}: {part_title}" if part_number is not None and part_title else ""
        series_for_path = series_title
        part_for_path = part_label
        if part_label:
            part_for_path = part_label
        if dean_collection_name:
            series_for_path = dean_collection_name
            part_for_path = dean_book_name or ""
            lesson_for_path = dean_lesson_code or lesson_number
            lesson_text = lesson_label(lesson_for_path)
            label = f"{lesson_text} · Chunk {child.chunk_index}"

        before = (
            session.execute(
                select(TranscriptChildChunk.content)
                .where(TranscriptChildChunk.parent_chunk_id == child.parent_chunk_id)
                .where(TranscriptChildChunk.chunk_index < child.chunk_index)
                .order_by(TranscriptChildChunk.chunk_index.desc())
                .limit(2)
            )
            .scalars()
            .all()
        )
        after = (
            session.execute(
                select(TranscriptChildChunk.content)
                .where(TranscriptChildChunk.parent_chunk_id == child.parent_chunk_id)
                .where(TranscriptChildChunk.chunk_index > child.chunk_index)
                .order_by(TranscriptChildChunk.chunk_index.asc())
                .limit(2)
            )
            .scalars()
            .all()
        )

        return EvidenceResponse(
            evidence_id=str(child.id),
            parent_id=str(child.parent_chunk_id),
            label=label,
            path=build_citation_path(series_for_path or "", part_for_path, lesson_for_path),
            primary_text=child.content,
            context_before=list(reversed(before)),
            context_after=after,
            metadata={
                "series": dean_collection_name or series_title,
                "part": dean_book_name or part_label,
                "lesson": dean_lesson_code or lesson_number,
                "date": date_str,
                "speaker": speaker,
                "file_path": file_path,
                "canonical_rel_path": canonical_rel_path,
                "canonical_url": canonical_url,
                "citation_ref": parent.citation_ref,
                "section_title": parent.section_title,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("evidence_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to load evidence.") from exc
