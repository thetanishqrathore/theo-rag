from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import structlog as _structlog  # noqa: F401
except Exception:
    structlog_stub = types.ModuleType("structlog")
    stdlib_stub = types.ModuleType("structlog.stdlib")
    contextvars_stub = types.ModuleType("structlog.contextvars")
    processors_stub = types.ModuleType("structlog.processors")

    class _Logger:
        def bind(self, **_kwargs):
            return self

        def info(self, *_args, **_kwargs):
            return None

        def warning(self, *_args, **_kwargs):
            return None

        def error(self, *_args, **_kwargs):
            return None

    class _LoggerFactory:
        def __call__(self, *_args, **_kwargs):
            return _Logger()

    class _ProcessorFormatter:
        def __init__(self, *args, **kwargs):
            pass

        @staticmethod
        def wrap_for_formatter(_logger, _method, event_dict):
            return event_dict

    class _NoopProcessor:
        def __call__(self, _logger=None, _method=None, event_dict=None):
            return event_dict or {}

    class _BoundLogger(_Logger):
        pass

    structlog_stub.get_logger = lambda *_args, **_kwargs: _Logger()
    structlog_stub.configure = lambda *args, **kwargs: None
    structlog_stub.stdlib = stdlib_stub
    structlog_stub.contextvars = contextvars_stub
    structlog_stub.processors = processors_stub

    stdlib_stub.LoggerFactory = _LoggerFactory
    stdlib_stub.BoundLogger = _BoundLogger
    stdlib_stub.ProcessorFormatter = _ProcessorFormatter
    contextvars_stub.merge_contextvars = lambda *_args, **_kwargs: None
    processors_stub.JSONRenderer = lambda *args, **kwargs: _NoopProcessor()
    processors_stub.TimeStamper = lambda *args, **kwargs: _NoopProcessor()
    processors_stub.StackInfoRenderer = lambda *args, **kwargs: _NoopProcessor()
    processors_stub.add_log_level = _NoopProcessor()
    processors_stub.format_exc_info = _NoopProcessor()

    sys.modules["structlog"] = structlog_stub
    sys.modules["structlog.stdlib"] = stdlib_stub
    sys.modules["structlog.contextvars"] = contextvars_stub
    sys.modules["structlog.processors"] = processors_stub

try:
    import ollama as _ollama  # noqa: F401
except Exception:
    ollama_stub = types.ModuleType("ollama")
    ollama_stub.Client = object
    sys.modules["ollama"] = ollama_stub

if "src.retrieval.retrieve" not in sys.modules:
    retrieve_stub = types.ModuleType("src.retrieval.retrieve")
    retrieve_stub.retrieve_context_with_stats = lambda *args, **kwargs: ([], {})
    retrieve_stub.retrieve_context = lambda *args, **kwargs: []
    sys.modules["src.retrieval.retrieve"] = retrieve_stub

if "sentence_transformers" not in sys.modules:
    sentence_transformers_stub = types.ModuleType("sentence_transformers")
    sentence_transformers_stub.SentenceTransformer = object
    sentence_transformers_stub.CrossEncoder = object
    sys.modules["sentence_transformers"] = sentence_transformers_stub

import src.generation.generate as gen
from src.retrieval.models import Citation, SearchResult


def _base_runtime_config(**overrides):
    config = {
        "rag_citations_parent_legacy": False,
        "rag_no_cite_retry_enabled": False,
        "rag_no_cite_retry_max": 1,
        "rag_no_cite_substantive_words": 35,
        "rag_citation_automap_enabled": True,
        "rag_citation_automap_min_sent_words": 6,
        "rag_citation_automap_min_score": 0.22,
        "rag_citation_automap_min_coverage": 0.70,
        "rag_citation_automap_max_cites_per_sentence": 2,
    }
    config.update(overrides)
    return SimpleNamespace(**config)


def _search_result(
    *,
    child_chunk_id: str,
    parent_chunk_id: str,
    child_text: str,
    rerank_score: float,
) -> SearchResult:
    return SearchResult(
        child_chunk_id=child_chunk_id,
        parent_chunk_id=parent_chunk_id,
        child_chunk_index=0,
        source_tag="transcripts",
        parent_text=child_text,
        child_text=child_text,
        score=1.0,
        rerank_score=rerank_score,
        citation_ref="Lesson 1 · Para 1-1",
        citation=Citation(
            series="Series",
            part="Part 1",
            lesson=1,
            speaker="Speaker",
            date="",
            file_path="data/canonical/sample.html",
        ),
    )


def test_normalize_citation_markup_variants() -> None:
    text = (
        "Point [CHUNK abcdef12-3456-7890-abcd-ef1234567890] "
        "then (source 2), ref:3 and [source 4,5]."
    )
    normalized = gen.normalize_citation_markup(text)

    assert "[chunk:abcdef12-3456-7890-abcd-ef1234567890]" in normalized
    assert "[SOURCE 2]" in normalized
    assert "[SOURCE 3]" in normalized
    assert "[SOURCE 4]" in normalized
    assert "[SOURCE 5]" in normalized


