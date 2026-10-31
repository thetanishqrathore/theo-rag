"""Grounded answer generation using Ollama with strict citation rules."""

from __future__ import annotations

import re
import time
import json
import math
import threading
from typing import Optional, List, Dict, Generator, Callable, Iterable

import ollama
import structlog
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sentence_transformers import SentenceTransformer, CrossEncoder

from src.retrieval.models import Citation, SearchResult
from src.retrieval.citation_utils import build_citation_path, lesson_label
from src.retrieval.retrieve import retrieve_context_with_stats
from src.observability.logging import log_event
from src.observability.timer import SpanTimer
from src.utils.config import get_config
from src.utils.logging import setup_logging
from src.utils.ollama_client import get_ollama_client, helper_num_ctx
from src.utils.text import STOPWORDS

logger = structlog.get_logger()

REFUSAL_TEXT = "I cannot support that claim from this corpus."
_CHUNK_ID_PATTERN = r"[0-9a-fA-F-]{8,}"
_CHUNK_ORDINAL_PATTERN = r"\d{1,3}"
_CHUNK_REF_PATTERN = rf"(?:{_CHUNK_ID_PATTERN}|{_CHUNK_ORDINAL_PATTERN})"
CITATION_STRATEGY_VERSION = "recall-first-single-profile-v1"
# Seconds between status heartbeats while a reasoning model streams only its reasoning.
REASONING_HEARTBEAT_SEC = 5.0
_SMALL_TALK_EXACT = {
    "hi",
    "hello",
    "hey",
    "yo",
    "sup",
    "thanks",
    "thank you",
    "ok",
    "okay",
}
_SMALL_TALK_PATTERNS = [
    re.compile(r"^\s*(hello|hi|hey|yo|sup)[\s!.,?]*$", re.IGNORECASE),
    re.compile(r"^\s*how are you[\s!.,?]*$", re.IGNORECASE),
    re.compile(r"^\s*good (morning|afternoon|evening)[\s!.,?]*$", re.IGNORECASE),
    re.compile(r"^\s*(thank you|thanks)[\s!.,?]*$", re.IGNORECASE),
]
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")
_INLINE_CITATION_PATTERN = re.compile(
    rf"\[chunk:{_CHUNK_REF_PATTERN}\]|\[SOURCE\s+\d+\]",
    re.IGNORECASE,
)

class GenerationResult(BaseModel):
    """Result schema for grounded generation."""

    answer: str
    citations: List[Citation]
    grounding_score: Optional[bool] = None
    follow_up_questions: List[str] = []
    answer_mode: Optional[str] = None
    sufficiency_metrics: Optional[dict] = None
    citation_items: List[dict] = []
    used_evidence_ids: List[str] = []
    matched_evidence_ids: List[str] = []
    refusal: bool = False
    grounding_reason: Optional[str] = None
    citation_retry_used: bool = False
    citation_retry_succeeded: bool = False
    citation_automap_used: bool = False
    citation_automap_succeeded: bool = False
    citation_fallback_reason: str | None = None
    citation_strategy_version: str | None = None
    num_citations_emitted: int | None = None
    num_citations_matched: int | None = None
    citation_guardrail_triggered: bool | None = None


def ensure_ollama_running(client: ollama.Client) -> None:
    try:
        client.list()
    except Exception as exc:  # noqa: BLE001
        raise ConnectionError(
            "Ollama is not reachable. Ensure the Ollama service is running and accessible."
        ) from exc


def is_small_talk_query(query: str) -> bool:
    config = get_config()
    if not config.rag_small_talk_bypass_enabled:
        return False
    normalized = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not normalized:
        return False
    if normalized in _SMALL_TALK_EXACT:
        return True
    if len(normalized.split()) > 6:
        return False
    return any(pattern.match(normalized) for pattern in _SMALL_TALK_PATTERNS)


def build_small_talk_reply(corpus: str) -> tuple[str, list[str]]:
    corpus_label = "Clough and Dean corpora" if corpus == "both" else f"{corpus} corpus"
    answer = (
        f"Hello. I can help with questions grounded in the {corpus_label}. "
        "Ask a specific topic, verse, or doctrine and I will answer with evidence."
    )
    follow_ups = [
        "What does the corpus teach about grace and faith?",
        "Can you summarize one lesson with citations?",
        "Show me where this corpus discusses Israel and covenant.",
    ]
    return answer, follow_ups


def format_citation(citation: Citation) -> str:
    return (
        f'Series="{citation.series}", Part="{citation.part}", '
        f'Lesson="{citation.lesson}", Date="{citation.date}", Speaker="{citation.speaker}"'
    )


def _build_sources_section(parent_chunks: List[SearchResult], use_chunk_ids: bool) -> list[str]:
    lines = [
        "SOURCES:",
        "---",
        "Use citations from PRIMARY sources when possible.",
        "Context-only sources provide surrounding context and should not be used as the sole basis for a claim.",
    ]
    for idx, chunk in enumerate(parent_chunks, start=1):
        label = f"[chunk:{chunk.child_chunk_id}]" if use_chunk_ids else f"[SOURCE {idx}]"
        if chunk.context_only:
            label = f"{label} (context-only)"
        lines.append(label)
        lines.append(chunk.child_text or chunk.parent_text)
        lines.append(f"Citation: {format_citation(chunk.citation)}")
        if chunk.citation_ref:
            lines.append(f"Ref: {chunk.citation_ref}")
        lines.append("---")
    return lines


