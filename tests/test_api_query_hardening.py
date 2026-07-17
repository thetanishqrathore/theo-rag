from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import types

import pytest
from fastapi import HTTPException


ROOT = Path("")
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
            _ = (args, kwargs)

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

if "sentence_transformers" not in sys.modules:
    sentence_transformers_stub = types.ModuleType("sentence_transformers")
    sentence_transformers_stub.SentenceTransformer = object
    sentence_transformers_stub.CrossEncoder = object
    sys.modules["sentence_transformers"] = sentence_transformers_stub

if "pgvector.sqlalchemy" not in sys.modules:
    pgvector_stub = types.ModuleType("pgvector")
    pgvector_sqlalchemy_stub = types.ModuleType("pgvector.sqlalchemy")
    from sqlalchemy.types import UserDefinedType

    class _Vector(UserDefinedType):
        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def get_col_spec(self, **_kwargs):
            return "VECTOR"

    pgvector_sqlalchemy_stub.Vector = _Vector
    pgvector_stub.sqlalchemy = pgvector_sqlalchemy_stub
    sys.modules["pgvector"] = pgvector_stub
    sys.modules["pgvector.sqlalchemy"] = pgvector_sqlalchemy_stub

import src.api.routes as routes
from src.api.models import QueryRequest


class _DummySession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _DummySemaphore:
    def __init__(self, acquire_ok: bool = True) -> None:
        self._acquire_ok = acquire_ok
        self.released = False

    def acquire(self, blocking: bool = False) -> bool:  # noqa: FBT001
        _ = blocking
        return self._acquire_ok

    def release(self) -> None:
        self.released = True


def test_query_request_top_k_default_is_five() -> None:
    request = QueryRequest(query="what is grace", corpus="clough")
    assert request.top_k == 5


def test_query_small_talk_short_bypasses_validate_query(monkeypatch) -> None:
    session = _DummySession()

    monkeypatch.setattr(routes, "setup_logging", lambda: None)
    monkeypatch.setattr(routes, "set_request_context", lambda **kwargs: None)
    monkeypatch.setattr(routes, "validate_corpus", lambda _corpus: None)
    monkeypatch.setattr(routes, "_persist_telemetry_rows", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "is_small_talk_query", lambda _query: True)
    monkeypatch.setattr(routes, "build_small_talk_reply", lambda _corpus: ("hello", []))

    def _fail_validate(_query: str) -> None:
        raise AssertionError("validate_query should not run for small-talk queries.")

    monkeypatch.setattr(routes, "validate_query", _fail_validate)

    response = routes.query(
        request=QueryRequest(query="hi", corpus="clough"),
        http_request=SimpleNamespace(headers={}),
        session=session,
    )

    assert response.answer == "hello"
    assert response.citations == []
    assert session.commits == 1


def test_query_stream_small_talk_short_bypasses_validate_query(monkeypatch) -> None:
    monkeypatch.setattr(routes, "setup_logging", lambda: None)
    monkeypatch.setattr(routes, "validate_corpus", lambda _corpus: None)
    monkeypatch.setattr(routes, "is_small_talk_query", lambda _query: True)
    monkeypatch.setattr(routes, "build_small_talk_reply", lambda _corpus: ("hello", []))

    def _fail_validate(_query: str) -> None:
        raise AssertionError("validate_query should not run for small-talk queries.")

    monkeypatch.setattr(routes, "validate_query", _fail_validate)

    response = routes.query_stream(
        request=QueryRequest(query="hi", corpus="clough"),
        http_request=SimpleNamespace(headers={}),
        session=_DummySession(),
    )

    assert response.media_type == "text/event-stream"


