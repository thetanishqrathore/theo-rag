"""Helpers for building consistent citation labels and paths."""

from __future__ import annotations


def lesson_label(lesson: int | str) -> str:
    return f"Lesson {lesson}"


def build_citation_path(series: str, part: str, lesson: int | str) -> str:
    parts: list[str] = []
    if series:
        parts.append(series)
    if part:
        parts.append(part)
    parts.append(lesson_label(lesson))
    return " → ".join(parts)


def build_transcript_citation_ref(
    lesson: int | str,
    start_para: int,
    end_para: int,
    section_path: str | None = None,
    part_number: int | None = None,
    part_title: str | None = None,
    chapter_number: int | None = None,
    chapter_title: str | None = None,
    include_chapter: bool = True,
) -> str:
    parts: list[str] = []

    if part_number is not None:
        if part_title and part_title.strip():
            parts.append(f"Part {part_number}: {part_title.strip()}")
        else:
            parts.append(f"Part {part_number}")

    if include_chapter and chapter_number is not None and int(chapter_number) > 0:
        chapter_label = f"Chapter {chapter_number}"
        title = (chapter_title or "").strip()
        if title and title.lower() != "general":
            chapter_label = f"{chapter_label}: {title}"
        parts.append(chapter_label)

    parts.append(lesson_label(lesson))

    if section_path:
        parts.append(f"Sec {section_path}")
    parts.append(f"Para {start_para}-{end_para}")
    return " · ".join(parts)