def build_grounded_messages(
    query: str,
    parent_chunks: List[SearchResult],
    corpus: str,
    history: Optional[List[Dict[str, str]]] = None,
    evidence_mode: str = "confident",
) -> tuple[str, str]:
    """Build system and user messages for chat-based generation.

    Returns:
        Tuple of (system_message, user_message).
    """
    if not parent_chunks:
        raise ValueError("No parent chunks provided for prompt construction.")

    primary_citation = parent_chunks[0].citation
    speaker = primary_citation.speaker

    config = get_config()
    citation_format = "[chunk:CHUNK_ID]" if config.rag_citations_child_canonical else "[SOURCE X]"
    if config.rag_citations_child_canonical:
        citation_example_correct = (
            "   - Correct: 'Faith is a mechanic [chunk:11111111-1111-1111-1111-111111111111], "
            "distinct from belief [chunk:22222222-2222-2222-2222-222222222222].'"
        )
        citation_example_incorrect = (
            "   - Incorrect: 'Faith is a mechanic distinct from belief "
            "[chunk:11111111-1111-1111-1111-111111111111] [chunk:22222222-2222-2222-2222-222222222222].'"
        )
    else:
        citation_example_correct = (
            "   - Correct: 'Faith is a mechanic [SOURCE 1], distinct from belief [SOURCE 2].'"
        )
        citation_example_incorrect = (
            "   - Incorrect: 'Faith is a mechanic distinct from belief [SOURCE 1] [SOURCE 2].'"
        )

    # A1: Corpus-aware source hierarchy
    if corpus == "clough":
        source_hierarchy = (
            "### SOURCE HIERARCHY (How to use the text):\n"
            "1. **Course Notes (High Authority):** Use these to define terms, outlines, and core doctrines. They are your 'Skeleton'.\n"
            "2. **Transcripts (Context):** Use these to explain the definitions, give examples, and show practical application. They are your 'Meat'.\n"
            "3. **Synthesis:** Do not just list quotes. Combine the Notes' structure with the Transcripts' explanation into a cohesive narrative.\n\n"
        )
    elif corpus == "dean":
        source_hierarchy = (
            "### SOURCE HIERARCHY (How to use the text):\n"
            "1. **Lesson Transcripts & Notes (Primary):** Use these as the authoritative source for doctrinal positions, definitions, and arguments.\n"
            "2. **Topical Studies & Articles (Supporting):** Use these to supplement, illustrate, or provide additional depth on specific topics.\n"
            "3. **Synthesis:** Do not just list quotes. Weave together the primary lesson content with supporting material into a cohesive narrative.\n\n"
        )
    else:
        source_hierarchy = (
            "### SOURCE HIERARCHY (How to use the text):\n"
            "Synthesize all provided sources into a cohesive narrative. Prioritize sources that most directly address the question.\n\n"
        )

    system_message = (
        f"You are an expert theological assistant answering questions based on christian teachings from {speaker}.\n"
        "Your goal is to provide a precise, structured, and readable answer that synthesizes the provided search results.\n\n"

        + source_hierarchy +

        "### CITATION RULES (Critical):\n"
        f"1. **Immediate Citation:** You must cite every single claim immediately after the sentence that contains it, using {citation_format}.\n"
        f"{citation_example_correct}\n"
        f"{citation_example_incorrect}\n"
        "2. **Granularity:** If a sentence combines ideas from two sources, cite both sources at the relevant parts of the sentence.\n\n"
        "3. **No Placeholders:** Never output placeholder markers such as [·], [citation], or [ref].\n\n"

        "### FORMATTING & READABILITY (Make it Spacious):\n"
        "1. **No Walls of Text:** Keep paragraphs short (maximum 3-4 sentences). Use double newlines between paragraphs.\n"
        "2. **Use Headers:** If the answer covers multiple distinct concepts, use Markdown headers (###) to separate them.\n"
        "3. **Visual Emphasis:** Use **bold** for key theological terms or definitions. Use *italics* for emphasis.\n"
        "4. **Lists:** Use bullet points or numbered lists whenever presenting steps, reasons, or multiple examples.\n\n"

        # A2: Study-oriented content rules
        "### CONTENT RULES:\n"
        f"1. **Objective Reporting:** Report what the text says using attributive language (e.g., '{speaker} teaches that...', '{speaker} argues that...'). Do not preach or editorialize.\n"
        "2. **Theological Precision:** When explaining a term or concept, give the teacher's specific definition first, then explain its significance and context within the broader teaching.\n"
        "3. **Cross-Lesson Awareness:** When multiple sources address the same topic, note areas of agreement. Mention the lesson context (e.g., series or lesson number) when it helps the reader locate the teaching.\n"
        "4. **Scope Honesty:** If the retrieved evidence only covers some aspects of the question, state explicitly what aspects are NOT covered rather than speculating beyond the sources.\n"
        "5. **Handling Silence:** If the sources do not explicitly support the core of the user's question, you must respond exactly:\n"
        f'   "{REFUSAL_TEXT}"\n\n'

        # A3: Answer structure guidance
        "### ANSWER STRUCTURE GUIDANCE:\n"
        "Adapt your answer structure to the question type:\n"
        "- **Definition questions** (e.g., 'What is...', 'Define...'): Lead with the teacher's definition, then provide context, significance, and any related distinctions.\n"
        "- **Doctrinal position questions** (e.g., 'What does X teach about...'): State the position clearly first, then present the reasoning and scriptural support the teacher uses.\n"
        "- **Verse/passage questions** (e.g., 'What does X say about Genesis 3...'): Follow the teacher's exposition flow — observation, interpretation, then application if given.\n"
        "- **Comparison questions** (e.g., 'How does X differ from...'): Use a structured comparison — present each position clearly, then highlight key differences.\n"
        "- **All answers:** End with a brief concluding sentence that ties the main points together.\n"
    )
    if evidence_mode == "narrow":
        system_message += (
            "\nEVIDENCE MODE: NARROW.\n"
            "Only answer the part of the question explicitly supported by the sources. "
            "If the question has multiple parts, state which parts are unsupported.\n"
        )

    sources = _build_sources_section(parent_chunks, use_chunk_ids=config.rag_citations_child_canonical)

    history_section = ""
    if history and len(history) > 0:
        history_lines = ["\nCONVERSATION HISTORY (for context):"]
        recent_history = history[-6:]
        for turn in recent_history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            if role == "user":
                history_lines.append(f"User: {content}")
            else:
                history_lines.append(f"Assistant: {content}")
        history_lines.append("---\n")
        history_section = "\n".join(history_lines)

    user_message = "\n".join([
        *sources,
        history_section,
        f"QUESTION: {query}",
        "",
        f"ANSWER (with citations {citation_format}):",
    ])

    # Context window guard: trim lowest-ranked sources if prompt exceeds budget
    max_prompt_tokens = config.ollama_num_ctx - 2048
    total_words = len(system_message.split()) + len(user_message.split())
    estimated_tokens = int(total_words * 1.3)
    if estimated_tokens > max_prompt_tokens and len(parent_chunks) > 1:
        trimmed_count = 0
        while estimated_tokens > max_prompt_tokens and len(parent_chunks) > 1:
            parent_chunks = parent_chunks[:-1]
            trimmed_count += 1
            sources = _build_sources_section(parent_chunks, use_chunk_ids=config.rag_citations_child_canonical)
            user_message = "\n".join([
                *sources,
                history_section,
                f"QUESTION: {query}",
                "",
                f"ANSWER (with citations {citation_format}):",
            ])
            total_words = len(system_message.split()) + len(user_message.split())
            estimated_tokens = int(total_words * 1.3)
        logger.warning(
            "context_window_trimmed",
            trimmed_sources=trimmed_count,
            remaining_sources=len(parent_chunks),
            estimated_tokens=estimated_tokens,
            max_prompt_tokens=max_prompt_tokens,
        )

    return system_message, user_message


def _tokenize(text: str) -> list[str]:
    tokens = [t.lower() for t in re.findall(r"[A-Za-z']+", text)]
    return [t for t in tokens if t not in STOPWORDS]


def lexical_overlap(query: str, text: str) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokenize(text))
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    return len(overlap) / max(1, len(query_tokens))


def _sigmoid(value: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-value))
    except OverflowError:
        return 0.0 if value < 0 else 1.0


def _sufficiency_thresholds_for_corpus(corpus: str, config) -> tuple[float, float, float, float, float]:
    thresholds = config.sufficiency.for_corpus(corpus)
    return (
        thresholds.t_high,
        thresholds.t_mid,
        thresholds.t_low,
        thresholds.o_mid,
        thresholds.o_low,
    )


def evaluate_sufficiency(query: str, parent_chunks: List[SearchResult], corpus: str) -> tuple[str, dict]:
    primary_chunks = [c for c in parent_chunks if c.is_primary]
    if not primary_chunks:
        return "refuse", {"reason": "no_primary_chunks"}
    scores = [c.rerank_score or 0.0 for c in primary_chunks]
    if not scores:
        return "refuse", {"reason": "no_scores"}

    s1_raw = scores[0]
    s5_raw = scores[4] if len(scores) >= 5 else scores[-1]
    s1 = _sigmoid(s1_raw)
    s5 = _sigmoid(s5_raw)
    top_text = primary_chunks[0].child_text or primary_chunks[0].parent_text
    overlap = lexical_overlap(query, top_text)

    config = get_config()
    t_high, t_mid, t_low, o_mid, o_low = _sufficiency_thresholds_for_corpus(corpus, config)
    metrics = {
        "s1": s1,
        "s5": s5,
        "overlap": overlap,
        "s1_raw": s1_raw,
        "s5_raw": s5_raw,
        "thresholds": {
            "t_high": t_high,
            "t_mid": t_mid,
            "t_low": t_low,
            "o_mid": o_mid,
            "o_low": o_low,
        },
    }

    if s1 >= t_high or (s1 >= t_mid and overlap >= o_mid):
        return "confident", metrics
    if s1 < t_low and overlap < o_low:
        return "refuse", metrics
    if len(parent_chunks) <= 1 and s1 < t_high:
        return "narrow", metrics
    return "narrow", metrics


def generate_answer(
    prompt: str,
    client: ollama.Client,
    model: str,
    num_ctx: int | None = None,
    temperature: float = 0.1,
    keep_alive: str = "5m",
    system_message: str | None = None,
    user_message: str | None = None,
) -> str:
    if num_ctx is None:
        num_ctx = get_config().ollama_num_ctx
    if system_message is not None and user_message is not None:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
            options={"num_ctx": num_ctx, "temperature": temperature},
            keep_alive=keep_alive,
        )
        text = response.get("message", {}).get("content", "")
    else:
        response = client.generate(
            model=model,
            prompt=prompt,
            options={"num_ctx": num_ctx, "temperature": temperature},
            keep_alive=keep_alive,
        )
        text = response.get("response", "")
    if not text.strip():
        raise RuntimeError("Ollama returned an empty response.")
    return text.strip()


def build_source_map(parent_chunks: List[SearchResult]) -> Dict[str, dict]:
    source_map: Dict[str, dict] = {}
    order: list[str] = []
    for chunk in parent_chunks:
        order.append(chunk.child_chunk_id)
        source_map[chunk.child_chunk_id] = {
            "child_chunk_id": chunk.child_chunk_id,
            "parent_chunk_id": chunk.parent_chunk_id,
            "child_chunk_index": chunk.child_chunk_index,
            "source_tag": chunk.source_tag,
            "collection": chunk.collection,
            "book_slug": chunk.book_slug,
            "lesson_code": chunk.lesson_code,
            "node_path": chunk.node_path,
            "section_path": chunk.section_path,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "is_front_matter": chunk.is_front_matter,
            "section_title": chunk.section_title,
            "citation_ref": chunk.citation_ref,
            "citation": chunk.citation,
            "parent_text": chunk.parent_text,
            "child_text": chunk.child_text,
            "file_path": chunk.citation.file_path,
            "is_primary": chunk.is_primary,
            "context_only": chunk.context_only,
            "rerank_score": chunk.rerank_score,
        }
    source_map["__order__"] = order
    return source_map