def test_query_accepts_flat_citation_cards_and_tracks_matched_rows(monkeypatch) -> None:
    session = _DummySession()
    semaphore = _DummySemaphore(acquire_ok=True)
    captured: dict = {}

    monkeypatch.setattr(routes, "setup_logging", lambda: None)
    monkeypatch.setattr(routes, "set_request_context", lambda **kwargs: None)
    monkeypatch.setattr(routes, "validate_corpus", lambda _corpus: None)
    monkeypatch.setattr(routes, "validate_query", lambda _query: None)
    monkeypatch.setattr(routes, "is_small_talk_query", lambda _query: False)
    monkeypatch.setattr(routes, "_get_generation_semaphore", lambda: (semaphore, 1))
    monkeypatch.setattr(
        routes,
        "retrieve_context_with_stats",
        lambda **_kwargs: (
            [
                SimpleNamespace(
                    child_chunk_id="match-1",
                    parent_chunk_id="p-1",
                    rerank_score=3.1,
                    is_primary=True,
                    context_only=False,
                )
            ],
            {"query_rewritten": False},
        ),
    )
    monkeypatch.setattr(
        routes,
        "get_config",
        lambda: SimpleNamespace(
            ollama_model="mistral",
            ollama_keepalive="5m",
            embedding_model="embed-model",
            reranker_model="rerank-model",
        ),
    )
    monkeypatch.setattr(routes, "maybe_warm_ollama_model", lambda *_args, **_kwargs: (False, 0.0))

    generation_result = SimpleNamespace(
        answer="Answer text",
        refusal=False,
        answer_mode="narrow",
        grounding_score=True,
        sufficiency_metrics={"s1": 0.4, "s5": 0.5, "overlap": 0.6},
        citation_items=[
            {
                "evidence_id": "match-1",
                "chunk_id": "match-1",
                "parent_id": "p-1",
                "parent_chunk_id": "p-1",
                "series": "Series A",
                "part": "Part 1",
                "lesson": 1,
                "date": "2025-01-01",
                "speaker": "Speaker A",
                "child_chunk_index": 0,
                "is_primary": True,
                "context_only": False,
                "source_tag": "transcripts",
                "collection": None,
                "book_slug": None,
                "lesson_code": None,
                "node_path": None,
                "section_path": None,
                "page_start": None,
                "page_end": None,
                "is_front_matter": False,
                "citation_ref": "[chunk:match-1]",
                "full_text": "matched parent",
                "parent_text": "matched parent",
                "child_text": "matched child",
                "file_path": "data/canonical/match.html",
                "preview": "matched child",
            },
            {
                "evidence_id": "fallback-2",
                "chunk_id": "fallback-2",
                "parent_id": "p-2",
                "parent_chunk_id": "p-2",
                "series": "Series B",
                "part": "Part 2",
                "lesson": 2,
                "date": "2025-01-02",
                "speaker": "Speaker B",
                "child_chunk_index": 1,
                "is_primary": True,
                "context_only": True,
                "source_tag": "transcripts",
                "collection": None,
                "book_slug": None,
                "lesson_code": None,
                "node_path": None,
                "section_path": None,
                "page_start": None,
                "page_end": None,
                "is_front_matter": False,
                "citation_ref": "[chunk:fallback-2]",
                "full_text": "fallback parent",
                "parent_text": "fallback parent",
                "child_text": "fallback child",
                "file_path": "data/canonical/fallback.html",
                "preview": "fallback child",
            },
        ],
        matched_evidence_ids=["match-1"],
        num_citations_emitted=2,
        num_citations_matched=1,
        grounding_reason="cited",
        citation_retry_used=False,
        citation_retry_succeeded=False,
        citation_automap_used=False,
        citation_automap_succeeded=False,
        citation_fallback_reason="narrow_answer_no_primary_matches_found",
        citation_strategy_version="recall-first-single-profile-v1",
        follow_up_questions=[],
    )
    monkeypatch.setattr(routes, "generate_grounded_answer", lambda **_kwargs: generation_result)

    def _capture_persist(
        _session,
        *,
        run_id: str,
        telemetry_payload,
        rerank_rows=None,
        evidence_rows=None,
    ) -> None:
        _ = run_id
        captured["telemetry_payload"] = telemetry_payload
        captured["rerank_rows"] = rerank_rows or []
        captured["evidence_rows"] = evidence_rows or []

    monkeypatch.setattr(routes, "_persist_telemetry_rows", _capture_persist)

    response = routes.query(
        request=QueryRequest(query="Explain the doctrine", corpus="clough"),
        http_request=SimpleNamespace(headers={"X-Session-Id": "session-123"}),
        session=session,
    )

    assert response.answer == "Answer text"
    assert response.source_count == 2
    assert response.num_citations_emitted == 2
    assert response.num_citations_matched == 1
    assert [item.child_chunk_id for item in response.citations] == ["match-1", "fallback-2"]

    evidence_rows = captured["evidence_rows"]
    assert len(evidence_rows) == 2
    assert evidence_rows[0]["matched_in_text"] is True
    assert evidence_rows[1]["matched_in_text"] is False
    assert captured["telemetry_payload"].num_citations_matched == 1
    assert session.commits == 1
    assert semaphore.released is True


def test_query_failure_persists_abort_telemetry(monkeypatch) -> None:
    session = _DummySession()
    semaphore = _DummySemaphore(acquire_ok=True)
    captured: dict = {}

    monkeypatch.setattr(routes, "setup_logging", lambda: None)
    monkeypatch.setattr(routes, "set_request_context", lambda **kwargs: None)
    monkeypatch.setattr(routes, "validate_corpus", lambda _corpus: None)
    monkeypatch.setattr(routes, "validate_query", lambda _query: None)
    monkeypatch.setattr(routes, "is_small_talk_query", lambda _query: False)
    monkeypatch.setattr(routes, "_get_generation_semaphore", lambda: (semaphore, 1))
    monkeypatch.setattr(
        routes,
        "retrieve_context_with_stats",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("retrieval failed")),
    )

    def _capture_persist(
        _session,
        *,
        run_id: str,
        telemetry_payload,
        rerank_rows=None,
        evidence_rows=None,
    ) -> None:
        _ = (rerank_rows, evidence_rows)
        captured["run_id"] = run_id
        captured["payload"] = telemetry_payload

    monkeypatch.setattr(routes, "_persist_telemetry_rows", _capture_persist)

    with pytest.raises(HTTPException) as exc:
        routes.query(
            request=QueryRequest(query="explain grace", corpus="clough"),
            http_request=SimpleNamespace(headers={"X-Session-Id": "session-123"}),
            session=session,
        )

    assert exc.value.status_code == 500
    assert "payload" in captured
    payload = captured["payload"]
    assert payload.status == "aborted"
    assert payload.error_type == "internal_error"
    assert payload.user_session_id == "session-123"
    assert session.commits == 1
    assert semaphore.released is True
