"""Context variables for request-scoped logging."""

from __future__ import annotations

import contextvars
import os
from typing import Any

from src.utils.config import get_config

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_route: contextvars.ContextVar[str | None] = contextvars.ContextVar("route", default=None)
_method: contextvars.ContextVar[str | None] = contextvars.ContextVar("method", default=None)
_corpus: contextvars.ContextVar[str | None] = contextvars.ContextVar("corpus", default=None)
_model: contextvars.ContextVar[str | None] = contextvars.ContextVar("model", default=None)
_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)
_query_hash: contextvars.ContextVar[str | None] = contextvars.ContextVar("query_hash", default=None)
_query_len: contextvars.ContextVar[int | None] = contextvars.ContextVar("query_len", default=None)
_query_text: contextvars.ContextVar[str | None] = contextvars.ContextVar("query_text", default=None)
_has_email: contextvars.ContextVar[bool | None] = contextvars.ContextVar("has_email", default=None)
_has_phone: contextvars.ContextVar[bool | None] = contextvars.ContextVar("has_phone", default=None)


def get_app_env() -> str:
    return os.getenv("APP_ENV") or os.getenv("ENV") or "dev"


def get_app_version() -> str:
    return get_config().rag_version


def set_request_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    route: str | None = None,
    method: str | None = None,
    corpus: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    query_hash: str | None = None,
    query_len: int | None = None,
    query_text: str | None = None,
    has_email: bool | None = None,
    has_phone: bool | None = None,
) -> None:
    if request_id is not None:
        _request_id.set(request_id)
    if trace_id is not None:
        _trace_id.set(trace_id)
    if route is not None:
        _route.set(route)
    if method is not None:
        _method.set(method)
    if corpus is not None:
        _corpus.set(corpus)
    if model is not None:
        _model.set(model)
    if session_id is not None:
        _session_id.set(session_id)
    if query_hash is not None:
        _query_hash.set(query_hash)
    if query_len is not None:
        _query_len.set(query_len)
    if query_text is not None:
        _query_text.set(query_text)
    if has_email is not None:
        _has_email.set(has_email)
    if has_phone is not None:
        _has_phone.set(has_phone)


def clear_request_context() -> None:
    _request_id.set(None)
    _trace_id.set(None)
    _route.set(None)
    _method.set(None)
    _corpus.set(None)
    _model.set(None)
    _session_id.set(None)
    _query_hash.set(None)
    _query_len.set(None)
    _query_text.set(None)
    _has_email.set(None)
    _has_phone.set(None)


def get_context() -> dict[str, Any]:
    return {
        "request_id": _request_id.get(),
        "trace_id": _trace_id.get(),
        "route": _route.get(),
        "method": _method.get(),
        "corpus": _corpus.get(),
        "model": _model.get(),
        "session_id": _session_id.get(),
        "query_hash": _query_hash.get(),
        "query_len": _query_len.get(),
        "query_text": _query_text.get(),
        "has_email": _has_email.get(),
        "has_phone": _has_phone.get(),
    }