def extract_follow_up_questions(answer_text: str) -> tuple[str, List[str]]:
    pattern = re.compile(r"FOLLOW-UP QUESTIONS:\s*\n((?:\d+\.\s*.+\n?)+)", re.IGNORECASE)
    match = pattern.search(answer_text)

    if not match:
        return answer_text, []

    questions_text = match.group(1)
    questions = []
    for line in questions_text.strip().split('\n'):
        cleaned = re.sub(r"^\d+\.\s*|\-\s*", "", line.strip())
        if cleaned:
            questions.append(cleaned)

    clean_answer = answer_text[:match.start()].strip()

    return clean_answer, questions[:3]


def _truncate(text: str, limit: int = 320) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _rerank_usefulness_prob(score: float | None) -> float:
    """Convert reranker logit to [0,1] usefulness probability via sigmoid."""
    if score is None:
        return 0.0
    return _sigmoid(float(score))


def _build_stream_telemetry_payload(
    *,
    run_id: str | None,
    answer_mode: str,
    grounded: bool,
    refusal: bool,
    generation_time_ms: float,
    retrieval_time_ms: float,
    t_post_ms: float,
    t_total_ms: float,
    retrieval_stats: dict,
    sufficiency_metrics: dict,
    num_citations_emitted: int,
    num_citations_matched: int,
    used_evidence_ids: list[str],
    rerank_rows: list[dict],
    evidence_rows: list[dict],
    grounding_reason: str | None = None,
    citation_retry_used: bool = False,
    citation_retry_succeeded: bool = False,
    citation_automap_used: bool = False,
    citation_automap_succeeded: bool = False,
    citation_fallback_reason: str | None = None,
    citation_strategy_version: str | None = None,
    small_talk_bypass: bool = False,
    status: str | None = None,
) -> dict:
    payload = {
        "run_id": run_id,
        "answer_mode": answer_mode,
        "grounded": grounded,
        "refusal": refusal,
        "generation_time_ms": generation_time_ms,
        "retrieval_time_ms": retrieval_time_ms,
        "t_post_ms": t_post_ms,
        "t_total_ms": t_total_ms,
        "retrieval_stats": retrieval_stats,
        "sufficiency": {"mode": answer_mode, "metrics": sufficiency_metrics},
        "num_citations_emitted": num_citations_emitted,
        "num_citations_matched": num_citations_matched,
        "used_evidence_ids": used_evidence_ids,
        "rerank_rows": rerank_rows,
        "evidence_rows": evidence_rows,
        "grounding_reason": grounding_reason,
        "citation_retry_used": citation_retry_used,
        "citation_retry_succeeded": citation_retry_succeeded,
        "citation_automap_used": citation_automap_used,
        "citation_automap_succeeded": citation_automap_succeeded,
        "citation_fallback_reason": citation_fallback_reason,
        "citation_strategy_version": citation_strategy_version,
        "small_talk_bypass": small_talk_bypass,
    }
    if status:
        payload["status"] = status
    return payload


def _filter_stream_citation_chunks(
    chunks: List[SearchResult],
    min_prob: float = 0.5,
    min_keep: int = 3,
) -> List[SearchResult]:
    """Keep only useful citation candidates; ensure a small fallback floor."""
    primary_chunks = [c for c in chunks if c.is_primary and not c.context_only]
    candidates = primary_chunks or chunks
    kept = [c for c in candidates if _rerank_usefulness_prob(c.rerank_score) >= min_prob]
    if kept:
        return kept
    # Fallback: keep top few primary chunks so sources never disappear entirely on borderline scores.
    return candidates[:min_keep]


def _build_citation_card_payload(
    chunk: SearchResult,
    *,
    force_context_only: bool = False,
) -> dict:
    lesson_text = lesson_label(chunk.citation.lesson)
    label = (
        f"{lesson_text} · Chunk {chunk.child_chunk_index}"
        if chunk.child_chunk_index is not None
        else lesson_text
    )
    return {
        "evidence_id": str(chunk.child_chunk_id),
        "chunk_id": str(chunk.child_chunk_id),
        "parent_id": str(chunk.parent_chunk_id),
        "parent_chunk_id": str(chunk.parent_chunk_id),
        "label": label,
        "path": build_citation_path(
            chunk.citation.series,
            chunk.citation.part,
            chunk.citation.lesson,
        ),
        "lesson": chunk.citation.lesson,
        "date": chunk.citation.date,
        "speaker": chunk.citation.speaker,
        "child_chunk_index": chunk.child_chunk_index,
        "is_primary": bool(chunk.is_primary),
        "context_only": bool(force_context_only or chunk.context_only),
        "source_tag": chunk.source_tag,
        "collection": chunk.collection,
        "book_slug": chunk.book_slug,
        "lesson_code": chunk.lesson_code,
        "node_path": chunk.node_path,
        "section_path": chunk.section_path,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "is_front_matter": bool(chunk.is_front_matter),
        "citation_ref": chunk.citation_ref,
        "full_text": chunk.parent_text or "",
        "parent_text": chunk.parent_text,
        "child_text": chunk.child_text,
        "file_path": chunk.citation.file_path,
        "preview": _truncate(chunk.child_text or chunk.parent_text or "", 320),
    }


def build_grounding_citation_payloads(
    *,
    parent_chunks: List[SearchResult],
    used_evidence_ids: List[str],
    answer_mode: str,
    refusal: bool,
    min_prob: float = 0.5,
    min_keep: int = 3,
    max_cards: int = 3,
    strategy_version: str = CITATION_STRATEGY_VERSION,
) -> dict[str, list[dict] | list[str] | str | None | int]:
    chunk_by_id = {str(chunk.child_chunk_id): chunk for chunk in parent_chunks}
    resolved_ids: list[str] = []
    resolved_chunks: list[SearchResult] = []
    seen = set()
    for raw_chunk_id in used_evidence_ids:
        chunk_id = str(raw_chunk_id)
        if chunk_id in seen:
            continue
        chunk = chunk_by_id.get(chunk_id)
        if not chunk:
            continue
        resolved_ids.append(chunk_id)
        resolved_chunks.append(chunk)
        seen.add(chunk_id)

    matched_ids = list(resolved_ids)
    fallback_reason: str | None = None
    if not resolved_chunks and not refusal and answer_mode == "narrow":
        fallback_chunks = _filter_stream_citation_chunks(
            parent_chunks,
            min_prob=min_prob,
            min_keep=min_keep,
        )[:max_cards]
        if fallback_chunks:
            fallback_reason = "narrow_answer_no_primary_matches_found"
            for chunk in fallback_chunks:
                chunk_id = str(chunk.child_chunk_id)
                if chunk_id in seen:
                    continue
                resolved_ids.append(chunk_id)
                resolved_chunks.append(chunk)
                seen.add(chunk_id)

    # Every source the answer cites gets a card, so each [chunk:] reference in the
    # answer resolves for the client; max_cards only bounds the context-only
    # fallback cards shown for an uncited narrow answer.
    citation_cards = [
        _build_citation_card_payload(chunk, force_context_only=bool(fallback_reason))
        for chunk in resolved_chunks
    ]
    return {
        "citations": citation_cards,
        "used_evidence_ids": resolved_ids,
        "matched_evidence_ids": matched_ids,
        "citation_fallback_reason": fallback_reason,
        "citation_strategy_version": strategy_version,
        "num_citations_emitted": len(citation_cards),
        "num_citations_matched": len(matched_ids),
    }


