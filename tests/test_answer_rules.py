"""Answer rules: partial answers instead of flat refusals; quotes only from the sources."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.generation.generate import (  # noqa: E402
    REFUSAL_TEXT,
    _legacy_silence_rule,
    _partial_answer_rules,
    verify_quotations,
)

SOURCES = {
    "c1": {
        "parent_text": "In Roman culture they were emphasized by Lucretius and Horace who popularized "
        "the Epicurean philosophy. The Epicureans were the atheist‑materialists of the day.",
        "child_text": "",
    },
    "__order__": ["c1"],
}


def test_verbatim_quote_is_kept_despite_case_hyphen_and_punctuation() -> None:
    answer = "Dean says “the epicureans were the atheist-materialists of the day” [chunk:c1]."
    text, stats = verify_quotations(answer, SOURCES)
    assert text == answer
    assert stats == {"quotes_checked": 1, "quotes_verbatim": 1, "quotes_unwrapped": 0}


def test_paraphrase_in_quotation_marks_is_unwrapped() -> None:
    answer = 'He critiques "the atomic swerve that creates chance rather than divine order" [chunk:c1].'
    text, stats = verify_quotations(answer, SOURCES)
    assert text == "He critiques the atomic swerve that creates chance rather than divine order [chunk:c1]."
    assert stats["quotes_unwrapped"] == 1


def test_elided_quote_needs_every_part_in_the_sources() -> None:
    kept = "“emphasized by Lucretius and Horace … the Epicurean philosophy”"
    dropped = "“emphasized by Lucretius and Horace … the clinamen of the atoms”"
    assert verify_quotations(kept, SOURCES)[1]["quotes_verbatim"] == 1
    assert verify_quotations(dropped, SOURCES)[1]["quotes_unwrapped"] == 1


def test_short_quoted_terms_are_left_alone() -> None:
    answer = "The so-called “swerve” and “unknown god”."
    text, stats = verify_quotations(answer, SOURCES)
    assert text == answer
    assert stats["quotes_checked"] == 0


def test_partial_answer_rules_keep_refusal_for_true_silence() -> None:
    rules = _partial_answer_rules("Pastor Dean")
    assert "do NOT refuse" in rules
    assert REFUSAL_TEXT in rules
    assert "Never attribute to Pastor Dean" in rules
    assert REFUSAL_TEXT in _legacy_silence_rule()
