"""Structured logging helpers with enforced schema."""

from __future__ import annotations

import json
import logging
import sys

import structlog

from src.observability.context import get_app_env, get_app_version, get_context


REQUIRED_FIELDS = [
    "service",
    "env",
    "version",
    "request_id",
    "trace_id",
    "route",
    "method",
    "corpus",
    "model",
    "query_hash",
    "query_len",
    "session_id",
]


_FALLBACK_LOGGER = logging.getLogger("theo_rag.observability_fallback")
if not _FALLBACK_LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _FALLBACK_LOGGER.addHandler(_handler)
_FALLBACK_LOGGER.setLevel(logging.INFO)
_FALLBACK_LOGGER.propagate = False


def ensure_schema(_logger, _method_name: str, event_dict: dict) -> dict:
    context = get_context()
    for key in REQUIRED_FIELDS:
        if key in event_dict:
            continue
        if key in context and context[key] is not None:
            event_dict[key] = context[key]
    if "service" not in event_dict:
        event_dict["service"] = "api"
    if "env" not in event_dict:
        event_dict["env"] = get_app_env()
    if "version" not in event_dict:
        event_dict["version"] = get_app_version()
    if "request_id" not in event_dict:
        event_dict["request_id"] = context.get("request_id")
    if "trace_id" not in event_dict:
        event_dict["trace_id"] = context.get("trace_id")
    for key in REQUIRED_FIELDS:
        event_dict.setdefault(key, None)
    event_dict.setdefault("event", event_dict.get("event", "log_event"))
    return event_dict


def log_event(event: str, **fields) -> None:
    logger = structlog.get_logger()
    try:
        logger.info(event, **fields)
    except Exception as exc:  # noqa: BLE001
        fallback = {"event": event, **fields, "logging_error": str(exc)}
        try:
            _FALLBACK_LOGGER.error("structlog_emit_failed %s", json.dumps(fallback, default=str))
        except Exception as fallback_exc:  # noqa: BLE001
            message = (
                "structlog_emit_failed "
                f"event={event} "
                f"fields={fields} "
                f"fallback_error={fallback_exc}"
            )
            try:
                sys.stderr.write(message + "\n")
                sys.stderr.flush()
            except OSError:
                return