def generate_followups(
    query: str,
    parent_chunks: List[SearchResult],
    client: ollama.Client,
    model: str,
    keep_alive: str = "5m",
) -> List[str]:
    """Generate 2-3 follow-up questions using a small model; fallback to heuristics on failure."""
    snippets = []
    for chunk in parent_chunks:
        if chunk.child_text:
            snippets.append(_truncate(chunk.child_text, 240))
        elif chunk.parent_text:
            snippets.append(_truncate(chunk.parent_text, 240))
        if len(snippets) >= 3:
            break

    if not snippets:
        return []

    prompt = (
        "You are generating follow-up questions for a theological study assistant.\n"
        "Given the user question and evidence snippets, produce 3 follow-up questions that help deepen understanding.\n"
        "Guidelines:\n"
        "- One question should explore a related theological concept mentioned in the evidence.\n"
        "- One question should ask about practical application or implications of the teaching.\n"
        "- One question should probe a different aspect or related passage connected to the topic.\n"
        "- Keep questions specific enough to produce good retrieval results from the corpus.\n"
        "Return ONLY a JSON array of 3 strings.\n\n"
        f"USER QUESTION: {query}\n\n"
        "EVIDENCE SNIPPETS:\n"
        + "\n".join(f"- {s}" for s in snippets)
        + "\n\nJSON ARRAY:"
    )
    try:
        response = client.generate(
            model=model,
            prompt=prompt,
            options={"num_ctx": helper_num_ctx(model, 2048), "temperature": 0.2},
            keep_alive=keep_alive,
        )
        raw = response.get("response", "").strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            cleaned = [str(item).strip() for item in parsed if str(item).strip()]
            return cleaned[:3]
    except Exception:  # noqa: BLE001
        pass

    # fallback: simple heuristic
    base = query.strip().rstrip("?")
    if not base:
        return []
    return [
        f"What related theological concepts are connected to {base}?",
        f"How is {base} applied practically according to the teaching?",
        f"What scriptural passages are discussed in relation to {base}?",
    ]


