"""Multi-stage retrieval pipeline."""

from __future__ import annotations

from collections import OrderedDict
from typing import List, Optional, Dict, Tuple, Any
import re
import time
import math
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import numpy as np
import structlog
from qdrant_client import QdrantClient
from qdrant_client.http.models import SearchParams
from sentence_transformers import SentenceTransformer, CrossEncoder
from sqlalchemy import and_, func, or_, select, text as sql_text
from sqlalchemy.orm import Session
from sqlalchemy.exc import ProgrammingError
import torch

from src.db.models import (
    Corpus,
    DeanBook,
    DeanCollection,
    DeanLesson,
    TranscriptChildChunk,
    TranscriptParentChunk,
    TranscriptLesson,
    TranscriptChapter,
    TranscriptPart,
    Series,
)
from src.db.session import get_sessionmaker
from src.retrieval.models import SearchResult, Citation
from src.utils.config import get_config
from src.utils.logging import setup_logging
from src.observability.logging import log_event
from src.observability.timer import SpanTimer
from src.utils.qdrant_client import get_qdrant_client
from src.utils.ollama_client import get_ollama_client, helper_num_ctx
from src.utils.text import STOPWORDS, normalize_search_text

logger = structlog.get_logger()
DEFINITION_PATTERN = re.compile(r"\bmeans\b|\bdefined as\b|\bin other words\b", re.IGNORECASE)
TERM_COLON_PATTERN = re.compile(r"^[^:\n]{1,40}:\s")


def rewrite_query(query: str, history: list[dict] | None = None) -> str:
    """Rewrite a vague or conversational query into a precise retrieval query."""
    config = get_config()
    if not config.rag_query_rewrite_enabled:
        return query
    model_name = config.secondary_ollama_model or config.ollama_model
    try:
        client = get_ollama_client(timeout=15)
        context = ""
        if history:
            recent = history[-4:]
            lines = []
            for turn in recent:
                role = turn.get("role", "user")
                content = turn.get("content", "")[:300]
                lines.append(f"{role}: {content}")
            context = "\nConversation context:\n" + "\n".join(lines)
        prompt = (
            "Rewrite the following user question into a clear, specific search query "
            "for retrieving relevant passages from theological teaching transcripts.\n"
            "Rules:\n"
            "- Preserve Bible book names, chapter/verse references, and theological terms exactly as given.\n"
            "- Resolve pronouns (e.g., 'he', 'that passage', 'this concept') using the conversation context.\n"
            "- Do not add information or assumptions not implied by the question.\n"
            "- Keep the rewritten query concise and search-friendly.\n"
            "Return ONLY the rewritten query, nothing else."
            f"{context}\n\nOriginal question: {query}\n\nRewritten query:"
        )
        response = client.generate(
            model=model_name,
            prompt=prompt,
            options={"num_ctx": helper_num_ctx(model_name, 2048), "temperature": 0.0},
            keep_alive=config.ollama_keepalive,
        )
        rewritten = normalize_search_text(response.get("response", "")).strip()
        if rewritten and 5 < len(rewritten) < 500:
            log_event("query_rewritten", span="retrieval", original=query, rewritten=rewritten)
            return rewritten
    except Exception:  # noqa: BLE001
        log_event("query_rewrite_failed", span="retrieval", original=query)
    return query


def load_embedding_model() -> SentenceTransformer:
    config = get_config()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return SentenceTransformer(config.embedding_model, device=device)


def load_reranker() -> CrossEncoder:
    config = get_config()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return CrossEncoder(config.reranker_model, device=device)


