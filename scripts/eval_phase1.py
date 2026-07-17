"""Lightweight evaluation harness for Phase 1 non-regression checks."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx


@dataclass
class QueryEvalResult:
    query_id: str
    corpus: str
    query: str
    ok: bool
    status_code: int | None
    error: str | None
    refusal: bool | None
    grounded: bool | None
    citation_count: int | None
    answer_sentence_citation_coverage: float | None
    uncited_sentence_rate: float | None
    hierarchy_metadata_presence_rate: float | None
    retrieval_time_ms: float | None
    generation_time_ms: float | None
    request_time_ms: float | None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (percentile / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] * (hi - k) + ordered[hi] * (k - lo))


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.fmean(values))


def _load_queries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Query file must contain a JSON list.")
    return [item for item in data if isinstance(item, dict)]


def _measure_answer_sentence_coverage(answer_text: str, min_sentence_words: int = 6) -> tuple[float | None, float | None]:
    text = (answer_text or "").strip()
    if not text:
        return None, None
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]
    substantive = [sentence for sentence in sentences if len(sentence.split()) >= min_sentence_words]
    if not substantive:
        return None, None
    cited = 0
    for sentence in substantive:
        if re.search(r"\[chunk:[0-9a-fA-F-]{8,}\]", sentence, re.IGNORECASE) or re.search(
            r"\[SOURCE\s+\d+\]", sentence, re.IGNORECASE
        ):
            cited += 1
    coverage = cited / len(substantive)
    uncited_rate = (len(substantive) - cited) / len(substantive)
    return float(coverage), float(uncited_rate)


def _build_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": item["query"],
        "corpus": item["corpus"],
        "top_k": int(item.get("top_k", 5)),
    }
    if "model" in item and item["model"]:
        payload["model"] = item["model"]
    if "retrieval_mode" in item and item["retrieval_mode"]:
        payload["retrieval_mode"] = item["retrieval_mode"]
    if "history" in item and isinstance(item["history"], list):
        payload["history"] = item["history"]
    return payload


def _evaluate_query(
    client: httpx.Client,
    api_base: str,
    item: dict[str, Any],
) -> QueryEvalResult:
    query_id = str(item.get("id") or "")
    corpus = str(item.get("corpus") or "")
    query = str(item.get("query") or "")
    payload = _build_payload(item)
    start = perf_counter()
    try:
        response = client.post(f"{api_base}/api/query", json=payload)
        request_time_ms = (perf_counter() - start) * 1000.0
    except Exception as exc:  # noqa: BLE001
        return QueryEvalResult(
            query_id=query_id,
            corpus=corpus,
            query=query,
            ok=False,
            status_code=None,
            error=f"{type(exc).__name__}: {exc}",
            refusal=None,
            grounded=None,
            citation_count=None,
            answer_sentence_citation_coverage=None,
            uncited_sentence_rate=None,
            hierarchy_metadata_presence_rate=None,
            retrieval_time_ms=None,
            generation_time_ms=None,
            request_time_ms=request_time_ms if "request_time_ms" in locals() else None,
        )

    if response.status_code != 200:
        detail = response.text.strip()
        try:
            body = response.json()
            if isinstance(body, dict) and "detail" in body:
                detail = str(body["detail"])
        except Exception:  # noqa: BLE001
            pass
        return QueryEvalResult(
            query_id=query_id,
            corpus=corpus,
            query=query,
            ok=False,
            status_code=response.status_code,
            error=detail[:1000] if detail else f"HTTP {response.status_code}",
            refusal=None,
            grounded=None,
            citation_count=None,
            answer_sentence_citation_coverage=None,
            uncited_sentence_rate=None,
            hierarchy_metadata_presence_rate=None,
            retrieval_time_ms=None,
            generation_time_ms=None,
            request_time_ms=request_time_ms,
        )

    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        return QueryEvalResult(
            query_id=query_id,
            corpus=corpus,
            query=query,
            ok=False,
            status_code=response.status_code,
            error=f"JSON parse error: {exc}",
            refusal=None,
            grounded=None,
            citation_count=None,
            answer_sentence_citation_coverage=None,
            uncited_sentence_rate=None,
            hierarchy_metadata_presence_rate=None,
            retrieval_time_ms=None,
            generation_time_ms=None,
            request_time_ms=request_time_ms,
        )

    citations = body.get("citations") if isinstance(body, dict) else []
    citation_count = len(citations) if isinstance(citations, list) else 0
    answer_text = body.get("answer", "") if isinstance(body, dict) else ""
    answer_sentence_citation_coverage, uncited_sentence_rate = _measure_answer_sentence_coverage(answer_text)
    hierarchy_metadata_presence_rate: float | None = None
    if isinstance(citations, list) and citations:
        with_hierarchy = 0
        for item in citations:
            if not isinstance(item, dict):
                continue
            node_path = item.get("node_path")
            section_path = item.get("section_path")
            if (isinstance(node_path, str) and node_path.strip()) or (isinstance(section_path, str) and section_path.strip()):
                with_hierarchy += 1
        hierarchy_metadata_presence_rate = with_hierarchy / len(citations)
    retrieval_ms = body.get("retrieval_time_ms") if isinstance(body, dict) else None
    generation_ms = body.get("generation_time_ms") if isinstance(body, dict) else None

    return QueryEvalResult(
        query_id=query_id,
        corpus=corpus,
        query=query,
        ok=True,
        status_code=response.status_code,
        error=None,
        refusal=bool(body.get("refusal")) if isinstance(body, dict) else None,
        grounded=bool(body.get("grounded")) if isinstance(body, dict) else None,
        citation_count=citation_count,
        answer_sentence_citation_coverage=answer_sentence_citation_coverage,
        uncited_sentence_rate=uncited_sentence_rate,
        hierarchy_metadata_presence_rate=hierarchy_metadata_presence_rate,
        retrieval_time_ms=float(retrieval_ms) if retrieval_ms is not None else None,
        generation_time_ms=float(generation_ms) if generation_ms is not None else None,
        request_time_ms=request_time_ms,
    )


def _make_report(
    api_base: str,
    input_file: Path,
    timeout_sec: float,
    results: list[QueryEvalResult],
) -> dict[str, Any]:
    total = len(results)
    successes = [r for r in results if r.ok]
    failures = [r for r in results if not r.ok]

    refusal_values = [1.0 for r in successes if r.refusal is True]
    grounded_values = [1.0 for r in successes if r.grounded is True]

    citation_counts = [float(r.citation_count) for r in successes if r.citation_count is not None]
    citation_coverage = [
        float(r.answer_sentence_citation_coverage)
        for r in successes
        if r.answer_sentence_citation_coverage is not None
    ]
    uncited_sentence_rates = [float(r.uncited_sentence_rate) for r in successes if r.uncited_sentence_rate is not None]
    hierarchy_presence_rates = [
        float(r.hierarchy_metadata_presence_rate)
        for r in successes
        if r.hierarchy_metadata_presence_rate is not None
    ]
    retrieval_lat = [float(r.retrieval_time_ms) for r in successes if r.retrieval_time_ms is not None]
    generation_lat = [float(r.generation_time_ms) for r in successes if r.generation_time_ms is not None]
    request_lat = [float(r.request_time_ms) for r in successes if r.request_time_ms is not None]

    refusal_rate = (len(refusal_values) / len(successes)) if successes else None
    grounded_rate = (len(grounded_values) / len(successes)) if successes else None
    error_rate = (len(failures) / total) if total else None

    return {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "api_base": api_base,
            "input_file": str(input_file),
            "timeout_sec": timeout_sec,
            "total_queries": total,
        },
        "summary": {
            "success_count": len(successes),
            "error_count": len(failures),
            "error_rate": error_rate,
            "refusal_rate": refusal_rate,
            "grounded_rate": grounded_rate,
        },
        "metrics": {
            "citation_count": {
                "avg": _avg(citation_counts),
                "p50": _percentile(citation_counts, 50),
                "p95": _percentile(citation_counts, 95),
                "min": min(citation_counts) if citation_counts else None,
                "max": max(citation_counts) if citation_counts else None,
            },
            "answer_sentence_citation_coverage": {
                "avg": _avg(citation_coverage),
                "p50": _percentile(citation_coverage, 50),
                "p95": _percentile(citation_coverage, 95),
            },
            "uncited_sentence_rate": {
                "avg": _avg(uncited_sentence_rates),
                "p50": _percentile(uncited_sentence_rates, 50),
                "p95": _percentile(uncited_sentence_rates, 95),
            },
            "hierarchy_metadata_presence_rate": {
                "avg": _avg(hierarchy_presence_rates),
                "p50": _percentile(hierarchy_presence_rates, 50),
                "p95": _percentile(hierarchy_presence_rates, 95),
            },
            "retrieval_time_ms": {
                "avg": _avg(retrieval_lat),
                "p50": _percentile(retrieval_lat, 50),
                "p95": _percentile(retrieval_lat, 95),
            },
            "generation_time_ms": {
                "avg": _avg(generation_lat),
                "p50": _percentile(generation_lat, 50),
                "p95": _percentile(generation_lat, 95),
            },
            "request_time_ms": {
                "avg": _avg(request_lat),
                "p50": _percentile(request_lat, 50),
                "p95": _percentile(request_lat, 95),
            },
        },
        "failures": [
            {
                "id": r.query_id,
                "corpus": r.corpus,
                "status_code": r.status_code,
                "error": r.error,
            }
            for r in failures
        ],
        "results": [
            {
                "id": r.query_id,
                "corpus": r.corpus,
                "ok": r.ok,
                "status_code": r.status_code,
                "refusal": r.refusal,
                "grounded": r.grounded,
                "citation_count": r.citation_count,
                "answer_sentence_citation_coverage": r.answer_sentence_citation_coverage,
                "uncited_sentence_rate": r.uncited_sentence_rate,
                "hierarchy_metadata_presence_rate": r.hierarchy_metadata_presence_rate,
                "retrieval_time_ms": r.retrieval_time_ms,
                "generation_time_ms": r.generation_time_ms,
                "request_time_ms": r.request_time_ms,
                "error": r.error,
            }
            for r in results
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 1 query quality harness.")
    parser.add_argument(
        "--api-base",
        default="http://localhost:8000",
        help="Base URL for backend API.",
    )
    parser.add_argument(
        "--input",
        default="eval/phase1_queries.json",
        help="Path to query-set JSON file.",
    )
    parser.add_argument(
        "--output",
        default="eval/reports/baseline.json",
        help="Path to output JSON report file.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=90.0,
        help="Per-request timeout in seconds.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_file = Path(args.input)
    output_file = Path(args.output)
    api_base = str(args.api_base).rstrip("/")

    queries = _load_queries(input_file)
    results: list[QueryEvalResult] = []
    with httpx.Client(timeout=args.timeout_sec) as client:
        for item in queries:
            if not item.get("query") or not item.get("corpus"):
                continue
            results.append(_evaluate_query(client, api_base, item))

    report = _make_report(
        api_base=api_base,
        input_file=input_file,
        timeout_sec=args.timeout_sec,
        results=results,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote report: {output_file}")


if __name__ == "__main__":
    main()