def test_extract_citations_rejects_unknown_chunk_ids(monkeypatch) -> None:
    monkeypatch.setattr(gen, "get_config", lambda: _base_runtime_config())
    source_map = {
        "abcdef12-3456-7890-abcd-ef1234567890": {
            "child_chunk_id": "abcdef12-3456-7890-abcd-ef1234567890",
            "parent_chunk_id": "feedface-1111-2222-3333-444444444444",
            "citation": Citation(series="S", part="P", lesson=1, speaker="X", date="", file_path="f"),
        },
        "__order__": ["abcdef12-3456-7890-abcd-ef1234567890"],
    }
    text = "Unsupported [chunk:11111111-2222-3333-4444-555555555555]."
    citations = gen.extract_citations(text, source_map)
    assert citations == []


def test_extract_citations_supports_bare_numeric_legacy(monkeypatch) -> None:
    monkeypatch.setattr(gen, "get_config", lambda: _base_runtime_config())
    source_map = {
        "abcdef12-3456-7890-abcd-ef1234567890": {
            "child_chunk_id": "abcdef12-3456-7890-abcd-ef1234567890",
            "parent_chunk_id": "feedface-1111-2222-3333-444444444444",
            "citation": Citation(series="S", part="P", lesson=1, speaker="X", date="", file_path="f"),
        },
        "fedcba98-7654-3210-abcd-ef1234567890": {
            "child_chunk_id": "fedcba98-7654-3210-abcd-ef1234567890",
            "parent_chunk_id": "feedface-1111-2222-3333-444444444444",
            "citation": Citation(series="S", part="P", lesson=1, speaker="X", date="", file_path="f"),
        },
        "__order__": [
            "abcdef12-3456-7890-abcd-ef1234567890",
            "fedcba98-7654-3210-abcd-ef1234567890",
        ],
    }
    citations = gen.extract_citations("Claim [1] and [2].", source_map)
    assert len(citations) == 2


def test_finalize_state_automap_success(monkeypatch) -> None:
    runtime_config = _base_runtime_config(rag_no_cite_substantive_words=6)
    monkeypatch.setattr(gen, "get_config", lambda: runtime_config)
    chunk = _search_result(
        child_chunk_id="abcdef12-3456-7890-abcd-ef1234567890",
        parent_chunk_id="feedface-1111-2222-3333-444444444444",
        child_text="The creator-creature distinction means God is not identical to creation.",
        rerank_score=3.5,
    )
    source_map = gen.build_source_map([chunk])

    final_state = gen._finalize_answer_state(
        query="What is the creator creature distinction?",
        raw_answer_text="The creator creature distinction means God is not identical to creation.",
        source_map=source_map,
        requested_mode="confident",
        parent_chunks=[chunk],
        config=runtime_config,
    )

    assert final_state["grounded"] is True
    assert final_state["refusal"] is False
    assert final_state["grounding_reason"] == "cited_auto_mapped"
    assert final_state["citation_automap_used"] is True
    assert final_state["citation_automap_succeeded"] is True
    assert "[chunk:abcdef12-3456-7890-abcd-ef1234567890]" in final_state["answer_text"]


def test_finalize_state_automap_fail_safe(monkeypatch) -> None:
    runtime_config = _base_runtime_config(rag_no_cite_substantive_words=6)
    monkeypatch.setattr(gen, "get_config", lambda: runtime_config)
    chunk = _search_result(
        child_chunk_id="abcdef12-3456-7890-abcd-ef1234567890",
        parent_chunk_id="feedface-1111-2222-3333-444444444444",
        child_text="This chunk discusses a different topic entirely.",
        rerank_score=0.0,
    )
    source_map = gen.build_source_map([chunk])

    final_state = gen._finalize_answer_state(
        query="What is the creator creature distinction?",
        raw_answer_text="The creator creature distinction means God is not identical to creation and remains distinct.",
        source_map=source_map,
        requested_mode="confident",
        parent_chunks=[chunk],
        config=runtime_config,
    )

    assert final_state["grounded"] is False
    assert final_state["refusal"] is False
    assert final_state["answer_mode"] == "narrow"
    assert final_state["grounding_reason"] == "uncited_narrow"
    assert final_state["citation_automap_used"] is True
    assert final_state["citation_automap_succeeded"] is False


def test_finalize_state_refusal_never_automaps(monkeypatch) -> None:
    monkeypatch.setattr(gen, "get_config", lambda: _base_runtime_config())
    runtime_config = _base_runtime_config()
    chunk = _search_result(
        child_chunk_id="abcdef12-3456-7890-abcd-ef1234567890",
        parent_chunk_id="feedface-1111-2222-3333-444444444444",
        child_text="Any text",
        rerank_score=2.0,
    )
    source_map = gen.build_source_map([chunk])

    final_state = gen._finalize_answer_state(
        query="Unsupported query",
        raw_answer_text=gen.REFUSAL_TEXT,
        source_map=source_map,
        requested_mode="narrow",
        parent_chunks=[chunk],
        config=runtime_config,
    )

    assert final_state["refusal"] is True
    assert final_state["grounding_reason"] == "refusal"
    assert final_state["citation_automap_used"] is False
    assert final_state["citation_automap_succeeded"] is False