def _extract_chunk_meta_fields(meta: dict | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {
            "node_path": None,
            "node_paths": [],
            "section_path": None,
            "page_start": None,
            "page_end": None,
            "is_front_matter": False,
            "source_tag": None,
            "collection": None,
            "book_slug": None,
            "lesson_code": None,
            "part_number": None,
            "part_title": None,
        }
    node_paths = list(meta.get("node_paths") or [])
    node_path = meta.get("node_path")
    if not node_path and node_paths:
        node_path = node_paths[0]
    return {
        "node_path": node_path,
        "node_paths": node_paths,
        "section_path": meta.get("section_path"),
        "page_start": meta.get("page_start"),
        "page_end": meta.get("page_end"),
        "is_front_matter": bool(meta.get("is_front_matter", False)),
        "source_tag": meta.get("source_tag"),
        "collection": meta.get("collection"),
        "book_slug": meta.get("book_slug"),
        "lesson_code": meta.get("lesson_code"),
        "part_number": meta.get("part_number"),
        "part_title": meta.get("part_title"),
    }


def _path_segments(path: str | None) -> tuple[str, ...]:
    if not path:
        return ()
    text = str(path).strip()
    if not text:
        return ()
    text = re.sub(r"^(?:path|sec)\s+", "", text, flags=re.IGNORECASE)
    segments = tuple(token.lower() for token in re.findall(r"[A-Za-z0-9]+", text))
    return segments


def _path_prefix_similarity(a: str | None, b: str | None) -> float:
    seg_a = _path_segments(a)
    seg_b = _path_segments(b)
    if not seg_a or not seg_b:
        return 0.0
    common = 0
    for left, right in zip(seg_a, seg_b):
        if left != right:
            break
        common += 1
    if common == 0:
        return 0.0
    return common / max(len(seg_a), len(seg_b))


def _candidate_hierarchy_key(candidate: dict, use_section_path: bool) -> str | None:
    node_path = candidate.get("node_path")
    if node_path:
        return str(node_path)
    if use_section_path:
        section_path = candidate.get("section_path")
        if section_path:
            return str(section_path)
    return None


def _apply_hierarchy_boost(
    ranked: list[dict], config, corpus: str | None = None
) -> tuple[list[dict], dict[str, Any]]:
    stats: dict[str, Any] = {
        "hierarchy_boost_applied": False,
        "hierarchy_coverage": 0.0,
        "hierarchy_anchor_count": 0,
        "hierarchy_boosted_count": 0,
        "hierarchy_avg_bonus": 0.0,
    }
    if not ranked:
        return ranked, stats
    if not config.rag_hierarchy_boost_enabled:
        return ranked, stats

    scope = getattr(config, "rag_hierarchy_boost_corpus_scope", "") or ""
    if corpus and scope:
        allowed = [token.strip().lower() for token in scope.split(",") if token.strip()]
        if corpus.lower() not in allowed:
            return ranked, stats

    coverage_count = sum(
        1 for candidate in ranked if _candidate_hierarchy_key(candidate, bool(config.rag_hierarchy_use_section_path))
    )
    coverage = coverage_count / len(ranked)
    stats["hierarchy_coverage"] = round(float(coverage), 6)
    if coverage < float(config.rag_hierarchy_min_coverage):
        return ranked, stats

    anchor_limit = max(1, int(config.rag_hierarchy_anchor_top_n))
    anchors: list[str] = []
    for candidate in ranked:
        if candidate.get("context_only"):
            continue
        key = _candidate_hierarchy_key(candidate, bool(config.rag_hierarchy_use_section_path))
        if not key:
            continue
        anchors.append(key)
        if len(anchors) >= anchor_limit:
            break

    stats["hierarchy_anchor_count"] = len(anchors)
    if not anchors:
        return ranked, stats

    max_weight = max(0.0, float(config.rag_hierarchy_boost_weight))
    if max_weight == 0.0:
        return ranked, stats

    boosted_count = 0
    total_bonus = 0.0
    for candidate in ranked:
        key = _candidate_hierarchy_key(candidate, bool(config.rag_hierarchy_use_section_path))
        if not key:
            continue
        similarity = max(_path_prefix_similarity(key, anchor) for anchor in anchors)
        bonus = _clamp(max_weight * similarity, 0.0, max_weight)
        if bonus <= 0.0:
            continue
        candidate["rerank_score"] = float(candidate.get("rerank_score") or 0.0) + bonus
        boosted_count += 1
        total_bonus += bonus

    if boosted_count > 0:
        ranked = _sort_by_rerank(ranked, config)
        stats["hierarchy_boost_applied"] = True
    stats["hierarchy_boosted_count"] = boosted_count
    stats["hierarchy_avg_bonus"] = round(total_bonus / boosted_count, 6) if boosted_count else 0.0
    return ranked, stats


class LRUCache:
    def __init__(self) -> None:
        self._values: OrderedDict[Any, Any] = OrderedDict()
        self._lock = Lock()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            if key not in self._values:
                return None
            self._values.move_to_end(key)
            return self._values[key]

    def put(self, key: Any, value: Any, max_size: int) -> None:
        if max_size <= 0:
            return
        with self._lock:
            if key in self._values:
                self._values.move_to_end(key)
            self._values[key] = value
            while len(self._values) > max_size:
                self._values.popitem(last=False)


_QUERY_EMBED_CACHE = LRUCache()
_RERANK_CACHE = LRUCache()


def _apply_query_prefix(query: str) -> str:
    config = get_config()
    if not config.embedding_use_prefix:
        return query
    prefix = (config.embedding_query_prefix or "").strip()
    if not prefix:
        return query
    return f"{prefix} {query}".strip()


def _cache_signature() -> tuple[str, str, str, str, str]:
    config = get_config()
    embedding_prefix = config.embedding_query_prefix if config.embedding_use_prefix else ""
    return (
        config.embedding_model,
        embedding_prefix,
        str(config.embedding_use_prefix),
        config.rag_version,
        config.rag_experiment_tag,
    )


def encode_query(query: str, model: SentenceTransformer, cache_size: int = 0) -> np.ndarray:
    query_input = _apply_query_prefix(query)
    cache_key = (*_cache_signature(), query_input)
    if cache_size > 0:
        cached = _QUERY_EMBED_CACHE.get(cache_key)
        if cached is not None:
            return cached
    vector = model.encode(query_input, convert_to_numpy=True, normalize_embeddings=True)
    if cache_size > 0:
        _QUERY_EMBED_CACHE.put(cache_key, vector, cache_size)
    return vector


def search_qdrant(
    query_vector: np.ndarray,
    corpus: str,
    client: QdrantClient,
    top_k: int = 20,
    ef_search: int | None = None,
) -> List[dict]:
    collection_name = f"{corpus}_chunks"
    search_params = SearchParams(hnsw_ef=ef_search) if ef_search else None
    try:
        results = client.search(
            collection_name=collection_name,
            query_vector=query_vector.tolist(),
            limit=top_k,
            search_params=search_params,
        )
    except Exception as exc:  # noqa: BLE001
        log_event(
            "qdrant_search_failed",
            span="retrieval",
            collection=collection_name,
            error=str(exc),
        )
        return []
    items = []
    for r in results:
        payload = r.payload or {}
        parent_id = payload.get("parent_id")
        lesson_id = payload.get("lesson_id")
        if parent_id is None:
            continue
        items.append(
            {
                "child_chunk_id": str(r.id),
                "score": float(r.score),
                "parent_id": parent_id,
                "lesson_id": lesson_id,
                "source": "vector",
                "source_tag": payload.get("source_tag"),
                "collection": payload.get("collection"),
                "book_slug": payload.get("book_slug"),
                "lesson_code": payload.get("lesson_code"),
                "section_title": payload.get("section_title"),
                "chunk_index": payload.get("chunk_index"),
                "node_path": payload.get("node_path"),
                "section_path": payload.get("section_path"),
                "page_start": payload.get("page_start"),
                "page_end": payload.get("page_end"),
                "is_front_matter": bool(payload.get("is_front_matter", False)),
            }
        )
    return items


def fetch_child_chunk_texts(child_chunk_ids: List[str], session: Session) -> dict[str, str]:
    rows = session.execute(
        select(TranscriptChildChunk.id, TranscriptChildChunk.content).where(TranscriptChildChunk.id.in_(child_chunk_ids))
    ).all()
    return {str(row[0]): row[1] for row in rows}


def _build_or_tsquery(query: str) -> str:
    """Build an OR-of-terms tsquery string from alphanumeric, non-stopword tokens.

    Only ``[A-Za-z0-9]`` tokens survive, so the result is always a safe ``to_tsquery``
    input (no operators or punctuation that could break parsing or inject SQL).
    """
    tokens = [token for token in re.findall(r"[A-Za-z0-9]+", query.lower()) if token not in STOPWORDS]
    return " | ".join(tokens)


def _merge_bm25_rows(primary: list, fallback: list) -> list:
    """Union BM25 result rows by child id (row[0]), keeping the higher ts_rank_cd (row[3])."""
    by_id: dict[str, Any] = {}
    for row in list(primary) + list(fallback):
        child_id = str(row[0])
        existing = by_id.get(child_id)
        if existing is None or float(row[3]) > float(existing[3]):
            by_id[child_id] = row
    return list(by_id.values())


def _bm25_sql(tsquery_expr: str) -> Any:
    """Build the BM25 SELECT for a fixed tsquery expression (literal, never user data)."""
    return sql_text(
        f"""
        SELECT cc.id,
               cc.parent_chunk_id,
               pc.lesson_id,
               ts_rank_cd(to_tsvector('english', cc.content), {tsquery_expr}) AS rank,
               ft.name AS source_tag,
               cc.metadata_json
        FROM child_chunks cc
        JOIN parent_chunks pc ON cc.parent_chunk_id = pc.id
        JOIN lessons l ON pc.lesson_id = l.id
        JOIN source_files sf ON l.source_file_id = sf.id
        JOIN file_tags ft ON sf.file_tag_id = ft.id
        WHERE cc.corpus_id = :corpus_id
          AND to_tsvector('english', cc.content) @@ {tsquery_expr}
        ORDER BY rank DESC
        LIMIT :limit
        """
    )


def search_bm25(query: str, corpus: str, session: Session, limit: int = 120) -> List[dict]:
    corpus_id = session.execute(select(Corpus.id).where(Corpus.name == corpus)).scalar_one_or_none()
    if corpus_id is None:
        return []
    config = get_config()
    sql = _bm25_sql("plainto_tsquery('english', :query)")
    rows = session.execute(sql, {"query": query, "limit": limit, "corpus_id": corpus_id}).all()
    if getattr(config, "rag_bm25_or_fallback_enabled", False) and len(rows) < max(
        0, int(config.rag_bm25_or_fallback_min_hits)
    ):
        or_query = _build_or_tsquery(query)
        if or_query:
            or_rows = session.execute(
                _bm25_sql("to_tsquery('english', :or_query)"),
                {"or_query": or_query, "limit": limit, "corpus_id": corpus_id},
            ).all()
            rows = _merge_bm25_rows(rows, or_rows)
    items: list[dict] = []
    for row in rows:
        meta_fields = _extract_chunk_meta_fields(row[5] if isinstance(row[5], dict) else None)
        items.append(
            {
                "child_chunk_id": str(row[0]),
                "score": float(row[3]),
                "parent_id": str(row[1]),
                "lesson_id": row[2],
                "source": "bm25",
                "source_tag": row[4],
                "node_path": meta_fields["node_path"],
                "section_path": meta_fields["section_path"],
                "page_start": meta_fields["page_start"],
                "page_end": meta_fields["page_end"],
                "is_front_matter": meta_fields["is_front_matter"],
            }
        )
    return items


def search_bm25_isolated(query: str, corpus: str, limit: int = 120) -> List[dict]:
    """Run BM25 search in its own short-lived DB session for thread safety."""
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        return search_bm25(query, corpus, session, limit=limit)
    finally:
        session.close()


def merge_candidates(candidates: List[dict]) -> List[dict]:
    merged: dict[str, dict] = {}
    for candidate in candidates:
        child_id = candidate["child_chunk_id"]
        if child_id in merged:
            existing = merged[child_id]
            if candidate.get("score", 0.0) > existing.get("score", 0.0):
                existing["score"] = candidate.get("score", 0.0)
            existing["source"] = f"{existing.get('source')},{candidate.get('source')}"
            for key in (
                "source_tag",
                "collection",
                "book_slug",
                "lesson_code",
                "section_title",
                "chunk_index",
                "node_path",
                "section_path",
                "page_start",
                "page_end",
                "is_front_matter",
            ):
                if not existing.get(key) and candidate.get(key):
                    existing[key] = candidate.get(key)
        else:
            merged[child_id] = dict(candidate)
    return list(merged.values())


def reciprocal_rank_fusion(
    vector_candidates: List[dict], bm25_candidates: List[dict], k: int = 60
) -> List[dict]:
    """Fuse two ranked candidate lists by Reciprocal Rank Fusion.

    Each source is ranked by its own descending ``score`` and contributes
    ``1 / (k + rank)`` (1-based rank) to a candidate's fused score. The returned
    list is the merged union of both sources, ordered by fused score descending.
    Only ``fused_rank_score`` is written; ``score`` is left untouched so the
    downstream rerank/weighting math is unchanged.
    """
    fused_scores: dict[str, float] = {}
    for source in (vector_candidates, bm25_candidates):
        ordered = sorted(source, key=lambda c: c.get("score", 0.0), reverse=True)
        for rank, candidate in enumerate(ordered):
            child_id = candidate["child_chunk_id"]
            fused_scores[child_id] = fused_scores.get(child_id, 0.0) + 1.0 / (k + rank + 1)
    merged = merge_candidates(vector_candidates + bm25_candidates)
    for candidate in merged:
        candidate["fused_rank_score"] = fused_scores.get(candidate["child_chunk_id"], 0.0)
    merged.sort(key=lambda c: c["fused_rank_score"], reverse=True)
    return merged


def _rank_sort_key(candidate: dict) -> tuple[float, str]:
    """Total-order sort key: rerank_score descending, child_chunk_id ascending."""
    return (-(float(candidate.get("rerank_score") or 0.0)), str(candidate.get("child_chunk_id") or ""))


def _sort_by_rerank(ranked: List[dict], config) -> List[dict]:
    """Sort by rerank_score descending; deterministic tie-break when enabled."""
    if getattr(config, "rag_stable_tiebreak", False):
        return sorted(ranked, key=_rank_sort_key)
    return sorted(ranked, key=lambda x: float(x.get("rerank_score") or 0.0), reverse=True)


def rerank_results(
    query: str,
    candidates: List[dict],
    chunk_texts: dict[str, str],
    reranker: CrossEncoder,
    top_k: int | None = 5,
    batch_size: int | None = None,
    cache_size: int = 0,
) -> List[dict]:
    active_candidates: list[dict] = []
    pairs = []
    pair_indexes = []
    skipped_missing_text = 0
    config = get_config()
    cache_signature = (*_cache_signature(), config.reranker_model)
    for candidate in candidates:
        chunk_text = chunk_texts.get(candidate["child_chunk_id"])
        if not chunk_text:
            skipped_missing_text += 1
            continue
        idx = len(active_candidates)
        active_candidates.append(candidate)
        cache_key = (*cache_signature, candidate["child_chunk_id"], query, chunk_text[:200])
        if cache_size > 0:
            cached = _RERANK_CACHE.get(cache_key)
            if cached is not None:
                candidate["rerank_score"] = float(cached)
                candidate["_rerank_cache_hit"] = True
                continue
        pairs.append([query, chunk_text])
        pair_indexes.append(idx)

    if skipped_missing_text:
        log_event(
            "rerank_missing_chunk_text_skipped",
            span="retrieval",
            skipped_count=skipped_missing_text,
        )
    if not active_candidates:
        return []

    if pairs:
        if batch_size:
            rerank_scores = reranker.predict(pairs, batch_size=batch_size)
        else:
            rerank_scores = reranker.predict(pairs)
        for i, score in enumerate(rerank_scores):
            candidate = active_candidates[pair_indexes[i]]
            candidate["rerank_score"] = float(score)
            if cache_size > 0:
                chunk_text = chunk_texts.get(candidate["child_chunk_id"])
                if chunk_text:
                    _RERANK_CACHE.put(
                        (*cache_signature, candidate["child_chunk_id"], query, chunk_text[:200]),
                        float(score),
                        cache_size,
                    )

    ranked = [candidate for candidate in active_candidates if "rerank_score" in candidate]
    ranked = _sort_by_rerank(ranked, config)
    if top_k is None:
        return ranked
    return ranked[:top_k]


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _info_density(text: str) -> float:
    tokens = [t.lower() for t in re.findall(r"[A-Za-z']+", text)]
    if not tokens:
        return 0.0
    content_tokens = [t for t in tokens if t not in STOPWORDS]
    if not content_tokens:
        return 0.0
    unique_tokens = len(set(content_tokens))
    return unique_tokens / len(content_tokens)


def _definition_like(text: str) -> bool:
    if DEFINITION_PATTERN.search(text):
        return True
    return bool(TERM_COLON_PATTERN.search(text.strip()))


def apply_rerank_weighting(
    candidates: List[dict], chunk_texts: dict[str, str], enable_density: bool = True
) -> List[dict]:
    for candidate in candidates:
        text = chunk_texts.get(candidate["child_chunk_id"], "")
        word_count = len(text.split())
        if word_count == 0:
            candidate["rerank_score"] = _apply_logit_penalty(
                candidate.get("rerank_score", 0.0), 0.6
            )
            continue
        if _definition_like(text):
            continue
        factor = _clamp(word_count / 120.0, 0.6, 1.0)
        if enable_density:
            density = _info_density(text)
            factor *= _clamp(density / 0.45, 0.7, 1.1)
        candidate["rerank_score"] = _apply_logit_penalty(
            candidate.get("rerank_score", 0.0), factor
        )
    return candidates


def apply_course_notes_front_matter_penalty(
    candidates: List[dict],
    factor: float = 0.85,
) -> List[dict]:
    for candidate in candidates:
        if (candidate.get("source_tag") or "") != "course_notes":
            continue
        if not bool(candidate.get("is_front_matter", False)):
            continue
        candidate["rerank_score"] = _apply_logit_penalty(
            candidate.get("rerank_score", 0.0),
            factor,
        )
    return candidates


def _apply_logit_penalty(score: float, factor: float) -> float:
    """Apply penalty to a logit score by scaling in probability space."""
    if factor >= 1.0:
        return score
    try:
        prob = 1.0 / (1.0 + math.exp(-score))
    except OverflowError:
        prob = 0.0 if score < 0 else 1.0
    prob = _clamp(prob * factor, 1e-6, 1.0 - 1e-6)
    return math.log(prob / (1.0 - prob))


def _token_set(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z']+", text or "")}


def apply_near_duplicate_dedup(
    ranked: List[dict], chunk_texts: dict[str, str], threshold: float = 0.92
) -> List[dict]:
    """Drop near-identical child chunks under the same parent (Jaccard >= threshold).

    Within each parent group, the lower-``rerank_score`` member of a near-duplicate
    pair is dropped. Survivor order is preserved. No-op when fewer than two children
    share a parent.
    """
    by_parent: dict[Any, list[dict]] = {}
    for candidate in ranked:
        by_parent.setdefault(candidate.get("parent_id"), []).append(candidate)

    drop_ids: set[str] = set()
    for group in by_parent.values():
        if len(group) < 2:
            continue
        signatures = {
            candidate["child_chunk_id"]: _token_set(chunk_texts.get(candidate["child_chunk_id"], ""))
            for candidate in group
        }
        for i in range(len(group)):
            ci = group[i]
            cid_i = ci["child_chunk_id"]
            if cid_i in drop_ids:
                continue
            sig_i = signatures[cid_i]
            if not sig_i:
                continue
            for j in range(i + 1, len(group)):
                cj = group[j]
                cid_j = cj["child_chunk_id"]
                if cid_j in drop_ids:
                    continue
                sig_j = signatures[cid_j]
                if not sig_j:
                    continue
                jaccard = len(sig_i & sig_j) / len(sig_i | sig_j)
                if jaccard < threshold:
                    continue
                if float(ci.get("rerank_score") or 0.0) <= float(cj.get("rerank_score") or 0.0):
                    drop_ids.add(cid_i)
                    break
                drop_ids.add(cid_j)

    if not drop_ids:
        return ranked
    return [candidate for candidate in ranked if candidate["child_chunk_id"] not in drop_ids]


def apply_parent_diversity(
    ranked: List[dict], max_per_parent: int = 3, stable: bool = False
) -> List[dict]:
    if stable:
        ranked = sorted(ranked, key=_rank_sort_key)
    counts: dict[str, int] = {}
    filtered: list[dict] = []
    for candidate in ranked:
        parent_id = candidate.get("parent_id")
        if not parent_id:
            continue
        count = counts.get(parent_id, 0)
        if count >= max_per_parent:
            continue
        counts[parent_id] = count + 1
        filtered.append(candidate)
    return filtered


def apply_source_diversity(
    ranked: List[dict],
    max_per_source: int = 3,
    keep_n: int | None = None,
) -> List[dict]:
    unique_tags = {candidate.get("source_tag") for candidate in ranked if candidate.get("source_tag")}
    if len(unique_tags) <= 1:
        return ranked

    counts: dict[str, int] = {}
    filtered: list[dict] = []
    overflow: list[dict] = []
    for candidate in ranked:
        tag = candidate.get("source_tag") or "unknown"
        count = counts.get(tag, 0)
        if count >= max_per_source:
            overflow.append(candidate)
            continue
        counts[tag] = count + 1
        filtered.append(candidate)

    target = keep_n if keep_n is not None else len(ranked)
    if len(filtered) < target:
        filtered.extend(overflow[: max(0, target - len(filtered))])
    return filtered


def _is_synthesis_query(query: str) -> bool:
    lower = query.lower()
    cues = [
        "compare",
        "contrast",
        "difference",
        "relationship",
        "connect",
        "how does",
        "in what ways",
        "both",
        "between",
        "synthesis",
    ]
    return any(cue in lower for cue in cues)


def _normalize_retrieval_mode(value: str | None) -> str | None:
    if not value:
        return None
    mode = value.strip().lower()
    if mode in {"fast", "deep"}:
        return mode
    return None


def _resolve_retrieval_mode(corpus: str, requested_mode: str | None, config) -> str:
    explicit = _normalize_retrieval_mode(requested_mode)
    if explicit:
        return explicit
    if corpus == "dean":
        dean_default = _normalize_retrieval_mode(config.rag_default_retrieval_mode_dean)
        if dean_default:
            return dean_default
    if corpus == "clough":
        clough_default = _normalize_retrieval_mode(config.rag_default_retrieval_mode_clough)
        if clough_default:
            return clough_default
    global_default = _normalize_retrieval_mode(config.rag_default_retrieval_mode)
    return global_default or "fast"


def fetch_child_chunk_meta(child_chunk_ids: List[str], session: Session) -> Dict[str, Tuple[str, int, str, dict]]:
    rows = session.execute(
        select(
            TranscriptChildChunk.id,
            TranscriptChildChunk.parent_chunk_id,
            TranscriptChildChunk.chunk_index,
            TranscriptChildChunk.content,
            TranscriptChildChunk.metadata_json,
        ).where(TranscriptChildChunk.id.in_(child_chunk_ids))
    ).all()
    return {str(row[0]): (str(row[1]), int(row[2]), row[3], row[4] or {}) for row in rows}


def expand_child_neighbors(
    primary_candidates: List[dict], session: Session, window: int = 2
) -> dict[str, dict]:
    primary_ids = [c["child_chunk_id"] for c in primary_candidates]
    primary_meta = fetch_child_chunk_meta(primary_ids, session)
    evidence: dict[str, dict] = {}
    parent_ids: set[str] = set()
    parent_windows: dict[str, list[tuple[int, int]]] = {}

    for candidate in primary_candidates:
        child_id = candidate["child_chunk_id"]
        meta = primary_meta.get(child_id)
        if not meta:
            continue
        parent_id, chunk_index, content, chunk_meta = meta
        meta_fields = _extract_chunk_meta_fields(chunk_meta if isinstance(chunk_meta, dict) else None)
        parent_ids.add(parent_id)
        parent_windows.setdefault(parent_id, []).append((max(chunk_index - window, 0), chunk_index + window))
        evidence[child_id] = {
            "child_chunk_id": child_id,
            "parent_chunk_id": parent_id,
            "child_chunk_index": chunk_index,
            "content": content,
            "is_primary": True,
            "context_only": False,
            "score": candidate.get("score", 0.0),
            "rerank_score": candidate.get("rerank_score"),
            "node_path": meta_fields["node_path"] or candidate.get("node_path"),
            "section_path": meta_fields["section_path"] or candidate.get("section_path"),
            "source_tag": candidate.get("source_tag") or meta_fields["source_tag"],
            "collection": candidate.get("collection"),
            "book_slug": candidate.get("book_slug"),
            "lesson_code": candidate.get("lesson_code"),
            "section_title": candidate.get("section_title"),
            "page_start": candidate.get("page_start") or meta_fields["page_start"],
            "page_end": candidate.get("page_end") or meta_fields["page_end"],
            "is_front_matter": bool(candidate.get("is_front_matter", meta_fields["is_front_matter"])),
        }

    if not parent_ids:
        return evidence

    range_filters = []
    for pid, ranges in parent_windows.items():
        for start_idx, end_idx in ranges:
            range_filters.append(
                and_(
                    TranscriptChildChunk.parent_chunk_id == pid,
                    TranscriptChildChunk.chunk_index >= start_idx,
                    TranscriptChildChunk.chunk_index <= end_idx,
                )
            )

    if not range_filters:
        return evidence

    neighbor_rows = session.execute(
            select(
                TranscriptChildChunk.id,
                TranscriptChildChunk.parent_chunk_id,
                TranscriptChildChunk.chunk_index,
                TranscriptChildChunk.content,
                TranscriptChildChunk.metadata_json,
            ).where(or_(*range_filters))
    ).all()

    for row in neighbor_rows:
        neighbor_id = str(row[0])
        if neighbor_id in evidence:
            continue
        parent_id = str(row[1])
        chunk_index = int(row[2])
        chunk_meta = row[4] or {}
        meta_fields = _extract_chunk_meta_fields(chunk_meta if isinstance(chunk_meta, dict) else None)
        evidence[neighbor_id] = {
            "child_chunk_id": neighbor_id,
            "parent_chunk_id": parent_id,
            "child_chunk_index": chunk_index,
            "content": row[3],
            "is_primary": False,
            "context_only": True,
            "score": None,
            "rerank_score": None,
            "node_path": meta_fields["node_path"],
            "section_path": meta_fields["section_path"],
            "source_tag": meta_fields["source_tag"],
            "collection": None,
            "book_slug": None,
            "lesson_code": None,
            "section_title": None,
            "page_start": meta_fields["page_start"],
            "page_end": meta_fields["page_end"],
            "is_front_matter": meta_fields["is_front_matter"],
        }

    return evidence


def fetch_parent_chunks(parent_ids: List[str], session: Session) -> List[dict]:
    base_stmt = (
        select(
            TranscriptParentChunk.id,
            TranscriptParentChunk.content,
            TranscriptParentChunk.section_title,
            TranscriptParentChunk.citation_ref,
            TranscriptParentChunk.metadata_json,
            TranscriptParentChunk.word_count,
            TranscriptLesson.lesson_number,
            TranscriptLesson.date,
            TranscriptLesson.speaker,
            TranscriptLesson.canonical_html_path,
            TranscriptPart.part_number,
            TranscriptPart.title,
            Series.title,
        )
        .join(TranscriptLesson, TranscriptParentChunk.lesson_id == TranscriptLesson.id)
        .join(TranscriptChapter, TranscriptLesson.chapter_id == TranscriptChapter.id, isouter=True)
        .join(TranscriptPart, func.coalesce(TranscriptChapter.part_id, TranscriptLesson.part_id) == TranscriptPart.id, isouter=True)
        .join(Series, TranscriptPart.series_id == Series.id, isouter=True)
        .where(TranscriptParentChunk.id.in_(parent_ids))
    )

    try:
        rows = session.execute(
            base_stmt.add_columns(
                DeanCollection.name,
                DeanBook.display_name,
                DeanLesson.lesson_code,
            )
            .join(DeanLesson, TranscriptLesson.dean_lesson_id == DeanLesson.id, isouter=True)
            .join(DeanBook, DeanLesson.book_id == DeanBook.id, isouter=True)
            .join(DeanCollection, DeanBook.collection_id == DeanCollection.id, isouter=True)
        ).all()
    except ProgrammingError as exc:
        # Backward-compatibility fallback for DBs without Dean tables/migrations.
        if "dean_lessons" not in str(exc).lower():
            raise
        session.rollback()
        rows = session.execute(
            base_stmt.add_columns(
                sql_text("NULL::text"),
                sql_text("NULL::text"),
                sql_text("NULL::text"),
            )
        ).all()

    results = []
    for row in rows:
        part_label = ""
        if row[10] is not None and row[11] is not None:
            part_label = f"Part {row[10]}: {row[11]}"
        series_label = row[12] or ""
        part_value = part_label
        lesson_value = row[6] if row[6] is not None else 0
        parent_meta = row[4] if isinstance(row[4], dict) else None
        meta_fields = _extract_chunk_meta_fields(parent_meta)
        source_tag = meta_fields["source_tag"]
        collection_name = meta_fields["collection"]
        if not collection_name:
            collection_name = row[13]
        book_slug = meta_fields["book_slug"]
        lesson_code = row[15] or meta_fields["lesson_code"]
        page_start = meta_fields["page_start"]
        page_end = meta_fields["page_end"]
        is_front_matter = bool(meta_fields["is_front_matter"])
        part_number_meta = meta_fields["part_number"]
        part_title_meta = meta_fields["part_title"]
        node_paths: list[str] = list(meta_fields["node_paths"])
        default_node_path = meta_fields["node_path"]
        section_path = meta_fields["section_path"]
        if row[13]:
            series_label = row[13]
            dean_book = row[14] or ""
            part_value = dean_book or part_value
            lesson_value = lesson_code or lesson_value
        citation = Citation(
            series=series_label,
            part=part_value,
            lesson=lesson_value,
            speaker=row[8] or "",
            date=row[7].isoformat() if row[7] else "",
            file_path=row[9] or "",
        )
        citation_ref = row[3]
        if source_tag == "course_notes":
            part_number = part_number_meta if part_number_meta is not None else row[10]
            part_title = part_title_meta or row[11]
            part_label = f"Course Notes Part {part_number}" if part_number else "Course Notes"
            if part_title and str(part_title).strip():
                part_label = f"{part_label}: {part_title}"
            resolved_path = default_node_path or section_path
            path_label = f"Path {resolved_path}" if resolved_path else None
            if page_start and page_end:
                page_label = f"pp.{page_start}-{page_end}" if int(page_start) != int(page_end) else f"p.{page_start}"
            else:
                page_label = None
            citation_ref = " · ".join([item for item in [part_label, path_label, page_label] if item])
            part_value = part_label
            citation = Citation(
                series=series_label or "Biblical Framework Course Notes",
                part=part_value,
                lesson=row[6] if row[6] is not None else "Course Notes",
                speaker=row[8] or "",
                date=row[7].isoformat() if row[7] else "",
                file_path=row[9] or "",
            )
        elif node_paths:
            citation_ref = f"{citation_ref} · Path {node_paths[0]}"
        results.append(
            {
                "parent_chunk_id": str(row[0]),
                "parent_text": row[1],
                "section_title": row[2],
                "citation_ref": citation_ref,
                "word_count": row[5],
                "citation": citation,
                "source_tag": source_tag,
                "collection": collection_name,
                "book_slug": book_slug,
                "lesson_code": lesson_code,
                "node_path": default_node_path,
                "section_path": section_path,
                "page_start": page_start,
                "page_end": page_end,
                "is_front_matter": is_front_matter,
            }
        )
    return results


def retrieve_context(
    query: str,
    corpus: str,
    top_k: int = 5,
    session: Session | None = None,
    embedder: Optional[SentenceTransformer] = None,
    reranker: Optional[CrossEncoder] = None,
    retrieval_mode: str | None = None,
    history: list[dict] | None = None,
) -> List[SearchResult]:
    results, _stats = retrieve_context_with_stats(
        query=query,
        corpus=corpus,
        top_k=top_k,
        session=session,
        embedder=embedder,
        reranker=reranker,
        retrieval_mode=retrieval_mode,
        history=history,
    )
    return results


def retrieve_context_with_stats(
    query: str,
    corpus: str,
    top_k: int = 5,
    session: Session | None = None,
    embedder: Optional[SentenceTransformer] = None,
    reranker: Optional[CrossEncoder] = None,
    retrieval_mode: str | None = None,
    history: list[dict] | None = None,
) -> tuple[List[SearchResult], dict]:
    setup_logging()
    log = logger.bind(function="retrieve_context", corpus=corpus)
    config = get_config()
    retrieval_query = rewrite_query(query, history=history)
    mode = _resolve_retrieval_mode(corpus, retrieval_mode, config)
    ef_search = config.qdrant_ef_search_fast if mode == "fast" else config.qdrant_ef_search_deep
    search_k_fast = max(1, int(config.rag_query_search_k_fast))
    search_k_deep = max(1, int(config.rag_query_search_k_deep))
    bm25_limit = max(1, int(config.rag_query_bm25_limit))
    retrieval_workers = max(1, min(int(config.rag_threadpool_workers), 2))
    search_k = search_k_deep if mode == "deep" else search_k_fast
    cap_bm25 = max(1, int(config.rag_rerank_cap_bm25))
    cap_vec = max(1, int(config.rag_rerank_cap_vec))
    primary_cap = max(1, min(int(top_k), 20))
    stats: dict = {
        "query_rewritten": retrieval_query != query,
        "retrieval_query": retrieval_query if retrieval_query != query else None,
        "bm25_candidates_count": 0,
        "vector_candidates_count": 0,
        "union_candidates_count": 0,
        "union_candidates_pretrim_count": 0,
        "union_candidates_posttrim_count": 0,
        "reranked_count": 0,
        "num_unique_parents_pre": 0,
        "num_unique_parents_final": 0,
        "t_rerank_ms": 0.0,
        "t_expand_ms": 0.0,
        "diversity_cap_enabled": bool(
            config.rag_retrieve_parent_diversity or (corpus == "clough" and config.clough_source_diversity_cap)
        ),
        "local_expansion_enabled": True,
        "hierarchy_boost_applied": False,
        "hierarchy_coverage": 0.0,
        "hierarchy_anchor_count": 0,
        "hierarchy_boosted_count": 0,
        "hierarchy_avg_bonus": 0.0,
    }

    if session is None:
        raise ValueError("Session is required")

    with SpanTimer("retrieval", top_k_requested=top_k) as retrieval_span:
        embedder = embedder or load_embedding_model()
        reranker = reranker or load_reranker()
        query_vector = encode_query(
            retrieval_query,
            embedder,
            config.rag_query_embed_cache_size,
        )
        log.info("query_encoded", rewritten=stats["query_rewritten"])

        client = get_qdrant_client(timeout=30.0)
        search_k = max(search_k, top_k * 2)
        bm25_candidates: list[dict] = []

        if (
            config.rag_parallel_retrieval
            and config.rag_hybrid_retrieval
            and retrieval_workers > 1
        ):
            with ThreadPoolExecutor(max_workers=retrieval_workers) as executor:
                vector_start = time.perf_counter()
                bm25_start = time.perf_counter()
                qdrant_future = executor.submit(
                    search_qdrant, query_vector, corpus, client, search_k, ef_search
                )
                bm25_future = executor.submit(search_bm25_isolated, retrieval_query, corpus, bm25_limit)
                vector_candidates = qdrant_future.result()
                bm25_candidates = bm25_future.result()
                log_event(
                    "bm25_search_end",
                    span="retrieval",
                    duration_ms=(time.perf_counter() - bm25_start) * 1000,
                    bm25_candidates_count=len(bm25_candidates),
                )
                log_event(
                    "vector_search_end",
                    span="retrieval",
                    duration_ms=(time.perf_counter() - vector_start) * 1000,
                    vector_candidates_count=len(vector_candidates),
                )
        else:
            vector_start = time.perf_counter()
            vector_candidates = search_qdrant(
                query_vector, corpus, client, top_k=search_k, ef_search=ef_search
            )
            log_event(
                "vector_search_end",
                span="retrieval",
                duration_ms=(time.perf_counter() - vector_start) * 1000,
                vector_candidates_count=len(vector_candidates),
            )
            if config.rag_hybrid_retrieval:
                bm25_start = time.perf_counter()
                bm25_candidates = search_bm25(retrieval_query, corpus, session, limit=bm25_limit)
                log_event(
                    "bm25_search_end",
                    span="retrieval",
                    duration_ms=(time.perf_counter() - bm25_start) * 1000,
                    bm25_candidates_count=len(bm25_candidates),
                )

        candidates = merge_candidates(vector_candidates + bm25_candidates)
        if config.rag_retrieve_parent_diversity:
            min_unique = (
                config.rag_parent_diversity_min_unique
                if _is_synthesis_query(query)
                else max(top_k, config.rag_parent_diversity_min_topk)
            )
            unique_parents = len({c["parent_id"] for c in candidates})
            if unique_parents < min_unique:
                extended_vec = search_qdrant(
                    query_vector,
                    corpus,
                    client,
                    top_k=min(max(cap_vec, max(search_k, top_k * 4)), 240),
                    ef_search=ef_search,
                )
                if config.rag_hybrid_retrieval:
                    candidates = merge_candidates(extended_vec + candidates)
                else:
                    candidates = extended_vec
                vector_candidates = extended_vec

        stats["vector_candidates_count"] = len(vector_candidates)
        stats["bm25_candidates_count"] = len(bm25_candidates)
        stats["union_candidates_pretrim_count"] = len(candidates)
        stats["num_unique_parents_pre"] = len({c["parent_id"] for c in candidates}) if candidates else 0

        cap_vec = max(0, cap_vec)
        cap_bm25 = max(0, cap_bm25)
        if config.rag_dynamic_rerank_enabled and candidates:
            vec_ids = {c["child_chunk_id"] for c in vector_candidates}
            bm25_ids = {c["child_chunk_id"] for c in bm25_candidates}
            overlap_ratio = len(vec_ids & bm25_ids) / max(1, min(len(vec_ids), len(bm25_ids)))
            if overlap_ratio >= config.rag_dynamic_rerank_overlap_high:
                cap_vec = int(cap_vec * config.rag_dynamic_rerank_reduce_factor)
                cap_bm25 = int(cap_bm25 * config.rag_dynamic_rerank_reduce_factor)
            elif overlap_ratio <= config.rag_dynamic_rerank_overlap_low:
                cap_vec = int(cap_vec * config.rag_dynamic_rerank_boost_factor)
                cap_bm25 = int(cap_bm25 * config.rag_dynamic_rerank_boost_factor)
            cap_vec = max(1, cap_vec)
            cap_bm25 = max(1, cap_bm25)
            log_event(
                "dynamic_rerank_cap",
                span="retrieval",
                overlap_ratio=overlap_ratio,
                cap_vec=cap_vec,
                cap_bm25=cap_bm25,
                union_candidates_pretrim_count=stats["union_candidates_pretrim_count"],
                vector_candidates_count=len(vector_candidates),
                bm25_candidates_count=len(bm25_candidates),
            )
        if config.rag_rrf_fusion_enabled:
            candidates = reciprocal_rank_fusion(vector_candidates, bm25_candidates)[: cap_vec + cap_bm25]
        else:
            capped_vector = vector_candidates[:cap_vec] if cap_vec else []
            capped_bm25 = bm25_candidates[:cap_bm25] if cap_bm25 else []
            candidates = merge_candidates(capped_vector + capped_bm25)

        stats["union_candidates_posttrim_count"] = len(candidates)
        stats["union_candidates_count"] = len(candidates)
        log_event(
            "candidate_merge_end",
            span="retrieval",
            union_candidates_pretrim_count=stats["union_candidates_pretrim_count"],
            union_candidates_posttrim_count=stats["union_candidates_posttrim_count"],
            vector_candidates_count=stats["vector_candidates_count"],
            bm25_candidates_count=stats["bm25_candidates_count"],
        )
        if not candidates:
            retrieval_span.add_fields(status="error")
            return [], stats

        child_ids = [c["child_chunk_id"] for c in candidates]
        chunk_texts = fetch_child_chunk_texts(child_ids, session)
        missing_or_empty = [
            candidate
            for candidate in candidates
            if candidate["child_chunk_id"] not in chunk_texts
            or not (chunk_texts.get(candidate["child_chunk_id"]) or "").strip()
        ]
        if missing_or_empty:
            log_event(
                "missing_chunk_texts_dropped",
                span="retrieval",
                dropped_count=len(missing_or_empty),
            )
            candidates = [
                candidate
                for candidate in candidates
                if candidate["child_chunk_id"] in chunk_texts
                and (chunk_texts.get(candidate["child_chunk_id"]) or "").strip()
            ]
            stats["union_candidates_posttrim_count"] = len(candidates)
            stats["union_candidates_count"] = len(candidates)
        if not candidates:
            retrieval_span.add_fields(status="error")
            return [], stats
        with SpanTimer("rerank") as rerank_span:
            rerank_start = time.perf_counter()
            ranked = rerank_results(
                retrieval_query,
                candidates,
                chunk_texts,
                reranker,
                None if config.rag_retrieve_parent_diversity else primary_cap,
                config.rag_rerank_batch_size,
                config.rag_rerank_cache_size,
            )
            stats["t_rerank_ms"] = (time.perf_counter() - rerank_start) * 1000
            cache_hits = sum(1 for c in candidates if c.get("_rerank_cache_hit"))
            if cache_hits:
                log_event(
                    "rerank_cache_stats",
                    span="rerank",
                    cache_hits=cache_hits,
                    cache_misses=len(candidates) - cache_hits,
                    cache_size=config.rag_rerank_cache_size,
                )
            for candidate in candidates:
                candidate.pop("_rerank_cache_hit", None)
            rerank_span.add_fields(reranked_count=len(ranked))
        if config.rag_rerank_weighting:
            ranked = apply_rerank_weighting(ranked, chunk_texts, enable_density=True)
            ranked = _sort_by_rerank(ranked, config)
        if corpus == "clough" and config.course_notes_front_matter_penalty:
            ranked = apply_course_notes_front_matter_penalty(
                ranked,
                factor=float(config.course_notes_front_matter_penalty_factor),
            )
            ranked = _sort_by_rerank(ranked, config)
        hierarchy_stats: dict[str, Any] = {
            "hierarchy_boost_applied": False,
            "hierarchy_coverage": 0.0,
            "hierarchy_anchor_count": 0,
            "hierarchy_boosted_count": 0,
            "hierarchy_avg_bonus": 0.0,
        }
        if config.rag_hierarchy_boost_enabled:
            ranked, hierarchy_stats = _apply_hierarchy_boost(ranked, config, corpus=corpus)
        stats.update(hierarchy_stats)
        if config.rag_retrieve_parent_diversity:
            if config.rag_child_dedup_enabled:
                ranked = apply_near_duplicate_dedup(
                    ranked,
                    chunk_texts,
                    threshold=float(config.rag_child_dedup_jaccard_threshold),
                )
            ranked = apply_parent_diversity(
                ranked,
                max_per_parent=max(1, int(config.rag_parent_diversity_max_per_parent)),
                stable=config.rag_parent_diversity_stable,
            )
        if corpus == "clough" and config.clough_source_diversity_cap:
            ranked = apply_source_diversity(
                ranked,
                max_per_source=max(1, int(config.clough_source_diversity_max_per_tag)),
                keep_n=primary_cap,
            )
        if len(ranked) > primary_cap:
            ranked = ranked[:primary_cap]

        stats["reranked_count"] = len(ranked)
        stats["num_unique_parents_final"] = len({c["parent_id"] for c in ranked}) if ranked else 0

        expand_start = time.perf_counter()
        evidence_map = expand_child_neighbors(ranked, session, window=2)
        stats["t_expand_ms"] = (time.perf_counter() - expand_start) * 1000
        log_event(
            "expand_end",
            span="retrieval",
            duration_ms=stats["t_expand_ms"],
            expanded_count=len(evidence_map),
        )
        if not evidence_map:
            retrieval_span.add_fields(status="error")
            return [], stats

        parent_ids = {item["parent_chunk_id"] for item in evidence_map.values()}
        parents = fetch_parent_chunks(list(parent_ids), session)
        parent_map = {parent["parent_chunk_id"]: parent for parent in parents}

        results: list[SearchResult] = []
        added_ids: set[str] = set()
        neighbors_by_parent: dict[str, list[dict]] = {}
        for item in evidence_map.values():
            parent_id = item["parent_chunk_id"]
            neighbors_by_parent.setdefault(parent_id, []).append(item)
        for parent_neighbors in neighbors_by_parent.values():
            parent_neighbors.sort(key=lambda item: item.get("child_chunk_index", 0))
        for candidate in ranked:
            primary = evidence_map.get(candidate["child_chunk_id"])
            if not primary:
                continue
            parent = parent_map.get(primary["parent_chunk_id"])
            if not parent:
                continue
            if primary["child_chunk_id"] in added_ids:
                continue
            results.append(
                SearchResult(
                    child_chunk_id=primary["child_chunk_id"],
                    parent_chunk_id=primary["parent_chunk_id"],
                    child_chunk_index=primary.get("child_chunk_index"),
                    source_tag=primary.get("source_tag") or parent.get("source_tag"),
                    collection=primary.get("collection") or parent.get("collection"),
                    book_slug=primary.get("book_slug") or parent.get("book_slug"),
                    lesson_code=primary.get("lesson_code") or parent.get("lesson_code"),
                    node_path=primary.get("node_path") or parent.get("node_path"),
                    section_path=primary.get("section_path") or parent.get("section_path"),
                    page_start=primary.get("page_start") or parent.get("page_start"),
                    page_end=primary.get("page_end") or parent.get("page_end"),
                    is_front_matter=bool(primary.get("is_front_matter", parent.get("is_front_matter", False))),
                    is_primary=True,
                    context_only=False,
                    parent_text=parent["parent_text"],
                    child_text=primary["content"],
                    score=primary.get("score") or 0.0,
                    rerank_score=primary.get("rerank_score"),
                    section_title=primary.get("section_title") or parent.get("section_title"),
                    citation_ref=parent.get("citation_ref"),
                    citation=parent["citation"],
                )
            )
            added_ids.add(primary["child_chunk_id"])

            neighbors = neighbors_by_parent.get(primary["parent_chunk_id"], [])
            for neighbor in neighbors:
                if neighbor["child_chunk_id"] == primary["child_chunk_id"]:
                    continue
                if neighbor["child_chunk_id"] in added_ids:
                    continue
                results.append(
                    SearchResult(
                        child_chunk_id=neighbor["child_chunk_id"],
                        parent_chunk_id=neighbor["parent_chunk_id"],
                        child_chunk_index=neighbor.get("child_chunk_index"),
                        source_tag=neighbor.get("source_tag") or parent.get("source_tag"),
                        collection=neighbor.get("collection") or parent.get("collection"),
                        book_slug=neighbor.get("book_slug") or parent.get("book_slug"),
                        lesson_code=neighbor.get("lesson_code") or parent.get("lesson_code"),
                        node_path=neighbor.get("node_path") or parent.get("node_path"),
                        section_path=neighbor.get("section_path") or parent.get("section_path"),
                        page_start=neighbor.get("page_start") or parent.get("page_start"),
                        page_end=neighbor.get("page_end") or parent.get("page_end"),
                        is_front_matter=bool(neighbor.get("is_front_matter", parent.get("is_front_matter", False))),
                        is_primary=False,
                        context_only=True,
                        parent_text=parent["parent_text"],
                        child_text=neighbor["content"],
                        score=0.0,
                        rerank_score=None,
                        section_title=neighbor.get("section_title") or parent.get("section_title"),
                        citation_ref=parent.get("citation_ref"),
                        citation=parent["citation"],
                    )
                )
                added_ids.add(neighbor["child_chunk_id"])

        retrieval_span.add_fields(
            status="ok",
            unique_parents_final=stats["num_unique_parents_final"],
            union_candidates_posttrim_count=stats["union_candidates_posttrim_count"],
        )
    return results, stats
