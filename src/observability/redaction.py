"""Redaction and hashing utilities for logging."""

from __future__ import annotations

import hashlib
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
SECRET_RE = re.compile(r"\b(?:api[_-]?key|token|secret|bearer)\b\s*[:=]?\s*([A-Za-z0-9_\-\.=]{8,})", re.IGNORECASE)


def hash_query(query: str) -> str:
    normalized = " ".join(query.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def detect_email(query: str) -> bool:
    return bool(EMAIL_RE.search(query))


def detect_phone(query: str) -> bool:
    return bool(PHONE_RE.search(query))


def redact_query(query: str) -> str:
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", query)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = SECRET_RE.sub("secret=[REDACTED]", redacted)
    return redacted
