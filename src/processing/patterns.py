"""Shared regex patterns for processing, validation, and extraction."""

from __future__ import annotations

import re

CHAPTER_HEADING_RE = re.compile(r"^(?:chapter|ch\.?)\s+[ivxlcm\d]+\s*[:\u2013\-]\s*.+$", re.IGNORECASE)
LESSON_HEADING_RE = re.compile(r"^lesson\s+\d+\s*[\u2013\-:]\s*.+$", re.IGNORECASE)
APPENDIX_HEADING_RE = re.compile(
    r"^(?:appendix\s+[a-z]|conclusion|part\s+\d+\s+review\s+lessons)\b.*$",
    re.IGNORECASE,
)

PAGE_MARKER_RE = re.compile(r"\bPage\s+\d+(?:\s+of\s+\d+)?\b", re.IGNORECASE)
COPYRIGHT_RE = re.compile(r"(?:^|\s)(?:©|copyright\b|all rights reserved)", re.IGNORECASE)
REPEATED_WS_RE = re.compile(r"\s+")

MARKDOWN_LEAK_RE = re.compile(r"(^#{1,6}\s)|(\*\*)|(^>\s)|(\[[^\]]+\]\([^)]+\))|(`[^`]+`)")
