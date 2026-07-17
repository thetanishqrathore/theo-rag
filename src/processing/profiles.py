"""Shared profile helpers for processing and ingestion."""

from __future__ import annotations


def detect_dean_html_profile(tag_name: str | None) -> str:
    if (tag_name or "") == "dean_bible_books":
        return "dean_bible_html_v2"
    return "dean_topical_html_v2"


def detect_ingest_doc_profile(rel_path: str) -> str:
    normalized = rel_path.replace("\\", "/").lower()
    if "ot_survey" in normalized:
        return "lecture_transcript_doc"
    return "doctrinal_outline_doc"
