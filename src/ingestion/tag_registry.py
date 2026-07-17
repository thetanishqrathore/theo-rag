"""Tag registry for document processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class TagSpec:
    """Definition of a file tag."""

    name: str
    corpus: str
    table: str
    strategy: str
    status: str
    source_root: str | None = None


TAG_REGISTRY: Dict[str, TagSpec] = {
    "transcripts": TagSpec(
        name="transcripts",
        corpus="clough",
        table="lessons",
        strategy="TranscriptStrategy",
        status="active",
    ),
    "course_notes": TagSpec(
        name="course_notes",
        corpus="clough",
        table="lessons",
        strategy="CourseNoteV2Strategy",
        status="active",
    ),
    "mathew_series": TagSpec(
        name="mathew_series",
        corpus="dean",
        table="dean_chapters",
        strategy="BookStrategy",
        status="future",
    ),
    "dean_bible_books": TagSpec(
        name="dean_bible_books",
        corpus="dean",
        table="dean_lessons",
        strategy="DeanHtmlBibleStrategy",
        status="active",
        source_root="transcripts_books_bible",
    ),
    "dean_topical_html": TagSpec(
        name="dean_topical_html",
        corpus="dean",
        table="dean_lessons",
        strategy="DeanHtmlTopicalStrategy",
        status="active",
        source_root="topical_studies",
    ),
    "dean_topical_pdfs": TagSpec(
        name="dean_topical_pdfs",
        corpus="dean",
        table="dean_lessons",
        strategy="DeanPdfTopicalStrategy",
        status="active",
        source_root="topical_studies",
    ),
    "dean_topical_doc": TagSpec(
        name="dean_topical_doc",
        corpus="dean",
        table="dean_lessons",
        strategy="DeanDocTopicalStrategy",
        status="active",
        source_root="topical_studies",
    ),
    "dean_topical_rtf": TagSpec(
        name="dean_topical_rtf",
        corpus="dean",
        table="dean_lessons",
        strategy="DeanRtfTopicalStrategy",
        status="active",
        source_root="topical_studies",
    ),
}


def get_tag_spec(tag: str) -> TagSpec:
    """Fetch a tag spec or raise ValueError."""
    if tag not in TAG_REGISTRY:
        raise ValueError(f"Unknown tag '{tag}'. Must be one of: {', '.join(TAG_REGISTRY.keys())}")
    return TAG_REGISTRY[tag]
