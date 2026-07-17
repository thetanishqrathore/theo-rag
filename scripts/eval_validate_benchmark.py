"""Validate benchmark dataset shape and expected distribution for eval v1."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXPECTED_TOTAL = 200
EXPECTED_CORPUS_COUNTS = {"clough": 100, "dean": 100}
EXPECTED_TIER_COUNTS = {"gold": 90, "silver": 110}
EXPECTED_CLASS_COUNTS = {
    "definition": 40,
    "synthesis": 40,
    "comparison": 30,
    "multi_turn": 20,
    "ambiguity": 20,
    "refusal": 30,
    "citation_hard": 20,
}
ALLOWED_EXPECTED_MODE = {"confident", "narrow", "refuse"}
ALLOWED_HIERARCHY_POLICY = {"optional", "expected", "not_applicable"}
ALLOWED_RETRIEVAL_MODE = {"fast", "deep"}
ALLOWED_CORPUS = set(EXPECTED_CORPUS_COUNTS.keys())
ALLOWED_TIERS = set(EXPECTED_TIER_COUNTS.keys())


@dataclass
class ValidationIssue:
    code: str
    message: str
    case_id: str | None = None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at line {line_no}: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected JSON object at line {line_no}.")
            rows.append(parsed)
    return rows


def _safe_case_id(row: dict[str, Any]) -> str:
    value = row.get("id")
    return str(value) if isinstance(value, str) and value else "<missing-id>"


def _validate_case(row: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    case_id = _safe_case_id(row)

    def require_str(key: str, min_len: int = 1, max_len: int | None = None) -> None:
        value = row.get(key)
        if not isinstance(value, str):
            issues.append(ValidationIssue("type", f"`{key}` must be a string.", case_id))
            return
        text = value.strip()
        if len(text) < min_len:
            issues.append(ValidationIssue("value", f"`{key}` is too short.", case_id))
        if max_len is not None and len(text) > max_len:
            issues.append(ValidationIssue("value", f"`{key}` is too long.", case_id))

    require_str("id", min_len=8, max_len=32)
    require_str("corpus", min_len=4, max_len=10)
    require_str("query", min_len=8, max_len=500)
    require_str("label_tier", min_len=4, max_len=10)
    require_str("expected_mode", min_len=6, max_len=16)
    require_str("hierarchy_policy", min_len=8, max_len=16)
    require_str("retrieval_mode", min_len=4, max_len=8)

    corpus = row.get("corpus")
    if corpus not in ALLOWED_CORPUS:
        issues.append(ValidationIssue("value", f"Invalid corpus `{corpus}`.", case_id))

    tier = row.get("label_tier")
    if tier not in ALLOWED_TIERS:
        issues.append(ValidationIssue("value", f"Invalid label_tier `{tier}`.", case_id))

    mode = row.get("expected_mode")
    if mode not in ALLOWED_EXPECTED_MODE:
        issues.append(ValidationIssue("value", f"Invalid expected_mode `{mode}`.", case_id))

    retrieval_mode = row.get("retrieval_mode")
    if retrieval_mode not in ALLOWED_RETRIEVAL_MODE:
        issues.append(ValidationIssue("value", f"Invalid retrieval_mode `{retrieval_mode}`.", case_id))

    hierarchy_policy = row.get("hierarchy_policy")
    if hierarchy_policy not in ALLOWED_HIERARCHY_POLICY:
        issues.append(ValidationIssue("value", f"Invalid hierarchy_policy `{hierarchy_policy}`.", case_id))

    top_k = row.get("top_k")
    if not isinstance(top_k, int) or not (1 <= top_k <= 20):
        issues.append(ValidationIssue("value", "`top_k` must be an int in [1, 20].", case_id))

    must_refuse = row.get("must_refuse")
    if not isinstance(must_refuse, bool):
        issues.append(ValidationIssue("type", "`must_refuse` must be a boolean.", case_id))

    min_citations = row.get("min_citations")
    if not isinstance(min_citations, int) or min_citations < 0:
        issues.append(ValidationIssue("value", "`min_citations` must be a non-negative integer.", case_id))

    class_tags = row.get("class_tags")
    if not isinstance(class_tags, list) or not class_tags:
        issues.append(ValidationIssue("type", "`class_tags` must be a non-empty list.", case_id))
    elif not all(isinstance(item, str) and item.strip() for item in class_tags):
        issues.append(ValidationIssue("type", "`class_tags` values must be non-empty strings.", case_id))

    history = row.get("history")
    if not isinstance(history, list):
        issues.append(ValidationIssue("type", "`history` must be a list.", case_id))
    else:
        for turn in history:
            if not isinstance(turn, dict):
                issues.append(ValidationIssue("type", "history item must be an object.", case_id))
                continue
            role = turn.get("role")
            content = turn.get("content")
            if role not in {"user", "assistant"}:
                issues.append(ValidationIssue("value", "history role must be user|assistant.", case_id))
            if not isinstance(content, str) or not content.strip():
                issues.append(ValidationIssue("value", "history content must be non-empty.", case_id))

    expected_source_tags = row.get("expected_source_tags")
    if not isinstance(expected_source_tags, list) or not expected_source_tags:
        issues.append(ValidationIssue("type", "`expected_source_tags` must be non-empty list.", case_id))
    elif not all(isinstance(item, str) and item.strip() for item in expected_source_tags):
        issues.append(ValidationIssue("type", "expected_source_tags values must be non-empty strings.", case_id))

    gold_anchors = row.get("gold_anchors")
    if not isinstance(gold_anchors, list):
        issues.append(ValidationIssue("type", "`gold_anchors` must be a list.", case_id))
    else:
        for anchor in gold_anchors:
            if not isinstance(anchor, dict):
                issues.append(ValidationIssue("type", "gold anchor must be an object.", case_id))
                continue
            if anchor.get("kind") != "contains_any":
                issues.append(ValidationIssue("value", "gold anchor kind must be contains_any.", case_id))
            terms = anchor.get("terms")
            if not isinstance(terms, list) or not terms or not all(isinstance(term, str) and term.strip() for term in terms):
                issues.append(ValidationIssue("value", "gold anchor terms must be non-empty string list.", case_id))

    silver_rules = row.get("silver_rules")
    if not isinstance(silver_rules, dict):
        issues.append(ValidationIssue("type", "`silver_rules` must be an object.", case_id))
    else:
        for bool_key in ("require_chunk_citation", "allow_no_hierarchy", "require_final_event"):
            if not isinstance(silver_rules.get(bool_key), bool):
                issues.append(ValidationIssue("type", f"`silver_rules.{bool_key}` must be a boolean.", case_id))
        for rate_key in ("min_sentence_citation_coverage", "max_uncited_sentence_rate"):
            value = silver_rules.get(rate_key)
            if not isinstance(value, (int, float)) or not (0.0 <= float(value) <= 1.0):
                issues.append(ValidationIssue("value", f"`silver_rules.{rate_key}` must be in [0,1].", case_id))

    if must_refuse is True:
        if mode != "refuse":
            issues.append(ValidationIssue("cross_field", "must_refuse=true requires expected_mode=refuse.", case_id))
        if isinstance(min_citations, int) and min_citations != 0:
            issues.append(ValidationIssue("cross_field", "must_refuse=true requires min_citations=0.", case_id))

    if tier == "gold" and isinstance(gold_anchors, list) and len(gold_anchors) == 0:
        issues.append(ValidationIssue("cross_field", "gold case must include at least one gold_anchors entry.", case_id))
    if tier == "silver" and isinstance(gold_anchors, list) and len(gold_anchors) > 0:
        issues.append(ValidationIssue("cross_field", "silver case must not include gold_anchors.", case_id))

    return issues


def _find_primary_class(class_tags: list[str]) -> str | None:
    for tag in class_tags:
        if tag in EXPECTED_CLASS_COUNTS:
            return tag
    return None


def validate_rows(rows: list[dict[str, Any]]) -> tuple[list[ValidationIssue], dict[str, Any]]:
    issues: list[ValidationIssue] = []
    id_counter: Counter[str] = Counter()
    corpus_counter: Counter[str] = Counter()
    tier_counter: Counter[str] = Counter()
    class_counter: Counter[str] = Counter()
    class_by_corpus: dict[str, Counter[str]] = defaultdict(Counter)
    history_counter = 0

    for row in rows:
        issues.extend(_validate_case(row))
        case_id = _safe_case_id(row)
        id_counter[case_id] += 1
        if isinstance(row.get("history"), list) and row["history"]:
            history_counter += 1

        corpus = row.get("corpus")
        tier = row.get("label_tier")
        if isinstance(corpus, str):
            corpus_counter[corpus] += 1
        if isinstance(tier, str):
            tier_counter[tier] += 1

        class_tags = row.get("class_tags")
        if isinstance(class_tags, list):
            cls = _find_primary_class([tag for tag in class_tags if isinstance(tag, str)])
            if cls:
                class_counter[cls] += 1
                if isinstance(corpus, str):
                    class_by_corpus[corpus][cls] += 1
            else:
                issues.append(ValidationIssue("value", "No recognized benchmark class tag found.", case_id))

    duplicates = [case_id for case_id, count in id_counter.items() if count > 1]
    for case_id in duplicates:
        issues.append(ValidationIssue("duplicate", f"Duplicate case id `{case_id}`.", case_id))

    if len(rows) != EXPECTED_TOTAL:
        issues.append(ValidationIssue("distribution", f"Expected {EXPECTED_TOTAL} rows, found {len(rows)}."))

    for corpus, expected in EXPECTED_CORPUS_COUNTS.items():
        found = corpus_counter.get(corpus, 0)
        if found != expected:
            issues.append(ValidationIssue("distribution", f"Expected {expected} rows for corpus={corpus}, found {found}."))

    for tier, expected in EXPECTED_TIER_COUNTS.items():
        found = tier_counter.get(tier, 0)
        if found != expected:
            issues.append(ValidationIssue("distribution", f"Expected {expected} rows for label_tier={tier}, found {found}."))

    for cls, expected in EXPECTED_CLASS_COUNTS.items():
        found = class_counter.get(cls, 0)
        if found != expected:
            issues.append(ValidationIssue("distribution", f"Expected {expected} rows for class={cls}, found {found}."))

    stats: dict[str, Any] = {
        "total_cases": len(rows),
        "corpus_counts": dict(sorted(corpus_counter.items())),
        "tier_counts": dict(sorted(tier_counter.items())),
        "class_counts": dict(sorted(class_counter.items())),
        "class_counts_by_corpus": {corpus: dict(sorted(counter.items())) for corpus, counter in sorted(class_by_corpus.items())},
        "cases_with_history": history_counter,
        "duplicate_id_count": len(duplicates),
        "issue_count": len(issues),
        "valid": len(issues) == 0,
    }
    return issues, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate eval benchmark v1 dataset.")
    parser.add_argument(
        "--input",
        default="eval/benchmark/v1_cases.jsonl",
        help="Path to benchmark JSONL.",
    )
    parser.add_argument(
        "--schema",
        default="eval/benchmark/v1_schema.json",
        help="Path to schema reference (presence check only; validation is implemented in this script).",
    )
    parser.add_argument(
        "--stats-output",
        default="eval/reports/benchmark_v1_stats.json",
        help="Path to write validation stats JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    schema_path = Path(args.schema)
    stats_output_path = Path(args.stats_output)

    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    if not schema_path.exists():
        raise SystemExit(f"Schema file not found: {schema_path}")

    rows = _load_jsonl(input_path)
    issues, stats = validate_rows(rows)

    stats_output_path.parent.mkdir(parents=True, exist_ok=True)
    stats_output_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    if issues:
        print(f"Validation failed with {len(issues)} issue(s).")
        for issue in issues[:50]:
            prefix = f"[{issue.code}]"
            if issue.case_id:
                print(f"{prefix} {issue.case_id}: {issue.message}")
            else:
                print(f"{prefix} {issue.message}")
        if len(issues) > 50:
            print(f"... {len(issues) - 50} more issues.")
        raise SystemExit(1)

    print(f"Validation passed for {len(rows)} benchmark cases.")
    print(f"Stats written to: {stats_output_path}")


if __name__ == "__main__":
    main()
