"""Compare baseline and candidate eval_suite summaries with hard and soft gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _pct_increase(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    if baseline <= 0:
        return None
    return (candidate - baseline) / baseline


def _extract(summary: dict[str, Any]) -> dict[str, float | None]:
    hard = summary.get("hard_signals", {}) if isinstance(summary.get("hard_signals"), dict) else {}
    gold = summary.get("gold_metrics", {}) if isinstance(summary.get("gold_metrics"), dict) else {}
    silver = summary.get("silver_metrics", {}) if isinstance(summary.get("silver_metrics"), dict) else {}
    latency = summary.get("latency_metrics", {}) if isinstance(summary.get("latency_metrics"), dict) else {}

    return {
        "contract_failures": _num(hard.get("contract_failures")),
        "error_rate": _num(hard.get("error_rate")),
        "stream_incomplete_rate": _num(hard.get("stream_incomplete_rate")),
        "telemetry_join_rate": _num(hard.get("telemetry_join_rate")),
        "citation_coverage_avg": _num(silver.get("answer_sentence_citation_coverage_avg")),
        "uncited_sentence_rate_avg": _num(silver.get("uncited_sentence_rate_avg")),
        "anchor_recall_avg": _num(gold.get("anchor_recall_at_k_avg")),
        "refusal_f1": _num(gold.get("refusal_f1")),
        "query_request_ms_p95": _num(latency.get("query_request_ms_p95")),
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    base = _extract(baseline)
    cand = _extract(candidate)

    hard_failures: list[str] = []
    soft_warnings: list[str] = []

    contract_failures = cand.get("contract_failures")
    if contract_failures is None:
        hard_failures.append("candidate contract_failures metric missing")
    elif contract_failures > 0:
        hard_failures.append(f"contract_failures={contract_failures} (must be 0)")

    base_error = base.get("error_rate")
    cand_error = cand.get("error_rate")
    error_cap = None
    if cand_error is None:
        hard_failures.append("candidate error_rate metric missing")
    else:
        reference = base_error if base_error is not None else 0.0
        error_cap = max(reference + 0.01, 0.02)
        if cand_error > error_cap:
            hard_failures.append(f"error_rate={cand_error:.4f} exceeds cap={error_cap:.4f}")

    base_incomplete = base.get("stream_incomplete_rate")
    cand_incomplete = cand.get("stream_incomplete_rate")
    incomplete_cap = None
    if cand_incomplete is None:
        hard_failures.append("candidate stream_incomplete_rate metric missing")
    else:
        reference = base_incomplete if base_incomplete is not None else 0.0
        incomplete_cap = max(reference + 0.01, 0.01)
        if cand_incomplete > incomplete_cap:
            hard_failures.append(
                f"stream_incomplete_rate={cand_incomplete:.4f} exceeds cap={incomplete_cap:.4f}"
            )

    join_rate = cand.get("telemetry_join_rate")
    if join_rate is None:
        hard_failures.append("candidate telemetry_join_rate metric missing")
    elif join_rate < 0.99:
        hard_failures.append(f"telemetry_join_rate={join_rate:.4f} is below 0.99")

    cov_drop = _delta(cand.get("citation_coverage_avg"), base.get("citation_coverage_avg"))
    if cov_drop is not None and cov_drop < -0.05:
        soft_warnings.append(f"citation coverage dropped by {abs(cov_drop):.4f} (> 0.05)")

    uncited_rise = _delta(cand.get("uncited_sentence_rate_avg"), base.get("uncited_sentence_rate_avg"))
    if uncited_rise is not None and uncited_rise > 0.05:
        soft_warnings.append(f"uncited sentence rate increased by {uncited_rise:.4f} (> 0.05)")

    anchor_drop = _delta(cand.get("anchor_recall_avg"), base.get("anchor_recall_avg"))
    if anchor_drop is not None and anchor_drop < -0.03:
        soft_warnings.append(f"anchor recall dropped by {abs(anchor_drop):.4f} (> 0.03)")

    refusal_drop = _delta(cand.get("refusal_f1"), base.get("refusal_f1"))
    if refusal_drop is not None and refusal_drop < -0.03:
        soft_warnings.append(f"refusal F1 dropped by {abs(refusal_drop):.4f} (> 0.03)")

    p95_increase = _pct_increase(cand.get("query_request_ms_p95"), base.get("query_request_ms_p95"))
    if p95_increase is not None and p95_increase > 0.20:
        soft_warnings.append(f"query p95 latency increased by {p95_increase * 100:.2f}% (> 20%)")

    return {
        "hard_pass": len(hard_failures) == 0,
        "hard_failures": hard_failures,
        "soft_warnings": soft_warnings,
        "baseline_metrics": base,
        "candidate_metrics": cand,
        "thresholds": {
            "error_rate_cap": error_cap,
            "stream_incomplete_rate_cap": incomplete_cap,
            "telemetry_join_rate_min": 0.99,
            "soft_max_coverage_drop": 0.05,
            "soft_max_uncited_rate_increase": 0.05,
            "soft_max_anchor_recall_drop": 0.03,
            "soft_max_refusal_f1_drop": 0.03,
            "soft_max_query_p95_increase": 0.20,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare baseline vs candidate eval summaries.")
    parser.add_argument("--baseline", required=True, help="Path to baseline summary.json")
    parser.add_argument("--candidate", required=True, help="Path to candidate summary.json")
    parser.add_argument("--output", required=True, help="Path to output verdict JSON")
    parser.add_argument(
        "--fail-on-hard",
        action="store_true",
        default=False,
        help="Exit non-zero when hard gates fail.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    output_path = Path(args.output)

    baseline = _load_json(baseline_path)
    candidate = _load_json(candidate_path)
    verdict = compare(baseline, candidate)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")

    print(f"Wrote verdict: {output_path}")
    print(f"Hard pass: {verdict['hard_pass']}")
    if verdict["hard_failures"]:
        for item in verdict["hard_failures"]:
            print(f"HARD FAIL: {item}")
    if verdict["soft_warnings"]:
        for item in verdict["soft_warnings"]:
            print(f"SOFT WARN: {item}")

    if args.fail_on_hard and not verdict["hard_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
