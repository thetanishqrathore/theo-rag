"""Backward-compatible wrappers for the legacy topical node module."""

from __future__ import annotations

from src.processing.structured_nodes import (
    StructuredNodeInput,
    StructuredTableInput,
    derive_paragraph_rows_from_nodes,
    is_dean_node_lesson,
    is_topical_node_lesson,
    is_topical_pdf_lesson,
    load_dean_structured_nodes,
    load_topical_nodes,
    persist_dean_structured_doc,
    persist_dean_structured_nodes,
)

# Legacy type aliases kept for imports in existing codepaths.
TopicalNodeInput = StructuredNodeInput
TopicalTableInput = StructuredTableInput


def persist_topical_doc(*args, **kwargs):
    return persist_dean_structured_doc(*args, **kwargs)


def persist_topical_nodes(*args, **kwargs):
    return persist_dean_structured_nodes(*args, **kwargs)

