"""Central corpus registry and typed corpus identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Tuple

CorpusId = Literal["clough", "dean"]


@dataclass(frozen=True)
class CorpusConfig:
    """Definition of a supported corpus."""

    corpus_id: CorpusId
    display_name: str
    description: str


CORPUS_REGISTRY: Dict[CorpusId, CorpusConfig] = {
    "clough": CorpusConfig(
        corpus_id="clough",
        display_name="Clough",
        description="Charlie Clough corpus",
    ),
    "dean": CorpusConfig(
        corpus_id="dean",
        display_name="Dean",
        description="Pastor Dean corpus",
    ),
}

CORPUS_IDS: Tuple[CorpusId, ...] = tuple(CORPUS_REGISTRY.keys())


def is_valid_corpus(corpus: str) -> bool:
    """Return true when the provided corpus is supported."""
    return corpus in CORPUS_REGISTRY