def normalize_citation_markup(answer_text: str) -> str:
    text = answer_text or ""
    # Normalize common CHUNK variants into canonical [chunk:UUID] form.
    text = re.sub(
        rf"\[\s*chunk\s+({_CHUNK_REF_PATTERN})\s*\]",
        lambda m: f"[chunk:{m.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\[\s*chunk\s*:\s*({_CHUNK_REF_PATTERN})\s*\]",
        lambda m: f"[chunk:{m.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\bchunk\s+id\s*[:#-]?\s*({_CHUNK_REF_PATTERN})\b",
        lambda m: f"[chunk:{m.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\bchunk\s+({_CHUNK_REF_PATTERN})\b",
        lambda m: f"[chunk:{m.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    # Expand grouped chunk citations: [chunk:id1, id2] -> [chunk:id1] [chunk:id2]
    text = re.sub(
        rf"\[\s*chunk\s*:\s*(({_CHUNK_ID_PATTERN})(?:\s*[,;]\s*{_CHUNK_ID_PATTERN})+)\s*\]",
        lambda m: " ".join(
            f"[chunk:{token}]"
            for token in re.findall(_CHUNK_ID_PATTERN, m.group(1), flags=re.IGNORECASE)
        ),
        text,
        flags=re.IGNORECASE,
    )
    # Normalize SOURCE variants.
    text = re.sub(
        r"\[\s*source\s+([0-9,\s;]+)\]",
        lambda m: " ".join(f"[SOURCE {n}]" for n in re.findall(r"\d+", m.group(1))),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\(\s*(?:source|src|ref)\s+(\d{1,3})\s*\)",
        lambda m: f"[SOURCE {m.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:source|src|ref)\s*[#:]\s*(\d{1,3})\b",
        lambda m: f"[SOURCE {m.group(1)}]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _append_citation_item(citations: List[dict], source_item: dict) -> None:
    citations.append(
        {
            "chunk_id": source_item["child_chunk_id"],
            "parent_chunk_id": source_item["parent_chunk_id"],
            "child_chunk_index": source_item.get("child_chunk_index"),
            "section_title": source_item.get("section_title"),
            "citation_ref": source_item.get("citation_ref"),
            "citation": source_item["citation"],
            "parent_text": source_item.get("parent_text"),
            "child_text": source_item.get("child_text"),
            "file_path": source_item.get("file_path"),
            "is_primary": source_item.get("is_primary", True),
            "context_only": source_item.get("context_only", False),
            "source_tag": source_item.get("source_tag"),
            "collection": source_item.get("collection"),
            "book_slug": source_item.get("book_slug"),
            "lesson_code": source_item.get("lesson_code"),
            "node_path": source_item.get("node_path"),
            "section_path": source_item.get("section_path"),
            "page_start": source_item.get("page_start"),
            "page_end": source_item.get("page_end"),
            "is_front_matter": source_item.get("is_front_matter", False),
        }
    )


_LOOSE_CHUNK_REF_RE = re.compile(r"\[chunk:([0-9a-fA-F\-\u2010\u2011\u2012]{8,})\]", re.IGNORECASE)
_MIN_CHUNK_PREFIX_LEN = 8


def resolve_chunk_refs(answer_text: str, source_map: Dict[str, dict]) -> str:
    """Rewrite [chunk:...] refs that unambiguously name provided evidence to full IDs.

    Some models (observed with gpt-oss:120b) abbreviate the evidence UUIDs they
    were given to their first 8 hex characters, or write the UUID hyphens as
    U+2011. A ref is rewritten only when it matches exactly one evidence ID in
    this answer's source map; unknown or ambiguous refs are left untouched and
    stay invalid downstream.
    """
    keys = [str(key) for key in source_map.keys() if key != "__order__"]
    if not answer_text or not keys:
        return answer_text
    by_lower = {key.lower(): key for key in keys}

    def _resolve(match: re.Match) -> str:
        ref = match.group(1).translate({0x2010: "-", 0x2011: "-", 0x2012: "-"}).lower()
        if ref in by_lower:
            return f"[chunk:{by_lower[ref]}]"
        if len(ref) >= _MIN_CHUNK_PREFIX_LEN:
            candidates = [key for lower, key in by_lower.items() if lower.startswith(ref)]
            if len(candidates) == 1:
                return f"[chunk:{candidates[0]}]"
        return match.group(0)

    return _LOOSE_CHUNK_REF_RE.sub(_resolve, answer_text)


def extract_citations(answer_text: str, source_map: Dict[str, dict]) -> List[dict]:
    config = get_config()
    normalized_text = normalize_citation_markup(answer_text)
    citations: List[dict] = []
    seen: set[str] = set()
    source_lookup = {
        str(key).lower(): str(key)
        for key in source_map.keys()
        if key != "__order__"
    }

    source_keys = source_map.get("__order__")
    if not isinstance(source_keys, list):
        source_keys = [key for key in source_map.keys() if key != "__order__"]

    # Canonical chunk refs support UUID-like ids and ordinal refs [chunk:1], [chunk:2], ...
    chunk_pattern = re.compile(rf"\[chunk:({_CHUNK_REF_PATTERN})\]", re.IGNORECASE)
    for raw_ref in chunk_pattern.findall(normalized_text):
        ref = str(raw_ref).strip()
        if not ref:
            continue
        if ref.isdigit():
            idx = int(ref) - 1
            if idx < 0 or idx >= len(source_keys):
                continue
            chunk_id = str(source_keys[idx])
            if chunk_id in seen:
                continue
            _append_citation_item(citations, source_map[chunk_id])
            seen.add(chunk_id)
            continue
        lookup_key = source_lookup.get(ref.lower())
        if not lookup_key or lookup_key in seen:
            continue
        _append_citation_item(citations, source_map[lookup_key])
        seen.add(lookup_key)

    legacy_pattern = re.compile(r"\[SOURCE\s+([0-9,\s;]+)\]", re.IGNORECASE)
    legacy_citations: List[dict] = []
    for match in legacy_pattern.findall(normalized_text):
        for token in re.findall(r"\d+", match):
            idx = int(token) - 1
            if idx < 0 or idx >= len(source_keys):
                continue
            chunk_id = source_keys[idx]
            if chunk_id in seen:
                continue
            _append_citation_item(legacy_citations, source_map[chunk_id])
            seen.add(chunk_id)

    # Last-resort legacy compatibility for [1], [2], ... only when explicit tags are absent.
    if not citations and not legacy_citations:
        bare_numeric_pattern = re.compile(r"\[(\d{1,3})\]")
        for token in bare_numeric_pattern.findall(normalized_text):
            idx = int(token) - 1
            if idx < 0 or idx >= len(source_keys):
                continue
            chunk_id = source_keys[idx]
            if chunk_id in seen:
                continue
            _append_citation_item(legacy_citations, source_map[chunk_id])
            seen.add(chunk_id)

    if citations:
        return citations
    if legacy_citations:
        if not config.rag_citations_parent_legacy:
            log_event("legacy_citations_fallback", count=len(legacy_citations))
        return legacy_citations
    return citations


def _measure_sentence_citation_coverage(
    answer_text: str,
    source_map: Dict[str, dict],
    min_sentence_words: int,
) -> dict[str, float | int]:
    text = normalize_citation_markup((answer_text or "").strip())
    if not text:
        return {
            "substantive_sentences": 0,
            "cited_sentences": 0,
            "coverage_ratio": 1.0,
            "uncited_sentence_count": 0,
            "invalid_citation_refs": 0,
        }

    chunk_ref_pattern = re.compile(rf"\[chunk:({_CHUNK_REF_PATTERN})\]", re.IGNORECASE)
    legacy_ref_pattern = re.compile(r"\[SOURCE\s+\d+\]", re.IGNORECASE)
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]

    substantive_sentences = 0
    cited_sentences = 0
    invalid_refs = 0
    valid_ids = {str(key).lower() for key in source_map.keys() if key != "__order__"}
    source_keys = source_map.get("__order__")
    if not isinstance(source_keys, list):
        source_keys = [key for key in source_map.keys() if key != "__order__"]

    for sentence in sentences:
        if len(sentence.split()) < min_sentence_words:
            continue
        substantive_sentences += 1
        chunk_refs = chunk_ref_pattern.findall(sentence)
        has_citation = bool(chunk_refs) or bool(legacy_ref_pattern.search(sentence))
        if has_citation:
            cited_sentences += 1
        for ref in chunk_refs:
            ref_text = str(ref).strip()
            if ref_text.isdigit():
                idx = int(ref_text) - 1
                if idx < 0 or idx >= len(source_keys):
                    invalid_refs += 1
                continue
            if ref_text.lower() not in valid_ids:
                invalid_refs += 1

    if substantive_sentences == 0:
        coverage_ratio = 1.0
    else:
        coverage_ratio = cited_sentences / substantive_sentences

    return {
        "substantive_sentences": substantive_sentences,
        "cited_sentences": cited_sentences,
        "coverage_ratio": round(float(coverage_ratio), 6),
        "uncited_sentence_count": max(0, substantive_sentences - cited_sentences),
        "invalid_citation_refs": invalid_refs,
    }


def _automap_citations_for_uncited_answer(
    *,
    answer_text: str,
    source_map: Dict[str, dict],
    min_sentence_words: int,
    min_score: float,
    min_coverage: float,
    max_cites_per_sentence: int,
) -> tuple[str, List[dict], bool]:
    sentences = [segment.strip() for segment in _SENTENCE_SPLIT_PATTERN.split(answer_text or "") if segment.strip()]
    if not sentences:
        return answer_text, [], False

    source_keys = source_map.get("__order__")
    if not isinstance(source_keys, list):
        source_keys = [key for key in source_map.keys() if key != "__order__"]

    primary_sources: list[dict] = []
    for source_key in source_keys:
        item = source_map.get(source_key)
        if not isinstance(item, dict):
            continue
        if not item.get("is_primary", True) or item.get("context_only", False):
            continue
        candidate_text = (item.get("child_text") or item.get("parent_text") or "").strip()
        if not candidate_text:
            continue
        primary_sources.append(item)

    if not primary_sources:
        return answer_text, [], False

    mapped_sentence_count = 0
    rebuilt_sentences: list[str] = []
    max_cites = max(1, int(max_cites_per_sentence))
    threshold = float(min_score)

    for sentence in sentences:
        word_count = len(sentence.split())
        if word_count < max(1, int(min_sentence_words)):
            rebuilt_sentences.append(sentence)
            continue
        if _INLINE_CITATION_PATTERN.search(sentence):
            rebuilt_sentences.append(sentence)
            continue

        scored: list[tuple[float, str]] = []
        for source_item in primary_sources:
            sentence_overlap = lexical_overlap(sentence, source_item.get("child_text") or source_item.get("parent_text") or "")
            rerank_prob = _rerank_usefulness_prob(source_item.get("rerank_score"))
            combined = (0.7 * sentence_overlap) + (0.3 * rerank_prob)
            if combined >= threshold:
                scored.append((combined, str(source_item["child_chunk_id"])))

        if not scored:
            rebuilt_sentences.append(sentence)
            continue

        scored.sort(key=lambda item: item[0], reverse=True)
        chosen_ids: list[str] = []
        for _score, chunk_id in scored:
            if chunk_id in chosen_ids:
                continue
            chosen_ids.append(chunk_id)
            if len(chosen_ids) >= max_cites:
                break
        if not chosen_ids:
            rebuilt_sentences.append(sentence)
            continue

        mapped_sentence_count += 1
        citations_text = " ".join(f"[chunk:{chunk_id}]" for chunk_id in chosen_ids)
        sentence_text = sentence.strip()
        if sentence_text and sentence_text[-1] in ".!?":
            rebuilt_sentences.append(f"{sentence_text[:-1].rstrip()} {citations_text}{sentence_text[-1]}".strip())
        else:
            rebuilt_sentences.append(f"{sentence_text} {citations_text}".strip())

    rebuilt_answer = " ".join(rebuilt_sentences).strip()
    rebuilt_citations = extract_citations(rebuilt_answer, source_map)
    coverage = _measure_sentence_citation_coverage(
        rebuilt_answer,
        source_map,
        min_sentence_words=max(1, int(min_sentence_words)),
    )
    success = (
        mapped_sentence_count > 0
        and len(rebuilt_citations) > 0
        and int(coverage.get("invalid_citation_refs", 0)) == 0
        and float(coverage.get("coverage_ratio", 0.0)) >= float(min_coverage)
    )
    return rebuilt_answer, rebuilt_citations, success


def verify_grounding(
    answer_text: str,
    citations: List[dict],
    refusal_keywords: Optional[List[str]] = None,
) -> dict:
    if refusal_keywords is None:
        refusal_keywords = ["cannot support", "insufficient", "not found"]

    lower_text = answer_text.lower()
    refusal = any(keyword in lower_text for keyword in refusal_keywords)
    citation_count = len(citations)
    issues: List[str] = []

    if refusal:
        return {
            "is_grounded": False,
            "refusal": True,
            "citation_count": citation_count,
            "issues": [],
        }

    config = get_config()
    word_count = len(answer_text.split())
    if word_count >= max(1, int(config.rag_no_cite_substantive_words)) and citation_count == 0:
        issues.append("Substantive answer with no citations")

    is_grounded = len(issues) == 0
    return {
        "is_grounded": is_grounded,
        "refusal": False,
        "citation_count": citation_count,
        "issues": issues,
    }


def _is_explicit_refusal(answer_text: str) -> bool:
    return (answer_text or "").strip() == REFUSAL_TEXT


def _is_substantive_uncited(answer_text: str, citations: List[dict], min_words: int) -> bool:
    if citations:
        return False
    return len((answer_text or "").split()) >= max(1, int(min_words))


def _build_citation_repair_messages(
    query: str,
    draft_answer: str,
    parent_chunks: List[SearchResult],
) -> tuple[str, str]:
    source_lines = _build_sources_section(parent_chunks, use_chunk_ids=True)
    system_message = (
        "You repair citations in an existing grounded answer.\n"
        "Rules:\n"
        "1. Keep claims and wording as close as possible to the draft.\n"
        "2. Add inline citations using ONLY [chunk:UUID] format.\n"
        "3. Do not invent new claims.\n"
        "4. Remove markdown citation variants like [CHUNK UUID] and output canonical [chunk:UUID].\n"
        "5. Return only the repaired answer text.\n"
    )
    user_message = "\n".join(
        [
            *source_lines,
            f"QUESTION: {query}",
            "",
            "DRAFT ANSWER:",
            draft_answer.strip(),
            "",
            "REPAIRED ANSWER:",
        ]
    )
    return system_message, user_message


def _retry_citation_repair(
    *,
    query: str,
    answer_text: str,
    parent_chunks: List[SearchResult],
    source_map: Dict[str, dict],
    client: ollama.Client,
    model_name: str,
    config,
) -> tuple[str, List[dict], bool]:
    repaired_text = answer_text
    max_attempts = max(0, int(config.rag_no_cite_retry_max))
    for _ in range(max_attempts):
        repair_system, repair_user = _build_citation_repair_messages(query, repaired_text, parent_chunks)
        repaired_raw = generate_answer(
            prompt="",
            client=client,
            model=model_name,
            num_ctx=config.ollama_num_ctx,
            temperature=0.05,
            keep_alive=config.ollama_keepalive,
            system_message=repair_system,
            user_message=repair_user,
        )
        candidate_text, _ = extract_follow_up_questions(repaired_raw)
        candidate_text = resolve_chunk_refs(normalize_citation_markup(candidate_text), source_map).strip()
        candidate_citations = extract_citations(candidate_text, source_map)
        if candidate_citations:
            return candidate_text, candidate_citations, True
        repaired_text = candidate_text
    return normalize_citation_markup(answer_text).strip(), [], False


def _finalize_answer_state(
    *,
    query: str,
    raw_answer_text: str,
    source_map: Dict[str, dict],
    requested_mode: str,
    parent_chunks: List[SearchResult],
    config,
    client: Optional[ollama.Client] = None,
    model_name: Optional[str] = None,
) -> dict:
    normalized_text = normalize_citation_markup(raw_answer_text)
    answer_text = resolve_chunk_refs(normalized_text, source_map).strip()
    if answer_text != normalized_text.strip():
        log_event("citation_refs_resolved", span="postprocess", reason="abbreviated_or_unicode_chunk_ids")
    citations = extract_citations(answer_text, source_map)
    citation_retry_used = False
    citation_retry_succeeded = False
    citation_automap_used = False
    citation_automap_succeeded = False

    explicit_refusal = _is_explicit_refusal(answer_text)
    should_retry = (
        config.rag_no_cite_retry_enabled
        and not explicit_refusal
        and _is_substantive_uncited(answer_text, citations, config.rag_no_cite_substantive_words)
        and client is not None
        and model_name is not None
        and int(config.rag_no_cite_retry_max) > 0
    )
    if should_retry:
        citation_retry_used = True
        answer_text, retry_citations, citation_retry_succeeded = _retry_citation_repair(
            query=query,
            answer_text=answer_text,
            parent_chunks=parent_chunks,
            source_map=source_map,
            client=client,
            model_name=model_name,
            config=config,
        )
        if citation_retry_succeeded:
            citations = retry_citations

    explicit_refusal = _is_explicit_refusal(answer_text)
    should_automap = (
        config.rag_citation_automap_enabled
        and not explicit_refusal
        and _is_substantive_uncited(answer_text, citations, config.rag_no_cite_substantive_words)
    )
    if should_automap:
        citation_automap_used = True
        mapped_answer, mapped_citations, mapped_success = _automap_citations_for_uncited_answer(
            answer_text=answer_text,
            source_map=source_map,
            min_sentence_words=max(1, int(config.rag_citation_automap_min_sent_words)),
            min_score=float(config.rag_citation_automap_min_score),
            min_coverage=float(config.rag_citation_automap_min_coverage),
            max_cites_per_sentence=max(1, int(config.rag_citation_automap_max_cites_per_sentence)),
        )
        if mapped_success:
            answer_text = mapped_answer
            citations = mapped_citations
            citation_automap_succeeded = True

    explicit_refusal = _is_explicit_refusal(answer_text)
    used_ids = [str(item.get("chunk_id")) for item in citations if item.get("chunk_id")]

    if explicit_refusal:
        return {
            "answer_text": answer_text,
            "citations": [],
            "used_evidence_ids": [],
            "grounded": False,
            "refusal": True,
            "answer_mode": "refuse",
            "grounding_reason": "refusal",
            "citation_retry_used": citation_retry_used,
            "citation_retry_succeeded": citation_retry_succeeded,
            "citation_automap_used": citation_automap_used,
            "citation_automap_succeeded": citation_automap_succeeded,
        }

    if citations:
        return {
            "answer_text": answer_text,
            "citations": citations,
            "used_evidence_ids": used_ids,
            "grounded": True,
            "refusal": False,
            "answer_mode": requested_mode,
            "grounding_reason": "cited_auto_mapped" if citation_automap_succeeded else "cited",
            "citation_retry_used": citation_retry_used,
            "citation_retry_succeeded": citation_retry_succeeded,
            "citation_automap_used": citation_automap_used,
            "citation_automap_succeeded": citation_automap_succeeded,
        }

    return {
        "answer_text": answer_text,
        "citations": [],
        "used_evidence_ids": [],
        "grounded": False,
        "refusal": False,
        "answer_mode": "narrow",
        "grounding_reason": "uncited_narrow",
        "citation_retry_used": citation_retry_used,
        "citation_retry_succeeded": citation_retry_succeeded,
        "citation_automap_used": citation_automap_used,
        "citation_automap_succeeded": citation_automap_succeeded,
    }


def generate_grounded_answer(
    query: str,
    parent_chunks: List[SearchResult],
    corpus: str,
    model: Optional[str] = None,
    client: Optional[ollama.Client] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> GenerationResult:
    setup_logging()
    log = logger.bind(function="generate_grounded_answer", corpus=corpus)

    if not parent_chunks:
        log.warning("no_parent_chunks")
        return GenerationResult(
            answer=REFUSAL_TEXT,
            citations=[],
            grounding_score=False,
            follow_up_questions=[],
            answer_mode="refuse",
            sufficiency_metrics={"reason": "no_parent_chunks"},
            refusal=True,
            grounding_reason="refusal",
        )

    mode, metrics = evaluate_sufficiency(query, parent_chunks, corpus)
    log.info("sufficiency_check", mode=mode, **metrics)
    log_event(
        "sufficiency_decision",
        span="sufficiency",
        answer_mode=mode,
        s1=metrics.get("s1"),
        s5=metrics.get("s5"),
        overlap=metrics.get("overlap"),
    )
    if mode == "refuse":
        return GenerationResult(
            answer=REFUSAL_TEXT,
            citations=[],
            grounding_score=False,
            follow_up_questions=[],
            answer_mode=mode,
            sufficiency_metrics=metrics,
            refusal=True,
            grounding_reason="refusal",
        )

    config = get_config()
    ollama_client = client or get_ollama_client(timeout=config.ollama_timeout)
    ensure_ollama_running(ollama_client)

    system_msg, user_msg = build_grounded_messages(query, parent_chunks, corpus, history=history, evidence_mode=mode)
    log.info("prompt_built", source_count=len(parent_chunks), has_history=bool(history))
    log_event(
        "prompt_built",
        span="generation",
        source_count=len(parent_chunks),
        has_history=bool(history),
        answer_mode=mode,
    )

    model_name = model or config.ollama_model
    with SpanTimer(
        "generation",
        model=model_name,
        num_ctx=config.ollama_num_ctx,
        temperature=config.rag_generation_temperature,
    ):
        raw_answer = generate_answer(
            prompt="",
            client=ollama_client,
            model=model_name,
            num_ctx=config.ollama_num_ctx,
            temperature=config.rag_generation_temperature,
            keep_alive=config.ollama_keepalive,
            system_message=system_msg,
            user_message=user_msg,
        )
    log.info("answer_generated", length=len(raw_answer))

    with SpanTimer("postprocess") as post_span:
        answer_text, _ = extract_follow_up_questions(raw_answer)
        source_map = build_source_map(parent_chunks)
        final_state = _finalize_answer_state(
            query=query,
            raw_answer_text=answer_text,
            source_map=source_map,
            requested_mode=mode,
            parent_chunks=parent_chunks,
            config=config,
            client=ollama_client,
            model_name=model_name,
        )
        citations = final_state["citations"]
        citation_payload = build_grounding_citation_payloads(
            parent_chunks=parent_chunks,
            used_evidence_ids=final_state["used_evidence_ids"],
            answer_mode=final_state["answer_mode"],
            refusal=final_state["refusal"],
        )
        log_event(
            "grounding_finalize",
            span="postprocess",
            grounding_reason=final_state["grounding_reason"],
            citation_retry_used=final_state["citation_retry_used"],
            citation_retry_succeeded=final_state["citation_retry_succeeded"],
            citation_automap_used=final_state["citation_automap_used"],
            citation_automap_succeeded=final_state["citation_automap_succeeded"],
            refusal=final_state["refusal"],
            grounded=final_state["grounded"],
        )
        if config.rag_citation_guardrail_enabled:
            coverage = _measure_sentence_citation_coverage(
                answer_text=final_state["answer_text"],
                source_map=source_map,
                min_sentence_words=max(1, int(config.rag_citation_guardrail_min_sentence_words)),
            )
            coverage_low = float(coverage.get("coverage_ratio", 1.0)) < float(
                config.rag_citation_guardrail_min_coverage
            )
            event_name = "citation_coverage_low" if coverage_low else "citation_coverage_ok"
            log_event(
                event_name,
                span="postprocess",
                coverage_ratio=coverage.get("coverage_ratio"),
                substantive_sentences=coverage.get("substantive_sentences"),
                cited_sentences=coverage.get("cited_sentences"),
                uncited_sentence_count=coverage.get("uncited_sentence_count"),
                invalid_citation_refs=coverage.get("invalid_citation_refs"),
            )
            if coverage_low and not config.rag_citation_guardrail_log_only:
                final_state["citation_guardrail_triggered"] = True
                log_event(
                    "citation_guardrail_enforced",
                    span="postprocess",
                    coverage_ratio=coverage.get("coverage_ratio"),
                    min_coverage=float(config.rag_citation_guardrail_min_coverage),
                )
        log.info(
            "grounding_verified",
            grounded=final_state["grounded"],
            refusal=final_state["refusal"],
            citations=len(citations),
        )
        follow_up_questions = []
        if config.rag_followups_enabled and not final_state["refusal"]:
            follow_up_questions = generate_followups(
                query,
                parent_chunks,
                ollama_client,
                config.secondary_ollama_model or model_name,
                keep_alive=config.ollama_keepalive,
            )
            log.info("follow_up_generated", count=len(follow_up_questions))
        post_span.add_fields(
            citations_count=len(citations),
            followups_count=len(follow_up_questions),
            grounded=final_state["grounded"],
            refusal=final_state["refusal"],
        )
    citation_models = [item["citation"] for item in citations]
    citation_cards = citation_payload["citations"]
    final_used_evidence_ids = citation_payload["used_evidence_ids"]
    matched_evidence_ids = citation_payload["matched_evidence_ids"]
    citation_fallback_reason = citation_payload["citation_fallback_reason"]
    citation_strategy_version = citation_payload["citation_strategy_version"]
    num_citations_matched = citation_payload["num_citations_matched"]
    return GenerationResult(
        answer=final_state["answer_text"],
        citations=citation_models,
        grounding_score=final_state["grounded"],
        follow_up_questions=follow_up_questions,
        answer_mode=final_state["answer_mode"],
        sufficiency_metrics=metrics,
        citation_items=citation_cards,
        used_evidence_ids=final_used_evidence_ids,
        matched_evidence_ids=matched_evidence_ids,
        refusal=final_state["refusal"],
        grounding_reason=final_state["grounding_reason"],
        citation_guardrail_triggered=final_state.get("citation_guardrail_triggered"),
        citation_retry_used=final_state["citation_retry_used"],
        citation_retry_succeeded=final_state["citation_retry_succeeded"],
        citation_automap_used=final_state["citation_automap_used"],
        citation_automap_succeeded=final_state["citation_automap_succeeded"],
        citation_fallback_reason=citation_fallback_reason,
        citation_strategy_version=citation_strategy_version,
        num_citations_emitted=len(citation_cards),
        num_citations_matched=num_citations_matched,
    )


def _stream_answer_events(
    stream: Iterable[dict],
    heartbeat_sec: float = REASONING_HEARTBEAT_SEC,
) -> Generator[dict, None, None]:
    """Map Ollama chat stream chunks to answer events.

    Only `message.content` becomes answer tokens. Reasoning models (e.g. gpt-oss)
    stream their reasoning in `message.thinking`; it never enters the answer,
    citations, or telemetry. While only reasoning arrives, emit a status event at
    most every `heartbeat_sec` so clients do not mistake it for a stalled stream.
    """
    last_heartbeat = time.perf_counter()
    for chunk in stream:
        message = chunk.get("message", {}) or {}
        token = message.get("content", "")
        if token:
            yield {"type": "token", "content": token}
            continue
        if message.get("thinking"):
            now = time.perf_counter()
            if now - last_heartbeat >= heartbeat_sec:
                yield {"type": "status", "phase": "generating", "message": "Reasoning..."}
                last_heartbeat = now


def stream_grounded_answer(
    query: str,
    corpus: str,
    session: Session,
    embedder: Optional[SentenceTransformer] = None,
    reranker: Optional[CrossEncoder] = None,
    model: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: int = 5,
    retrieval_mode: str | None = None,
    run_id: str | None = None,
    telemetry_hook: Optional[Callable[[dict], None]] = None,
) -> Generator[dict, None, None]:
    setup_logging()
    log = logger.bind(function="stream_grounded_answer", corpus=corpus)

    total_start = time.perf_counter()

    yield {"type": "status", "phase": "retrieving", "message": "Finding evidence..."}
    retrieval_start = time.perf_counter()
    parent_chunks, retrieval_stats = retrieve_context_with_stats(
        query=query,
        corpus=corpus,
        top_k=top_k,
        session=session,
        embedder=embedder,
        reranker=reranker,
        retrieval_mode=retrieval_mode,
        history=history,
    )
    retrieval_time_ms = (time.perf_counter() - retrieval_start) * 1000
    log.info("stream_retrieval_complete", results=len(parent_chunks), ms=retrieval_time_ms)

    yield {"type": "status", "phase": "reranking", "message": "Ranking evidence..."}
    yield {"type": "status", "phase": "building", "message": "Building context..."}

    if not parent_chunks:
        if telemetry_hook:
            telemetry_hook(
                _build_stream_telemetry_payload(
                    run_id=run_id,
                    answer_mode="refuse",
                    grounded=False,
                    refusal=True,
                    generation_time_ms=0.0,
                    retrieval_time_ms=retrieval_time_ms,
                    t_post_ms=0.0,
                    t_total_ms=(time.perf_counter() - total_start) * 1000,
                    retrieval_stats=retrieval_stats,
                    sufficiency_metrics={"s1": None, "s5": None, "overlap": None},
                    num_citations_emitted=0,
                    num_citations_matched=0,
                    used_evidence_ids=[],
                    rerank_rows=[],
                    evidence_rows=[],
                    grounding_reason="refusal",
                )
            )
        # Emit empty citations explicitly so UI clears any stale source cards.
        yield {"type": "citations", "items": [], "retrieval_time_ms": retrieval_time_ms}
        yield {"type": "token", "content": REFUSAL_TEXT}
        yield {
            "type": "final",
            "answer_mode": "refuse",
            "grounded": False,
            "refusal": True,
            "grounding_reason": "refusal",
            "citation_retry_used": False,
            "citation_retry_succeeded": False,
            "citation_automap_used": False,
            "citation_automap_succeeded": False,
            "citation_fallback_reason": None,
            "citation_strategy_version": CITATION_STRATEGY_VERSION,
            "follow_ups": [],
            "generation_time_ms": 0,
            "num_citations_emitted": 0,
            "num_citations_matched": 0,
            "used_evidence_ids": [],
        }
        return

    mode, metrics = evaluate_sufficiency(query, parent_chunks, corpus)
    log.info("sufficiency_check", mode=mode, **metrics)
    log_event(
        "sufficiency_decision",
        span="sufficiency",
        answer_mode=mode,
        s1=metrics.get("s1"),
        s5=metrics.get("s5"),
        overlap=metrics.get("overlap"),
    )
    if mode == "refuse":
        if telemetry_hook:
            telemetry_hook(
                _build_stream_telemetry_payload(
                    run_id=run_id,
                    answer_mode="refuse",
                    grounded=False,
                    refusal=True,
                    generation_time_ms=0.0,
                    retrieval_time_ms=retrieval_time_ms,
                    t_post_ms=0.0,
                    t_total_ms=(time.perf_counter() - total_start) * 1000,
                    retrieval_stats=retrieval_stats,
                    sufficiency_metrics=metrics,
                    num_citations_emitted=0,
                    num_citations_matched=0,
                    used_evidence_ids=[],
                    rerank_rows=[],
                    evidence_rows=[],
                    grounding_reason="refusal",
                )
            )
        yield {"type": "token", "content": REFUSAL_TEXT}
        yield {
            "type": "final",
            "answer_mode": "refuse",
            "grounded": False,
            "refusal": True,
            "grounding_reason": "refusal",
            "citation_retry_used": False,
            "citation_retry_succeeded": False,
            "citation_automap_used": False,
            "citation_automap_succeeded": False,
            "citation_fallback_reason": None,
            "citation_strategy_version": CITATION_STRATEGY_VERSION,
            "num_citations_emitted": 0,
            "num_citations_matched": 0,
            "generation_time_ms": 0,
            "follow_ups": [],
            "used_evidence_ids": [],
        }
        return

    config = get_config()
    ollama_client = get_ollama_client(timeout=config.ollama_timeout)
    ensure_ollama_running(ollama_client)

    if mode == "narrow":
        yield {"type": "status", "phase": "generating", "message": "Evidence limited; answering narrowly"}
    system_msg, user_msg = build_grounded_messages(query, parent_chunks, corpus, history=history, evidence_mode=mode)
    log_event(
        "prompt_built",
        span="generation",
        source_count=len(parent_chunks),
        has_history=bool(history),
        answer_mode=mode,
    )
    model_name = model or config.ollama_model
    yield {"type": "status", "phase": "generating", "message": f"Reasoning with {model_name}..."}
    yield {"type": "status", "phase": "generating", "message": "Writing answer..."}

    generation_start = time.perf_counter()
    with SpanTimer(
        "generation",
        model=model_name,
        num_ctx=config.ollama_num_ctx,
        temperature=config.rag_generation_temperature,
    ):
        full_response = ""
        token_count = 0
        first_token_logged = False
        last_progress_time = time.perf_counter()
        stream = ollama_client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            options={"num_ctx": config.ollama_num_ctx, "temperature": config.rag_generation_temperature},
            keep_alive=config.ollama_keepalive,
            stream=True,
        )

        for event in _stream_answer_events(stream):
            if event["type"] != "token":
                yield event
                continue
            token = event["content"]
            if token:
                full_response += token
                yield event
                token_count += 1
                if not first_token_logged:
                    log_event(
                        "stream_first_token",
                        span="stream",
                        first_token_ms=(time.perf_counter() - generation_start) * 1000,
                    )
                    first_token_logged = True
                now = time.perf_counter()
                if now - last_progress_time >= 1.5:
                    log_event(
                        "stream_token_progress",
                        span="stream",
                        token_count=token_count,
                        elapsed_ms=(now - generation_start) * 1000,
                    )
                    last_progress_time = now

    generation_time_ms = (time.perf_counter() - generation_start) * 1000
    log.info("stream_generation_complete", ms=generation_time_ms)

    yield {"type": "status", "phase": "finalizing", "message": "Checking grounding..."}
    post_start = time.perf_counter()
    with SpanTimer("postprocess") as post_span:
        clean_response, _ = extract_follow_up_questions(full_response)
        source_map = build_source_map(parent_chunks)
        final_state = _finalize_answer_state(
            query=query,
            raw_answer_text=clean_response,
            source_map=source_map,
            requested_mode=mode,
            parent_chunks=parent_chunks,
            config=config,
            client=ollama_client,
            model_name=model_name,
        )
        citations = final_state["citations"]
        grounded = final_state["grounded"]
        refusal = final_state["refusal"]
        citation_payload = build_grounding_citation_payloads(
            parent_chunks=parent_chunks,
            used_evidence_ids=final_state["used_evidence_ids"],
            answer_mode=final_state["answer_mode"],
            refusal=final_state["refusal"],
        )
        used_ids = citation_payload["used_evidence_ids"]
        used_id_set = {str(item) for item in used_ids if item}
        citations_data = citation_payload["citations"]
        citation_fallback_reason = citation_payload["citation_fallback_reason"]
        citation_strategy_version = citation_payload["citation_strategy_version"]
        log_event(
            "grounding_finalize",
            span="postprocess",
            grounding_reason=final_state["grounding_reason"],
            citation_retry_used=final_state["citation_retry_used"],
            citation_retry_succeeded=final_state["citation_retry_succeeded"],
            citation_automap_used=final_state["citation_automap_used"],
            citation_automap_succeeded=final_state["citation_automap_succeeded"],
            refusal=final_state["refusal"],
            grounded=final_state["grounded"],
        )
        if config.rag_citation_guardrail_enabled:
            coverage = _measure_sentence_citation_coverage(
                answer_text=final_state["answer_text"],
                source_map=source_map,
                min_sentence_words=max(1, int(config.rag_citation_guardrail_min_sentence_words)),
            )
            coverage_low = float(coverage.get("coverage_ratio", 1.0)) < float(
                config.rag_citation_guardrail_min_coverage
            )
            event_name = "citation_coverage_low" if coverage_low else "citation_coverage_ok"
            log_event(
                event_name,
                span="postprocess",
                coverage_ratio=coverage.get("coverage_ratio"),
                substantive_sentences=coverage.get("substantive_sentences"),
                cited_sentences=coverage.get("cited_sentences"),
                uncited_sentence_count=coverage.get("uncited_sentence_count"),
                invalid_citation_refs=coverage.get("invalid_citation_refs"),
            )
            if coverage_low and not config.rag_citation_guardrail_log_only:
                final_state["citation_guardrail_triggered"] = True
                log_event(
                    "citation_guardrail_enforced",
                    span="postprocess",
                    coverage_ratio=coverage.get("coverage_ratio"),
                    min_coverage=float(config.rag_citation_guardrail_min_coverage),
                )

        # Run follow-up generation in background thread so we don't block the final event
        follow_ups: list[str] = []
        followup_done = threading.Event()
        followup_result: dict[str, list[str]] = {"value": []}
        followup_thread: threading.Thread | None = None
        if config.rag_followups_enabled and not refusal:
            def _run_followups() -> None:
                try:
                    followup_result["value"] = generate_followups(
                        query,
                        parent_chunks,
                        ollama_client,
                        config.secondary_ollama_model or model_name,
                        keep_alive=config.ollama_keepalive,
                    )
                finally:
                    followup_done.set()

            followup_thread = threading.Thread(target=_run_followups, daemon=True)
            followup_thread.start()

        post_span.add_fields(
            citations_count=len(citations),
            grounded=grounded,
            refusal=refusal,
        )
    post_time_ms = (time.perf_counter() - post_start) * 1000
    total_time_ms = (time.perf_counter() - total_start) * 1000

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
    matched_chunk_ids = {str(item.get("chunk_id")) for item in citations}
    evidence_rows = [
        {
            "evidence_id": item.get("chunk_id"),
            "parent_chunk_id": item.get("parent_chunk_id"),
            "citation_ref": item.get("citation_ref"),
            "position": idx + 1,
            "context_only": bool(item.get("context_only", False)),
            "matched_in_text": str(item.get("chunk_id")) in matched_chunk_ids,
        }
        for idx, item in enumerate(citations_data)
    ]

    # Wait for follow-ups with a timeout so they don't block indefinitely
    if followup_thread is not None:
        completed = followup_done.wait(timeout=10)
        follow_ups = followup_result["value"] if completed else []

    final_mode = final_state["answer_mode"]

    # Run telemetry in a background thread so it doesn't block the final SSE event
    if telemetry_hook:
        telemetry_payload = _build_stream_telemetry_payload(
            run_id=run_id,
            answer_mode=final_mode,
            grounded=grounded,
            refusal=refusal,
            generation_time_ms=generation_time_ms,
            retrieval_time_ms=retrieval_time_ms,
            t_post_ms=post_time_ms,
            t_total_ms=total_time_ms,
            retrieval_stats=retrieval_stats,
            sufficiency_metrics=metrics,
            num_citations_emitted=len(citations_data),
            num_citations_matched=len(citations),
            used_evidence_ids=used_ids,
            rerank_rows=rerank_rows,
            evidence_rows=evidence_rows,
            grounding_reason=final_state["grounding_reason"],
            citation_retry_used=final_state["citation_retry_used"],
            citation_retry_succeeded=final_state["citation_retry_succeeded"],
            citation_automap_used=final_state["citation_automap_used"],
            citation_automap_succeeded=final_state["citation_automap_succeeded"],
            citation_fallback_reason=citation_fallback_reason,
            citation_strategy_version=citation_strategy_version,
        )
        threading.Thread(target=telemetry_hook, args=(telemetry_payload,), daemon=True).start()

    # Emit citations only after final grounding/citation extraction to avoid mismatched sources.
    yield {"type": "citations", "items": citations_data, "retrieval_time_ms": retrieval_time_ms}

    yield {
        "type": "final",
        "answer_mode": final_mode,
        "grounded": grounded,
        "refusal": refusal,
        "answer_text_final": final_state["answer_text"],
        "grounding_reason": final_state["grounding_reason"],
        "citation_retry_used": final_state["citation_retry_used"],
        "citation_retry_succeeded": final_state["citation_retry_succeeded"],
        "citation_automap_used": final_state["citation_automap_used"],
        "citation_automap_succeeded": final_state["citation_automap_succeeded"],
        "citation_fallback_reason": citation_fallback_reason,
        "citation_strategy_version": citation_strategy_version,
        "num_citations_emitted": len(citations_data),
        "num_citations_matched": len(citations),
        "generation_time_ms": generation_time_ms,
        "follow_ups": follow_ups,
        "used_evidence_ids": used_ids,
    }
