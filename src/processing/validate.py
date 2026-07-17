"""Validation rules for structured lessons."""

from __future__ import annotations

import re
from typing import Any, List, Tuple

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    DeanLesson,
    FileTag,
    SourceFile,
    TranscriptChapter,
    TranscriptLesson,
    TranscriptParagraph,
)
from src.processing.course_note_nodes import is_course_note_node_lesson, load_course_note_nodes
from src.processing.patterns import MARKDOWN_LEAK_RE, PAGE_MARKER_RE
from src.processing.semantic_review import run_shadow_semantic_review
from src.processing.structured_nodes import is_dean_node_lesson, load_dean_structured_nodes
from src.utils.config import get_config
from src.utils.logging import setup_logging

logger = structlog.get_logger()


def _is_copyright_footer_artifact(paragraph: TranscriptParagraph, total_paragraphs: int) -> bool:
    text = (paragraph.content or "").strip()
    lower = text.lower()
    if "©" not in text and "copyright" not in lower:
        return False
    words = len(text.split())
    if words <= 60:
        return True
    if paragraph.paragraph_index >= max(0, total_paragraphs - 5) and words <= 220:
        return True
    if re.search(r"(?:©|copyright)\s+[A-Za-z].{0,120}\b(?:19|20)\d{2}\b", text, re.IGNORECASE):
        return True
    return False

def _profile_thresholds(profile: str) -> dict:
    config = get_config()
    table = config.validation_profile_thresholds()
    return table.get(profile, table["default"])


def _profile_for_lesson(lesson: TranscriptLesson, session: Session) -> str:
    if not lesson.dean_lesson_id:
        source = session.execute(
            select(FileTag.name, SourceFile.source_format)
            .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
            .where(SourceFile.id == lesson.source_file_id)
        ).first()
        source_tag = source[0] if source else None
        source_format = str(source[1] or "").lower() if source else ""
        if source_tag == "course_notes":
            return "clough_course_notes_v2"
        if source_tag == "transcripts":
            if source_format in {"doc", "docx"}:
                return get_config().transcript_validation_profile or "clough_transcripts_doc_v3"
            return "clough_transcripts_pdf_v2"
        return "default"
    dean_lesson = session.execute(select(DeanLesson).where(DeanLesson.id == lesson.dean_lesson_id)).scalar_one_or_none()
    if not dean_lesson or not dean_lesson.metadata_json:
        return "default"
    return str(dean_lesson.metadata_json.get("parser_profile") or "default")


def _source_format_for_lesson(lesson: TranscriptLesson, session: Session) -> str:
    source_format = session.execute(
        select(SourceFile.source_format).where(SourceFile.id == lesson.source_file_id)
    ).scalar_one_or_none()
    return str(source_format or "").lower()


def check_sequential_indexes(paragraphs: List[TranscriptParagraph]) -> Tuple[bool, str]:
    indexes = [p.paragraph_index for p in paragraphs]
    if not indexes:
        return False, "No paragraphs found"
    if indexes[0] != 0:
        return False, "Paragraph indexes must start at 0"
    if indexes != list(range(len(indexes))):
        return False, "Paragraph indexes are not sequential"
    return True, "All indexes sequential"


def check_hierarchy_continuity(paragraphs: List[TranscriptParagraph], profile: str = "default") -> Tuple[bool, str]:
    paths = [p.hierarchy_path for p in paragraphs if p.hierarchy_path]
    if not paths:
        return False, "No hierarchy paths"
    unique = sorted(set(paths))
    if "1" not in unique:
        return False, "Hierarchy missing section 1"
    if profile == "default":
        return True, "Hierarchy valid"
    unique_parts = {tuple(int(part) for part in path.split(".") if part.isdigit()) for path in unique}
    orphan_count = 0
    for parts in unique_parts:
        if len(parts) <= 1:
            continue
        if parts[:-1] not in unique_parts:
            orphan_count += 1
    if orphan_count:
        return False, f"Hierarchy has {orphan_count} orphan subsection paths"
    return True, "Hierarchy valid"


def check_minimum_content(paragraphs: List[TranscriptParagraph], profile: str = "default") -> Tuple[bool, str]:
    thresholds = _profile_thresholds(profile)
    min_blocks = int(thresholds["min_blocks"])
    min_words = int(thresholds["min_words"])
    if len(paragraphs) < min_blocks:
        return False, f"Only {len(paragraphs)} paragraphs"
    total_words = sum(p.word_count for p in paragraphs)
    if total_words < min_words:
        return False, f"Only {total_words} total words"
    return True, f"{len(paragraphs)} paragraphs, {total_words} words"


