"""FastAPI middleware for structured request logging."""

from __future__ import annotations

import json
import time
import uuid
from typing import Callable, Awaitable

from fastapi import Request, Response
from starlette.types import ASGIApp, Receive, Send
import anyio

from src.observability.context import set_request_context, clear_request_context
from src.observability.logging import log_event
from src.observability.redaction import hash_query, redact_query, detect_email, detect_phone
from src.utils.config import get_config


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = None
        session_id = None
        route = scope.get("path")
        method = scope.get("method")

        inner_receive = receive
        body = b""
        more_body = True
        while more_body:
            message = await inner_receive()
            body += message.get("body", b"")
            more_body = message.get("more_body", False)

        body_sent = False

        async def receive_wrapper() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await inner_receive()

        request = Request(scope, receive_wrapper)
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        session_id = request.headers.get("X-Session-Id")

        query_hash = None
        query_len = None
        query_text = None
        has_email = None
        has_phone = None
        corpus = None
        model = None

        try:
            if route in {"/api/query", "/api/query/stream"} and body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    query = payload.get("query") if isinstance(payload, dict) else None
                    if isinstance(payload, dict):
                        corpus = payload.get("corpus")
                        model = payload.get("model")
                    if isinstance(query, str):
                        query_len = len(query)
                        query_hash = hash_query(query)
                        has_email = detect_email(query)
                        has_phone = detect_phone(query)
                        if get_config().telemetry_store_query_text:
                            query_text = redact_query(query)
                except Exception:
                    pass
        except Exception:
            pass

        set_request_context(
            request_id=request_id,
            trace_id=request_id,
            route=route,
            method=method,
            session_id=session_id,
            corpus=corpus,
            model=model,
            query_hash=query_hash,
            query_len=query_len,
            query_text=query_text,
            has_email=has_email,
            has_phone=has_phone,
        )

        start = time.perf_counter()
        log_event("request_start", route=route, method=method, status="ok")

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("utf-8")))
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
            duration_ms = (time.perf_counter() - start) * 1000
            log_event(
                "request_end",
                route=route,
                method=method,
                status="ok",
                duration_ms=duration_ms,
            )
        except (anyio.EndOfStream, anyio.WouldBlock, anyio.ExceptionGroup, anyio.get_cancelled_exc_class()):  # type: ignore[arg-type]
            duration_ms = (time.perf_counter() - start) * 1000
            log_event(
                "request_aborted",
                route=route,
                method=method,
                status="aborted",
                duration_ms=duration_ms,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000
            log_event(
                "request_error",
                route=route,
                method=method,
                status="error",
                error=str(exc),
                duration_ms=duration_ms,
            )
            raise
        finally:
            clear_request_context()
