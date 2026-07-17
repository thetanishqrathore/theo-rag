"""Pydantic models for API request/response payloads."""

from __future__ import annotations

from typing import List, Union, Literal

from pydantic import BaseModel, Field

from src.corpus.registry import CorpusId


class ConversationTurn(BaseModel):
    """A single turn in the conversation history."""

    role: str = Field(..., description="Either 'user' or 'assistant'.")
    content: str = Field(..., description="The message content.")


class QueryRequest(BaseModel):
    """Request payload for the main query endpoint."""

    query: str = Field(..., description="User query text.")
    corpus: CorpusId = Field(..., description="Corpus id ('clough' | 'dean').")
    top_k: int = Field(
        5,
        ge=1,
        le=20,
        description="Number of primary evidence chunks to retrieve.",
    )
    model: str | None = Field(
        None,
        description="Optional Ollama model name for generation.",
    )
    retrieval_mode: Literal["fast", "deep"] | None = Field(
        None,
        description="Optional retrieval mode for Qdrant (fast|deep).",
    )
    history: List[ConversationTurn] = Field(
        default_factory=list,
        description="Previous conversation turns for context.",
    )


class CitationData(BaseModel):
    """Citation metadata for a retrieved child chunk (canonical)."""

    series: str
    part: str
    lesson: Union[int, str]
    date: str
    speaker: str
    child_chunk_id: str
    parent_chunk_id: str
    child_chunk_index: int | None = None
    is_primary: bool = True
    context_only: bool = False
    source_tag: str | None = None
    collection: str | None = None
    book_slug: str | None = None
    lesson_code: str | None = None
    node_path: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    is_front_matter: bool = False
    citation_ref: str | None = None
    full_text: str
    parent_text: str | None = None
    child_text: str | None = None
    file_path: str


class QueryResponse(BaseModel):
    """Response payload for the main query endpoint."""

    answer: str
    grounded: bool
    refusal: bool
    citations: List[CitationData]
    source_count: int
    retrieval_time_ms: float
    generation_time_ms: float
    follow_up_questions: List[str] = []
    warmup_ms: float | None = None
    model_warmup_performed: bool | None = None
    grounding_reason: str | None = None
    citation_retry_used: bool | None = None
    citation_retry_succeeded: bool | None = None
    citation_automap_used: bool | None = None
    citation_automap_succeeded: bool | None = None
    citation_fallback_reason: str | None = None
    citation_strategy_version: str | None = None
    num_citations_emitted: int | None = None
    num_citations_matched: int | None = None


class HealthResponse(BaseModel):
    """Response payload for health checks."""

    status: str
    services: dict


class CollectionsResponse(BaseModel):
    """Response payload for available corpora."""

    collections: list


class StatsResponse(BaseModel):
    """Response payload for system statistics."""

    total_lessons: int
    total_parent_chunks: int
    total_child_chunks: int
    total_embeddings: int
    qdrant_points: int
    qdrant_points_by_corpus: dict[str, int] = Field(default_factory=dict)
    models_loaded: dict


class EvidenceResponse(BaseModel):
    """Response payload for evidence inspector."""

    evidence_id: str
    parent_id: str
    label: str
    path: str
    primary_text: str
    context_before: List[str]
    context_after: List[str]
    metadata: dict


class ModelPullRequest(BaseModel):
    """Request payload to pull a model from Ollama."""

    name: str = Field(..., description="Model name or tag to pull.")


class ModelKeepAliveRequest(BaseModel):
    """Request payload to keep a model warm in Ollama."""

    name: str = Field(..., description="Model name to keep alive.")
    keep_alive: str = Field("5m", description="Keep-alive duration, e.g. '5m'.")


class EnrichCitationItem(BaseModel):
    """Single citation to enrich."""

    id: str = Field(..., description="Citation chunk id.")
    text: str = Field(..., description="Citation text to analyze (child chunk preferred).")


class EnrichCitationsRequest(BaseModel):
    """Batch enrichment request payload."""

    query: str = Field(..., description="User query text.")
    citations: List[EnrichCitationItem] = Field(default_factory=list)
    model: str | None = Field(None, description="Optional enrichment model override.")
