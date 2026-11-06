"""Keyword (BM25-style) query construction: informative terms only, ranked by rarity."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retrieval.retrieve import _weighted_keyword_sql, select_keyword_terms  # noqa: E402

TOTAL = 73_504
DOC_FREQ = {
    "pastor": 2145,
    "dean": 193,
    "evalu": 900,
    "cyril": 27,
    "anathema": 27,
    "nestorius": 28,
    "roman": 5940,
    "christ": 16555,
    "twelv": 1200,
}


def test_drops_teacher_names_meta_words_and_common_terms() -> None:
    lexemes = ["pastor", "dean", "evalu", "cyril", "anathema", "nestorius", "roman", "christ", "twelv"]
    terms = select_keyword_terms(lexemes, TOTAL, DOC_FREQ, max_ratio=0.05, max_terms=8)
    assert [term for term, _ in terms] == ["anathema", "cyril", "nestorius", "twelv"]


def test_rarer_terms_weigh_more() -> None:
    terms = dict(select_keyword_terms(["cyril", "twelv"], TOTAL, DOC_FREQ, max_ratio=0.05, max_terms=8))
    assert terms["cyril"] > terms["twelv"] > 0


def test_keeps_the_two_rarest_when_every_term_is_common() -> None:
    terms = select_keyword_terms(["christ", "roman", "pastor"], TOTAL, DOC_FREQ, max_ratio=0.01, max_terms=8)
    # "pastor" is a meta word, so the two rarest content terms remain.
    assert [term for term, _ in terms] == ["roman", "christ"]


def test_ignores_unknown_and_non_alphanumeric_lexemes_and_caps_count() -> None:
    lexemes = ["cyril", "unknownword", "x'); drop table lessons; --", "anathema", "nestorius"]
    terms = select_keyword_terms(lexemes, TOTAL, DOC_FREQ, max_ratio=0.05, max_terms=2)
    assert [term for term, _ in terms] == ["anathema", "cyril"]


def test_keyword_sql_binds_terms_and_weights_as_parameters() -> None:
    sql = str(_weighted_keyword_sql(3))
    for name in (":t0", ":t1", ":t2", ":w0", ":w2", ":corpus_id", ":limit"):
        assert name in sql
    # The GIN expression index is only used if the expression matches it exactly.
    assert sql.count("to_tsvector('english', content) @@ to_tsquery('simple'") == 3
