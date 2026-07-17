"""Span timing helpers."""

from __future__ import annotations

import time
from typing import Any

from src.observability.logging import log_event


class SpanTimer:
    def __init__(self, name: str, **fields: Any) -> None:
        self.name = name
        self.fields = fields
        self.start = 0.0

    def add_fields(self, **fields: Any) -> None:
        self.fields.update(fields)

    def __enter__(self) -> "SpanTimer":
        self.start = time.perf_counter()
        log_event(f"{self.name}_start", span=self.name, **self.fields)
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        duration_ms = (time.perf_counter() - self.start) * 1000
        status = "error" if exc_type else "ok"
        fields = dict(self.fields)
        # Allow caller-provided status without duplicate keyword errors.
        status = str(fields.pop("status", status))
        log_event(
            f"{self.name}_end",
            span=self.name,
            duration_ms=duration_ms,
            status=status,
            **fields,
        )