def check_paragraph_length_bounds(paragraphs: List[TranscriptParagraph]) -> Tuple[bool, str]:
    if not paragraphs:
        return False, "No paragraphs"
    longest = max(paragraphs, key=lambda p: p.word_count)
    if longest.word_count > 1000:
        return False, f"Paragraph {longest.paragraph_index} has {longest.word_count} words"
    return True, f"Max paragraph: {longest.word_count} words"


def check_reading_flow(paragraphs: List[TranscriptParagraph], profile: str = "default") -> Tuple[bool, str]:
    thresholds = _profile_thresholds(profile)
    texts = [p.content.strip() for p in paragraphs if p.content]
    if not texts:
        return False, "No text content found"
    duplicate_ratio = (len(texts) - len(set(texts))) / len(texts)
    if duplicate_ratio > float(thresholds["dup_warn"]):
        return False, "Duplicate paragraph content detected"
    leak_markers = 0
    for p in paragraphs:
        if "©" in p.content and not p.is_junk:
            if _is_copyright_footer_artifact(p, len(paragraphs)):
                leak_markers += 1
                continue
            return False, "Copyright symbol found in non-junk paragraph"
        if "Page " in p.content:
            leak_markers += 1
    leakage_score = leak_markers / max(len(paragraphs), 1)
    if leakage_score > float(thresholds["leak_warn"]):
        return False, "Page marker detected in body text"
    return True, "No duplicates or corruption"


def check_markdown_leakage(paragraphs: List[TranscriptParagraph]) -> Tuple[bool, str]:
    if not paragraphs:
        return False, "No paragraphs"
    leak_count = 0
    for para in paragraphs:
        text = (para.content or "").strip()
        if not text:
            continue
        if MARKDOWN_LEAK_RE.search(text):
            leak_count += 1
    ratio = leak_count / max(len(paragraphs), 1)
    if ratio > 0.05:
        return False, f"Markdown control leakage ratio {ratio:.3f}"
    return True, "No markdown control leakage"


def check_node_parent_integrity(nodes: List[Any]) -> Tuple[bool, str]:
    if not nodes:
        return False, "No structured nodes"
    by_id = {node.id: node for node in nodes}
    for node in nodes:
        if node.parent_node_id is None:
            continue
        parent = by_id.get(node.parent_node_id)
        if parent is None:
            return False, f"Missing parent for node {node.id}"
        if parent.depth >= node.depth:
            return False, f"Invalid parent depth for node {node.id}"
    return True, "Node parent integrity valid"


def check_heading_list_coherence(nodes: List[Any]) -> Tuple[bool, str]:
    if not nodes:
        return False, "No structured nodes"
    bad_items = 0
    heading_depth_jumps = 0
    prev_heading_depth = None
    node_map = {node.id: node for node in nodes}
    for node in nodes:
        if node.node_type == "heading":
            current = int(node.depth or 1)
            if prev_heading_depth is not None and current - prev_heading_depth > 1:
                heading_depth_jumps += 1
            prev_heading_depth = current
        if node.node_type == "list_item":
            if node.parent_node_id is None:
                bad_items += 1
                continue
            parent = node_map.get(node.parent_node_id)
            if parent is None or parent.node_type != "list":
                bad_items += 1
    if bad_items > 0:
        return False, f"{bad_items} list items missing list container parent"
    if heading_depth_jumps > 4:
        return False, f"{heading_depth_jumps} abrupt heading depth jumps"
    return True, "Heading/list coherence valid"


def check_table_normalization(nodes: List[Any]) -> Tuple[bool, str]:
    table_nodes = [node for node in nodes if node.node_type == "table"]
    if not table_nodes:
        return True, "No table nodes present"
    missing_html = sum(1 for node in table_nodes if not (node.text_html or "").strip())
    if missing_html:
        return False, f"{missing_html} table nodes missing html fragment"
    return True, f"{len(table_nodes)} table nodes normalized"


