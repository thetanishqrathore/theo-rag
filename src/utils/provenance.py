"""Build provenance: what code, configuration and tools produced the corpus."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.utils.config import get_config

REPO_ROOT = Path(__file__).resolve().parents[2]

# Settings that change extracted text, structure, validation, chunks or vectors.
# Retrieval/generation knobs are excluded: they do not invalidate built data.
_PROCESSING_PREFIXES = (
    "transcript_",
    "course_notes_",
    "clough_course_notes_",
    "dean_",
    "validation_",
    "rag_chunk_",
    "rag_parent_",
    "rag_child_",
    "embedding_",
)
_PROCESSING_KEYS = {"qdrant_expected_dim", "raw_data_path", "canonical_path"}
_SECRET_MARKERS = ("password", "secret", "token", "database_url")


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(REPO_ROOT), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def processing_config() -> dict[str, Any]:
    """Effective processing configuration (secrets never included)."""
    values = get_config().model_dump()
    return {
        key: value
        for key, value in sorted(values.items())
        if (key.startswith(_PROCESSING_PREFIXES) or key in _PROCESSING_KEYS)
        and not any(marker in key for marker in _SECRET_MARKERS)
    }


def processing_config_digest() -> str:
    payload = json.dumps(processing_config(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


@lru_cache(maxsize=1)
def tool_identities() -> dict[str, str]:
    config = get_config()
    marker = shutil.which(config.dean_pdf_marker_command) or config.dean_pdf_marker_command
    return {
        "docling": _version("docling"),
        "pymupdf": _version("pymupdf"),
        "sentence_transformers": _version("sentence-transformers"),
        "torch": _version("torch"),
        "embedding_model": config.embedding_model,
        "soffice": shutil.which(config.dean_doc_soffice_command) or "missing",
        "marker": marker,
    }


@lru_cache(maxsize=1)
def code_identity() -> dict[str, Any]:
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "git_sha": _git("rev-parse", "HEAD"),
        "git_dirty": bool(dirty) if dirty != "unknown" else None,
    }


def stamp() -> dict[str, Any]:
    """Compact provenance stored with each validation report."""
    return {**code_identity(), "processing_config_digest": processing_config_digest()}


def full_record() -> dict[str, Any]:
    """Complete provenance for a build run summary."""
    return {
        **code_identity(),
        "processing_config_digest": processing_config_digest(),
        "processing_config": processing_config(),
        "tools": tool_identities(),
    }