def compute_validation_scores(
    paragraphs: List[TranscriptParagraph],
    profile: str = "default",
    nodes: List[Any] | None = None,
) -> dict:
    texts = [p.content.strip() for p in paragraphs if p.content]
    duplicate_line_ratio = 0.0
    if texts:
        duplicate_line_ratio = (len(texts) - len(set(texts))) / len(texts)
    leak_count = 0
    for p in paragraphs:
        text = p.content or ""
        if PAGE_MARKER_RE.search(text):
            leak_count += 1
            continue
        if "©" in text and not p.is_junk and not _is_copyright_footer_artifact(p, len(paragraphs)):
            leak_count += 1
    header_footer_leakage_score = leak_count / max(len(paragraphs), 1)

    def _looks_heading(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if stripped.endswith(":"):
            return True
        words = stripped.split()
        if len(words) > 14:
            return False
        letters = [ch for ch in stripped if ch.isalpha()]
        if not letters:
            return False
        upper_count = sum(1 for ch in letters if ch.isupper())
        return upper_count / len(letters) >= 0.35

    heading_like = sum(1 for p in paragraphs if p.content and _looks_heading(p.content))
    structure_density_score = heading_like / max(len(paragraphs), 1)
    parsed_paths: list[list[int]] = []
    for para in paragraphs:
        if not para.hierarchy_path:
            continue
        parts = [int(part) for part in para.hierarchy_path.split(".") if part.isdigit()]
        if parts:
            parsed_paths.append(parts)
    abrupt_depth_jumps = 0
    orphan_nodes = 0
    unique_paths = {tuple(parts) for parts in parsed_paths}
    for current in unique_paths:
        if len(current) > 1 and current[:-1] not in unique_paths:
            orphan_nodes += 1
    for prev, nxt in zip(parsed_paths, parsed_paths[1:]):
        if abs(len(prev) - len(nxt)) > 1:
            abrupt_depth_jumps += 1
    hierarchy_penalty = (
        (orphan_nodes / max(len(unique_paths), 1))
        + (abrupt_depth_jumps / max(len(parsed_paths), 1))
    )
    hierarchy_continuity_score = max(0.0, 1.0 - hierarchy_penalty)
    thresholds = _profile_thresholds(profile)
    total_words = sum(p.word_count for p in paragraphs)
    content_sufficiency_score = min(1.0, total_words / max(float(thresholds["min_words"]), 1.0))
    markdown_leaks = 0
    for para in paragraphs:
        text = (para.content or "").strip()
        if not text:
            continue
        if MARKDOWN_LEAK_RE.search(text):
            markdown_leaks += 1
    markdown_leakage_score = markdown_leaks / max(len(paragraphs), 1)

    node_parent_error_ratio = 0.0
    list_coherence_penalty = 0.0
    table_normalization_coverage = 1.0
    front_matter_ratio = 0.0
    if nodes:
        node_map = {node.id: node for node in nodes}
        bad_parents = 0
        bad_list_items = 0
        table_nodes = [node for node in nodes if node.node_type == "table"]
        bad_tables = 0
        front_nodes = 0
        for node in nodes:
            if node.parent_node_id is not None:
                parent = node_map.get(node.parent_node_id)
                if parent is None or parent.depth >= node.depth:
                    bad_parents += 1
            if node.node_type == "list_item":
                parent = node_map.get(node.parent_node_id) if node.parent_node_id is not None else None
                if parent is None or parent.node_type != "list":
                    bad_list_items += 1
            flags = node.quality_flags or {}
            if isinstance(flags, dict) and flags.get("is_front_matter"):
                front_nodes += 1
        for node in table_nodes:
            if not (node.text_html or "").strip():
                bad_tables += 1
        node_parent_error_ratio = bad_parents / max(len(nodes), 1)
        list_coherence_penalty = bad_list_items / max(len([n for n in nodes if n.node_type == "list_item"]), 1)
        table_normalization_coverage = 1.0 - (bad_tables / max(len(table_nodes), 1)) if table_nodes else 1.0
        front_matter_ratio = front_nodes / max(len(nodes), 1)

    return {
        "duplicate_line_ratio": round(duplicate_line_ratio, 4),
        "header_footer_leakage_score": round(header_footer_leakage_score, 4),
        "structure_density_score": round(structure_density_score, 4),
        "content_sufficiency_score": round(content_sufficiency_score, 4),
        "hierarchy_continuity_score": round(hierarchy_continuity_score, 4),
        "markdown_leakage_score": round(markdown_leakage_score, 4),
        "node_parent_error_ratio": round(node_parent_error_ratio, 4),
        "list_coherence_penalty": round(list_coherence_penalty, 4),
        "table_normalization_coverage": round(table_normalization_coverage, 4),
        "front_matter_ratio": round(front_matter_ratio, 4),
        "orphan_subsection_nodes": orphan_nodes,
        "abrupt_depth_jumps": abrupt_depth_jumps,
    }


def build_scored_reasons(paragraphs: List[TranscriptParagraph], profile: str, scores: dict) -> list[dict]:
    reasons: list[dict] = []
    thresholds = _profile_thresholds(profile)
    if scores["duplicate_line_ratio"] > float(thresholds["dup_warn"]):
        reasons.append(
            {"code": "duplicate_line_ratio_high", "score": scores["duplicate_line_ratio"], "threshold": thresholds["dup_warn"]}
        )
    if scores["header_footer_leakage_score"] > float(thresholds["leak_warn"]):
        reasons.append(
            {
                "code": "header_footer_leakage_high",
                "score": scores["header_footer_leakage_score"],
                "threshold": thresholds["leak_warn"],
            }
        )
    if float(scores.get("markdown_leakage_score", 0.0)) > 0.05:
        reasons.append(
            {
                "code": "markdown_leakage_high",
                "score": scores.get("markdown_leakage_score", 0.0),
                "threshold": 0.05,
            }
        )
    if float(scores.get("hierarchy_continuity_score", 1.0)) < 0.7:
        reasons.append(
            {
                "code": "hierarchy_continuity_low",
                "score": scores.get("hierarchy_continuity_score", 1.0),
                "threshold": 0.7,
            }
        )
    if float(scores.get("node_parent_error_ratio", 0.0)) > 0.0:
        reasons.append(
            {
                "code": "node_parent_integrity_error",
                "score": scores.get("node_parent_error_ratio", 0.0),
                "threshold": 0.0,
            }
        )
    if float(scores.get("list_coherence_penalty", 0.0)) > 0.1:
        reasons.append(
            {
                "code": "list_coherence_low",
                "score": scores.get("list_coherence_penalty", 0.0),
                "threshold": 0.1,
            }
        )
    if float(scores.get("table_normalization_coverage", 1.0)) < 0.9:
        reasons.append(
            {
                "code": "table_normalization_low",
                "score": scores.get("table_normalization_coverage", 1.0),
                "threshold": 0.9,
            }
        )
    if float(scores.get("front_matter_ratio", 0.0)) > 0.6:
        reasons.append(
            {
                "code": "front_matter_heavy",
                "score": scores.get("front_matter_ratio", 0.0),
                "threshold": 0.6,
            }
        )
    if int(scores.get("orphan_subsection_nodes", 0)) > 0:
        reasons.append(
            {
                "code": "orphan_subsection_nodes",
                "score": int(scores.get("orphan_subsection_nodes", 0)),
                "threshold": 0,
            }
        )
    if int(scores.get("abrupt_depth_jumps", 0)) > max(2, len(paragraphs) // 8):
        reasons.append(
            {
                "code": "abrupt_depth_jumps",
                "score": int(scores.get("abrupt_depth_jumps", 0)),
                "threshold": max(2, len(paragraphs) // 8),
            }
        )
    if len(paragraphs) < int(thresholds["min_blocks"]):
        reasons.append(
            {"code": "insufficient_blocks", "score": len(paragraphs), "threshold": int(thresholds["min_blocks"])}
        )
    total_words = sum(p.word_count for p in paragraphs)
    if total_words < int(thresholds["min_words"]):
        reasons.append(
            {"code": "insufficient_words", "score": total_words, "threshold": int(thresholds["min_words"])}
        )
    return reasons


def _build_check_failure_reasons(checks: dict[str, tuple[bool, str]]) -> list[dict]:
    code_map = {
        "reading_flow": "reading_flow_failed",
        "minimum_content": "minimum_content_failed",
        "hierarchy_continuity": "hierarchy_continuity_failed",
        "paragraph_length": "paragraph_length_failed",
        "sequential_indexes": "sequential_indexes_failed",
        "markdown_leakage": "markdown_leakage_failed",
        "node_parent_integrity": "node_parent_integrity_failed",
        "heading_list_coherence": "heading_list_coherence_failed",
        "table_normalization": "table_normalization_failed",
    }
    reasons: list[dict] = []
    for check_name, result in checks.items():
        passed, message = result
        if passed:
            continue
        reasons.append({"code": code_map.get(check_name, f"{check_name}_failed"), "message": message})
    return reasons


def _transcript_metadata_reasons(lesson: TranscriptLesson, session: Session) -> list[dict]:
    source_tag = session.execute(
        select(FileTag.name)
        .join(SourceFile, SourceFile.file_tag_id == FileTag.id)
        .where(SourceFile.id == lesson.source_file_id)
    ).scalar_one_or_none()
    if source_tag != "transcripts":
        return []

    reasons: list[dict] = []
    chapter = None
    if lesson.chapter_id is not None:
        chapter = session.execute(
            select(TranscriptChapter).where(TranscriptChapter.id == lesson.chapter_id)
        ).scalar_one_or_none()

    if lesson.date is None:
        reasons.append({"code": "missing_parsed_date", "score": 1, "threshold": 0})
    if not (lesson.title or "").strip():
        reasons.append({"code": "missing_lesson_marker_fallback", "score": 1, "threshold": 0})
    return reasons


def validate_lesson_structure(lesson_id: int, session: Session) -> dict:
    setup_logging()
    log = logger.bind(function="validate_lesson_structure", lesson_id=lesson_id)

    lesson = session.execute(select(TranscriptLesson).where(TranscriptLesson.id == lesson_id)).scalar_one_or_none()
    if not lesson:
        return {"status": "failed", "checks": {}, "message": "Lesson not found"}
    profile = _profile_for_lesson(lesson, session)
    source_format = _source_format_for_lesson(lesson, session)
    if source_format in {"htm", "html"} and profile == "default":
        # Preserve current behavior for non-profile HTML lessons.
        profile = "default"

    paragraphs = (
        session.execute(
            select(TranscriptParagraph)
            .where(TranscriptParagraph.lesson_id == lesson_id)
            .order_by(TranscriptParagraph.paragraph_index)
        )
        .scalars()
        .all()
    )
    structured_nodes: list[Any] = []
    node_backed = False
    config = get_config()
    if config.course_notes_node_mode and is_course_note_node_lesson(lesson, session):
        structured_nodes = load_course_note_nodes(session, lesson_id)
        node_backed = True
    elif is_dean_node_lesson(lesson, session) and config.dean_topical_node_mode:
        structured_nodes = load_dean_structured_nodes(session, lesson_id)
        node_backed = True

    checks = {
        "sequential_indexes": check_sequential_indexes(paragraphs),
        "hierarchy_continuity": check_hierarchy_continuity(paragraphs, profile=profile),
        "minimum_content": check_minimum_content(paragraphs, profile=profile),
        "paragraph_length": check_paragraph_length_bounds(paragraphs),
        "reading_flow": check_reading_flow(paragraphs, profile=profile),
    }
    if node_backed:
        checks["markdown_leakage"] = check_markdown_leakage(paragraphs)
        checks["node_parent_integrity"] = check_node_parent_integrity(structured_nodes)
        checks["heading_list_coherence"] = check_heading_list_coherence(structured_nodes)
        checks["table_normalization"] = check_table_normalization(structured_nodes)

    passed = sum(1 for val in checks.values() if val[0])
    total = len(checks)
    scores = compute_validation_scores(paragraphs, profile=profile, nodes=structured_nodes or None)
    reasons = build_scored_reasons(paragraphs, profile=profile, scores=scores)
    explicit_failures = _build_check_failure_reasons(checks)
    seen_codes = {str(item.get("code")) for item in reasons}
    reasons.extend(item for item in explicit_failures if str(item.get("code")) not in seen_codes)
    reasons.extend(_transcript_metadata_reasons(lesson, session))
    shadow_review = run_shadow_semantic_review([p.content for p in paragraphs if p.content], profile=profile)
    validation_report = {
        "validation_profile_used": profile,
        "checks_passed": passed,
        "checks_total": total,
        "scores": scores,
        "reasons": reasons,
        "llm_semantic_review": shadow_review,
        "details": {k: {"passed": v[0], "message": v[1]} for k, v in checks.items()},
    }

    if passed == total:
        lesson.status = "validated"
        lesson.validation_report = validation_report
        session.commit()
        return {"status": "validated", "checks": validation_report["details"], "message": "All validation checks passed"}

    lesson.status = "failed"
    lesson.validation_report = validation_report
    session.commit()
    log.error("validation_failed", checks=validation_report)
    return {"status": "failed", "checks": validation_report["details"], "message": "Validation checks failed"}
