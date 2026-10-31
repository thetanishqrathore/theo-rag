"""Unified benchmark runner for query + stream + evidence checks."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # Telemetry join imports src.*; running as `python scripts/eval_suite.py` puts
    # only scripts/ on sys.path.
    sys.path.insert(0, str(ROOT))


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.fmean(values))


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


def _measure_sentence_citation_coverage(answer_text: str, min_sentence_words: int = 6) -> dict[str, float | int | None]:
    text = (answer_text or "").strip()
    if not text:
        return {
            "answer_sentence_citation_coverage": None,
            "uncited_sentence_rate": None,
            "substantive_sentences": 0,
            "cited_sentences": 0,
        }
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]
    substantive = [s for s in sentences if len(s.split()) >= min_sentence_words]
    if not substantive:
        return {
            "answer_sentence_citation_coverage": None,
            "uncited_sentence_rate": None,
            "substantive_sentences": 0,
            "cited_sentences": 0,
        }

    cited = 0
    for sentence in substantive:
        if re.search(r"\[chunk:[0-9a-fA-F-]{8,}\]", sentence) or re.search(
            r"\[SOURCE\s+\d+\]", sentence
        ):
            cited += 1

    coverage = cited / len(substantive)
    uncited = (len(substantive) - cited) / len(substantive)
    return {
        "answer_sentence_citation_coverage": float(coverage),
        "uncited_sentence_rate": float(uncited),
        "substantive_sentences": len(substantive),
        "cited_sentences": cited,
    }


def _preflight_api_health(
    client: httpx.Client,
    api_base: str,
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    url = f"{api_base}/api/health"
    start = time.perf_counter()
    try:
        response = client.get(url, headers={"Accept": "application/json"}, timeout=timeout_sec)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Preflight failed: could not reach {url} ({type(exc).__name__}: {exc})"
        ) from exc
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if response.status_code != 200:
        body = response.text.strip()[:400]
        detail = f" body={body}" if body else ""
        raise RuntimeError(
            f"Preflight failed: GET {url} returned HTTP {response.status_code}.{detail}"
        )

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Preflight failed: {url} returned non-JSON payload ({type(exc).__name__}: {exc})"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"Preflight failed: {url} payload was not a JSON object.")

    status = str(payload.get("status") or "").lower()
    services = payload.get("services")
    if status != "healthy":
        raise RuntimeError(
            f"Preflight failed: {url} status='{status or 'unknown'}' services={services}"
        )

    return {
        "status": status,
        "services": services if isinstance(services, dict) else {},
        "latency_ms": round(float(elapsed_ms), 3),
    }


def parse_sse_text(raw_text: str) -> list[dict[str, str]]:
    """Parse raw SSE text into frames with `event` and `data`."""
    frames: list[dict[str, str]] = []
    current_event = ""
    current_data: list[str] = []

    def flush() -> None:
        nonlocal current_event, current_data
        if not current_event and not current_data:
            return
        event_name = current_event or "message"
        data_text = "\n".join(current_data)
        frames.append({"event": event_name, "data": data_text})
        current_event = ""
        current_data = []

    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            current_data.append(payload)
        elif line == "":
            flush()

    flush()
    return frames


def parse_stream_contract(frames: list[dict[str, str]]) -> dict[str, Any]:
    """Validate stream payload contract from SSE frames."""
    event_type_counts: Counter[str] = Counter()
    contract_errors: list[str] = []
    final_payload: dict[str, Any] | None = None
    citations_items: list[dict[str, Any]] = []
    used_evidence_ids: list[str] = []
    answer_text_parts: list[str] = []
    error_payload: dict[str, Any] | None = None

    for frame in frames:
        event_name = frame.get("event", "")
        data = frame.get("data", "")

        if event_name == "error":
            try:
                parsed_error = json.loads(data) if data else {}
                if isinstance(parsed_error, dict):
                    error_payload = parsed_error
                else:
                    error_payload = {"error": str(parsed_error)}
            except json.JSONDecodeError:
                error_payload = {"error": data or "stream_error"}
            continue

        if event_name != "message":
            contract_errors.append(f"Unexpected SSE event channel: {event_name}")
            continue

        if not data:
            continue

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            contract_errors.append("Invalid JSON in message event")
            continue

        if not isinstance(payload, dict):
            contract_errors.append("Message payload must be an object")
            continue

        payload_type = payload.get("type")
        if payload_type not in {"status", "citations", "token", "final"}:
            contract_errors.append(f"Unexpected payload type: {payload_type}")
            continue

        event_type_counts[str(payload_type)] += 1

        if payload_type == "token":
            token = payload.get("content")
            if isinstance(token, str):
                answer_text_parts.append(token)
        elif payload_type == "citations":
            items = payload.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        citations_items.append(item)
        elif payload_type == "final":
            if final_payload is not None:
                contract_errors.append("Multiple final events detected")
            final_payload = payload
            ids = payload.get("used_evidence_ids")
            if isinstance(ids, list):
                used_evidence_ids = [str(item) for item in ids if str(item)]

    final_received = final_payload is not None
    stream_incomplete = not final_received and error_payload is None
    if stream_incomplete:
        contract_errors.append("Missing final event")

    return {
        "final_received": final_received,
        "stream_incomplete": stream_incomplete,
        "event_type_counts": dict(event_type_counts),
        "contract_errors": contract_errors,
        "final_payload": final_payload,
        "error_payload": error_payload,
        "citations_items": citations_items,
        "used_evidence_ids": used_evidence_ids,
        "answer_text": "".join(answer_text_parts),
    }


@dataclass
class EvalCase:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def corpus(self) -> str:
        return str(self.raw["corpus"])


@dataclass
class EndpointResult:
    ok: bool
    status_code: int | None
    error: str | None
    request_time_ms: float | None
    payload: dict[str, Any] | None


def _load_cases(path: Path) -> list[EvalCase]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("JSON input must be a list.")
        rows = [item for item in parsed if isinstance(item, dict)]
    else:
        rows = []
        for line in text.splitlines():
            item = line.strip()
            if not item:
                continue
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                rows.append(parsed)
    return [EvalCase(raw=row) for row in rows]


def _build_payload(case: EvalCase) -> dict[str, Any]:
    payload = {
        "query": case.raw["query"],
        "corpus": case.raw["corpus"],
        "top_k": int(case.raw.get("top_k", 5)),
        "retrieval_mode": case.raw.get("retrieval_mode"),
    }
    history = case.raw.get("history")
    if isinstance(history, list):
        payload["history"] = history
    model = case.raw.get("model")
    if isinstance(model, str) and model.strip():
        payload["model"] = model.strip()
    return payload


def _run_query(client: httpx.Client, api_base: str, payload: dict[str, Any], headers: dict[str, str]) -> EndpointResult:
    start = time.perf_counter()
    try:
        response = client.post(f"{api_base}/api/query", json=payload, headers=headers)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # noqa: BLE001
        return EndpointResult(False, None, f"{type(exc).__name__}: {exc}", None, None)

    if response.status_code != 200:
        error_text = response.text.strip()[:1200]
        return EndpointResult(False, response.status_code, error_text or f"HTTP {response.status_code}", elapsed_ms, None)

    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        return EndpointResult(False, response.status_code, f"JSON parse error: {exc}", elapsed_ms, None)

    if not isinstance(body, dict):
        return EndpointResult(False, response.status_code, "Query payload was not an object.", elapsed_ms, None)

    return EndpointResult(True, response.status_code, None, elapsed_ms, body)


def _run_stream(
    client: httpx.Client,
    api_base: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> EndpointResult:
    start = time.perf_counter()
    raw_text = ""
    status_code: int | None = None
    try:
        with client.stream("POST", f"{api_base}/api/query/stream", json=payload, headers=headers) as response:
            status_code = response.status_code
            raw_text = "".join(chunk for chunk in response.iter_text())
        elapsed_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # noqa: BLE001
        return EndpointResult(False, None, f"{type(exc).__name__}: {exc}", None, None)

    if status_code != 200:
        return EndpointResult(False, status_code, raw_text[:1200] or f"HTTP {status_code}", elapsed_ms, None)

    frames = parse_sse_text(raw_text)
    contract = parse_stream_contract(frames)
    payload_out = {
        "raw_text": raw_text,
        "frames": frames,
        "contract": contract,
    }

    # A stream fails on any contract error, and on a server error event that ends
    # it without a final event (previously both had to hold, so an error-only
    # stream counted as a success).
    if contract["contract_errors"]:
        return EndpointResult(False, status_code, json.dumps(contract["contract_errors"]), elapsed_ms, payload_out)
    if contract["error_payload"] is not None and not contract["final_received"]:
        return EndpointResult(False, status_code, json.dumps(contract["error_payload"]), elapsed_ms, payload_out)

    return EndpointResult(True, status_code, None, elapsed_ms, payload_out)


def _extract_query_evidence_ids(query_payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(query_payload, dict):
        return []
    citations = query_payload.get("citations")
    if not isinstance(citations, list):
        return []
    ids: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        child_id = citation.get("child_chunk_id")
        if isinstance(child_id, str) and child_id:
            ids.append(child_id)
    return ids


def _extract_stream_evidence_ids(stream_payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(stream_payload, dict):
        return []
    contract = stream_payload.get("contract")
    if not isinstance(contract, dict):
        return []

    ids: list[str] = []
    for item in contract.get("citations_items", []):
        if isinstance(item, dict):
            evidence_id = item.get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id:
                ids.append(evidence_id)
    for item in contract.get("used_evidence_ids", []):
        if isinstance(item, str) and item:
            ids.append(item)
    return ids


def _fetch_evidence(
    client: httpx.Client,
    api_base: str,
    evidence_ids: Iterable[str],
    headers: dict[str, str],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for evidence_id in sorted(set(evidence_ids)):
        try:
            response = client.get(f"{api_base}/api/evidence/{evidence_id}", headers=headers)
            if response.status_code != 200:
                results[evidence_id] = {
                    "ok": False,
                    "status_code": response.status_code,
                    "error": response.text[:500],
                }
                continue
            payload = response.json()
            if not isinstance(payload, dict):
                results[evidence_id] = {
                    "ok": False,
                    "status_code": response.status_code,
                    "error": "Evidence payload was not an object.",
                }
                continue
            results[evidence_id] = {
                "ok": True,
                "status_code": response.status_code,
                "payload": payload,
            }
        except Exception as exc:  # noqa: BLE001
            results[evidence_id] = {
                "ok": False,
                "status_code": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return results


def _compute_anchor_recall(case: EvalCase, answer_text: str, evidence_results: dict[str, dict[str, Any]]) -> float | None:
    anchors = case.raw.get("gold_anchors")
    if not isinstance(anchors, list) or not anchors:
        return None

    terms: list[str] = []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        anchor_terms = anchor.get("terms")
        if isinstance(anchor_terms, list):
            terms.extend(str(term).strip().lower() for term in anchor_terms if str(term).strip())

    if not terms:
        return None

    evidence_text = []
    for item in evidence_results.values():
        if not item.get("ok"):
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            primary = payload.get("primary_text")
            if isinstance(primary, str) and primary.strip():
                evidence_text.append(primary.strip().lower())

    haystacks = [answer_text.lower()] + evidence_text
    hits = 0
    unique_terms = sorted(set(terms))
    for term in unique_terms:
        if any(term in hay for hay in haystacks):
            hits += 1

    return hits / len(unique_terms)


def _observed_answer_mode(query_payload: dict[str, Any] | None, stream_payload: dict[str, Any] | None) -> str | None:
    if isinstance(stream_payload, dict):
        contract = stream_payload.get("contract")
        if isinstance(contract, dict):
            final_payload = contract.get("final_payload")
            if isinstance(final_payload, dict):
                mode = final_payload.get("answer_mode")
                if isinstance(mode, str):
                    return mode

    if isinstance(query_payload, dict):
        refusal = bool(query_payload.get("refusal"))
        grounded = bool(query_payload.get("grounded"))
        if refusal:
            return "refuse"
        if grounded:
            return "confident"
        return "narrow"

    return None


def _hierarchy_presence_rate_from_citations(citations: list[dict[str, Any]]) -> float | None:
    if not citations:
        return None
    with_hierarchy = 0
    for citation in citations:
        node_path = citation.get("node_path") if isinstance(citation, dict) else None
        section_path = citation.get("section_path") if isinstance(citation, dict) else None
        if (isinstance(node_path, str) and node_path.strip()) or (isinstance(section_path, str) and section_path.strip()):
            with_hierarchy += 1
    return with_hierarchy / len(citations)


def _score_case(
    case: EvalCase,
    query_result: EndpointResult | None,
    stream_result: EndpointResult | None,
    evidence_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    query_payload = query_result.payload if query_result else None
    stream_payload = stream_result.payload if stream_result else None

    answer_text = ""
    if isinstance(query_payload, dict):
        answer = query_payload.get("answer")
        if isinstance(answer, str):
            answer_text = answer
    if not answer_text and isinstance(stream_payload, dict):
        contract = stream_payload.get("contract")
        if isinstance(contract, dict):
            answer_text = str(contract.get("answer_text") or "")

    expected_mode = str(case.raw.get("expected_mode"))
    must_refuse = bool(case.raw.get("must_refuse"))
    observed_mode = _observed_answer_mode(query_payload, stream_payload)

    refusal_observed: bool | None = None
    if isinstance(query_payload, dict):
        refusal_observed = bool(query_payload.get("refusal"))
    elif isinstance(stream_payload, dict):
        final_payload = stream_payload.get("contract", {}).get("final_payload") if isinstance(stream_payload.get("contract"), dict) else None
        if isinstance(final_payload, dict):
            refusal_observed = bool(final_payload.get("refusal"))

    refusal_correct = None
    if refusal_observed is not None:
        refusal_correct = 1.0 if refusal_observed == must_refuse else 0.0

    mode_correct = None
    if observed_mode is not None:
        mode_correct = 1.0 if observed_mode == expected_mode else 0.0

    query_citations = []
    if isinstance(query_payload, dict) and isinstance(query_payload.get("citations"), list):
        query_citations = [item for item in query_payload.get("citations", []) if isinstance(item, dict)]

    stream_citations = []
    if isinstance(stream_payload, dict):
        contract = stream_payload.get("contract")
        if isinstance(contract, dict):
            stream_citations = [item for item in contract.get("citations_items", []) if isinstance(item, dict)]

    all_citations = query_citations if query_citations else stream_citations
    citation_count = len(all_citations)

    coverage = _measure_sentence_citation_coverage(answer_text)

    min_citations = int(case.raw.get("min_citations", 0))
    citation_floor_pass = 1.0
    if not must_refuse:
        citation_floor_pass = 1.0 if citation_count >= min_citations else 0.0

    min_cov = float(case.raw.get("silver_rules", {}).get("min_sentence_citation_coverage", 0.0))
    max_uncited = float(case.raw.get("silver_rules", {}).get("max_uncited_sentence_rate", 1.0))

    cov_value = coverage.get("answer_sentence_citation_coverage")
    uncited_value = coverage.get("uncited_sentence_rate")

    coverage_score = None
    if cov_value is not None:
        coverage_score = min(1.0, float(cov_value) / max(min_cov, 1e-6))

    uncited_score = None
    if uncited_value is not None:
        uncited_score = 1.0 if float(uncited_value) <= max_uncited else max(0.0, 1.0 - (float(uncited_value) - max_uncited) * 2.0)

    stream_contract_pass = None
    stream_incomplete = False
    stream_contract_errors = []
    if isinstance(stream_payload, dict):
        contract = stream_payload.get("contract")
        if isinstance(contract, dict):
            stream_contract_errors = list(contract.get("contract_errors", []))
            stream_incomplete = bool(contract.get("stream_incomplete", False))
            stream_contract_pass = 1.0 if not stream_contract_errors else 0.0

    hierarchy_presence_rate = _hierarchy_presence_rate_from_citations(all_citations)

    # Share of returned evidence IDs (from both endpoints) that resolve via
    # /api/evidence. The denominator used to be one endpoint's citation count while
    # the numerator counted IDs from both, producing rates above 1.
    evidence_success = sum(1 for item in evidence_results.values() if item.get("ok"))
    evidence_failed_ids = sorted(eid for eid, item in evidence_results.items() if not item.get("ok"))
    evidence_support_rate = None
    if evidence_results:
        evidence_support_rate = evidence_success / len(evidence_results)
    elif citation_count > 0:
        evidence_support_rate = 0.0
    elif must_refuse:
        evidence_support_rate = 1.0

    anchor_recall = _compute_anchor_recall(case, answer_text, evidence_results)

    gold_components = [value for value in (refusal_correct, mode_correct, anchor_recall, evidence_support_rate) if value is not None]
    gold_score = _avg([float(value) for value in gold_components])

    silver_components = [value for value in (citation_floor_pass, coverage_score, uncited_score, stream_contract_pass) if value is not None]
    silver_score = _avg([float(value) for value in silver_components])

    composite_score = None
    if gold_score is not None and silver_score is not None:
        composite_score = 0.7 * gold_score + 0.3 * silver_score
    elif gold_score is not None:
        composite_score = gold_score
    elif silver_score is not None:
        composite_score = silver_score

    return {
        "id": case.id,
        "corpus": case.corpus,
        "class": next((tag for tag in case.raw.get("class_tags", []) if tag in {
            "definition", "synthesis", "comparison", "multi_turn", "ambiguity", "refusal", "citation_hard"
        }), "unknown"),
        "label_tier": case.raw.get("label_tier"),
        "must_refuse": must_refuse,
        "expected_mode": expected_mode,
        "observed_mode": observed_mode,
        "refusal_observed": refusal_observed,
        "citation_count": citation_count,
        "answer_sentence_citation_coverage": cov_value,
        "uncited_sentence_rate": uncited_value,
        "hierarchy_metadata_presence_rate": hierarchy_presence_rate,
        "anchor_recall_at_k": anchor_recall,
        "evidence_support_rate": evidence_support_rate,
        "evidence_ids_checked": len(evidence_results),
        "evidence_failed_ids": evidence_failed_ids,
        "answer_text": answer_text,
        "refusal_correct": refusal_correct,
        "mode_correct": mode_correct,
        "citation_floor_pass": citation_floor_pass,
        "coverage_score": coverage_score,
        "uncited_score": uncited_score,
        "stream_contract_pass": stream_contract_pass,
        "stream_incomplete": stream_incomplete,
        "stream_contract_errors": stream_contract_errors,
        "gold_score": gold_score,
        "silver_score": silver_score,
        "composite_score": composite_score,
        "query_ok": query_result.ok if query_result else None,
        "query_status_code": query_result.status_code if query_result else None,
        "query_error": query_result.error if query_result else None,
        "query_request_time_ms": query_result.request_time_ms if query_result else None,
        "stream_ok": stream_result.ok if stream_result else None,
        "stream_status_code": stream_result.status_code if stream_result else None,
        "stream_error": stream_result.error if stream_result else None,
        "stream_request_time_ms": stream_result.request_time_ms if stream_result else None,
        "evidence_fetch_total": len(evidence_results),
        "evidence_fetch_success": evidence_success,
    }


def _f1(tp: int, fp: int, fn: int) -> float | None:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _build_metrics(per_case: list[dict[str, Any]], mode: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    total_cases = len(per_case)

    query_attempts = [c for c in per_case if c.get("query_ok") is not None]
    stream_attempts = [c for c in per_case if c.get("stream_ok") is not None]
    endpoint_attempts = len(query_attempts) + len(stream_attempts)

    endpoint_errors = sum(1 for c in query_attempts if c.get("query_ok") is False) + sum(
        1 for c in stream_attempts if c.get("stream_ok") is False
    )

    stream_incomplete_count = sum(1 for c in stream_attempts if c.get("stream_incomplete"))
    contract_failures = sum(1 for c in stream_attempts if c.get("stream_contract_errors"))

    refusal_tp = refusal_fp = refusal_fn = 0
    for case in per_case:
        expected = bool(case.get("must_refuse"))
        observed = bool(case.get("refusal_observed")) if case.get("refusal_observed") is not None else False
        if observed and expected:
            refusal_tp += 1
        elif observed and not expected:
            refusal_fp += 1
        elif (not observed) and expected:
            refusal_fn += 1

    refusal_f1 = _f1(refusal_tp, refusal_fp, refusal_fn)

    gold_rows = [c for c in per_case if c.get("label_tier") == "gold"]
    silver_rows = [c for c in per_case if c.get("label_tier") == "silver"]

    summary = {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "total_cases": total_cases,
        },
        "counts": {
            "total_cases": total_cases,
            "query_attempts": len(query_attempts),
            "stream_attempts": len(stream_attempts),
            "query_success_count": sum(1 for c in query_attempts if c.get("query_ok")),
            "stream_success_count": sum(1 for c in stream_attempts if c.get("stream_ok")),
            "endpoint_error_count": endpoint_errors,
            "gold_case_count": len(gold_rows),
            "silver_case_count": len(silver_rows),
        },
        "hard_signals": {
            "contract_failures": contract_failures,
            "error_rate": (endpoint_errors / endpoint_attempts) if endpoint_attempts else None,
            "stream_incomplete_rate": (stream_incomplete_count / len(stream_attempts)) if stream_attempts else None,
            "telemetry_join_rate": None,
        },
        "gold_metrics": {
            "refusal_f1": refusal_f1,
            "answer_mode_accuracy": _avg([float(c["mode_correct"]) for c in gold_rows if c.get("mode_correct") is not None]),
            "anchor_recall_at_k_avg": _avg([float(c["anchor_recall_at_k"]) for c in gold_rows if c.get("anchor_recall_at_k") is not None]),
            "evidence_support_rate_avg": _avg([float(c["evidence_support_rate"]) for c in gold_rows if c.get("evidence_support_rate") is not None]),
        },
        "silver_metrics": {
            "citation_floor_pass_rate": _avg([float(c["citation_floor_pass"]) for c in silver_rows if c.get("citation_floor_pass") is not None]),
            "answer_sentence_citation_coverage_avg": _avg([
                float(c["answer_sentence_citation_coverage"]) for c in silver_rows if c.get("answer_sentence_citation_coverage") is not None
            ]),
            "uncited_sentence_rate_avg": _avg([
                float(c["uncited_sentence_rate"]) for c in silver_rows if c.get("uncited_sentence_rate") is not None
            ]),
            "hierarchy_metadata_presence_rate_avg": _avg([
                float(c["hierarchy_metadata_presence_rate"]) for c in silver_rows if c.get("hierarchy_metadata_presence_rate") is not None
            ]),
        },
        "latency_metrics": {
            "query_request_ms_avg": _avg([float(c["query_request_time_ms"]) for c in query_attempts if c.get("query_request_time_ms") is not None]),
            "query_request_ms_p95": _percentile(
                [float(c["query_request_time_ms"]) for c in query_attempts if c.get("query_request_time_ms") is not None], 95
            ),
            "stream_request_ms_avg": _avg([float(c["stream_request_time_ms"]) for c in stream_attempts if c.get("stream_request_time_ms") is not None]),
            "stream_request_ms_p95": _percentile(
                [float(c["stream_request_time_ms"]) for c in stream_attempts if c.get("stream_request_time_ms") is not None], 95
            ),
        },
        "scoring": {
            "gold_score_avg": _avg([float(c["gold_score"]) for c in per_case if c.get("gold_score") is not None]),
            "silver_score_avg": _avg([float(c["silver_score"]) for c in per_case if c.get("silver_score") is not None]),
            "composite_score_avg": _avg([float(c["composite_score"]) for c in per_case if c.get("composite_score") is not None]),
        },
    }

    by_corpus: dict[str, Any] = {}
    for corpus in sorted({case["corpus"] for case in per_case}):
        rows = [case for case in per_case if case["corpus"] == corpus]
        by_corpus[corpus] = {
            "case_count": len(rows),
            "composite_score_avg": _avg([float(c["composite_score"]) for c in rows if c.get("composite_score") is not None]),
            "refusal_f1": _f1(
                sum(1 for c in rows if c.get("must_refuse") and c.get("refusal_observed") is True),
                sum(1 for c in rows if (not c.get("must_refuse")) and c.get("refusal_observed") is True),
                sum(1 for c in rows if c.get("must_refuse") and c.get("refusal_observed") is False),
            ),
            "answer_sentence_citation_coverage_avg": _avg([
                float(c["answer_sentence_citation_coverage"]) for c in rows if c.get("answer_sentence_citation_coverage") is not None
            ]),
            "uncited_sentence_rate_avg": _avg([
                float(c["uncited_sentence_rate"]) for c in rows if c.get("uncited_sentence_rate") is not None
            ]),
            "stream_contract_failure_count": sum(1 for c in rows if c.get("stream_contract_errors")),
        }

    by_class: dict[str, Any] = {}
    for cls in sorted({case["class"] for case in per_case}):
        rows = [case for case in per_case if case["class"] == cls]
        by_class[cls] = {
            "case_count": len(rows),
            "composite_score_avg": _avg([float(c["composite_score"]) for c in rows if c.get("composite_score") is not None]),
            "anchor_recall_at_k_avg": _avg([float(c["anchor_recall_at_k"]) for c in rows if c.get("anchor_recall_at_k") is not None]),
            "citation_floor_pass_rate": _avg([float(c["citation_floor_pass"]) for c in rows if c.get("citation_floor_pass") is not None]),
            "hierarchy_metadata_presence_rate_avg": _avg([
                float(c["hierarchy_metadata_presence_rate"]) for c in rows if c.get("hierarchy_metadata_presence_rate") is not None
            ]),
        }

    return summary, by_corpus, by_class, {
        "stream_contract_failure_count": contract_failures,
        "stream_incomplete_count": stream_incomplete_count,
    }


def _analyze_telemetry_join(session_ids: list[str], mode: str) -> dict[str, Any]:
    try:
        from sqlalchemy import select

        from src.db.models import RagEvidenceUsed, RagQueryRun, RagRerank
        from src.db.session import get_sessionmaker
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": False,
            "error": f"telemetry import failed: {exc}",
            "join_rate": None,
        }

    if not session_ids:
        return {
            "enabled": True,
            "join_rate": None,
            "reason": "no session ids",
        }

    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        query_rows = session.execute(
            select(RagQueryRun.run_id, RagQueryRun.user_session_id, RagQueryRun.endpoint).where(
                RagQueryRun.user_session_id.in_(session_ids)
            )
        ).all()

        found_by_session: dict[str, set[str]] = defaultdict(set)
        run_ids: list[Any] = []
        for run_id, user_session_id, endpoint in query_rows:
            if isinstance(user_session_id, str):
                found_by_session[user_session_id].add(str(endpoint))
            run_ids.append(run_id)

        expected_endpoints = {"/api/query", "/api/query/stream"} if mode == "both" else {f"/api/{mode}"}
        matched = 0
        missing: list[str] = []
        for sid in session_ids:
            found = found_by_session.get(sid, set())
            if expected_endpoints.issubset(found):
                matched += 1
            else:
                missing.append(sid)

        rerank_count = 0
        evidence_count = 0
        orphan_rerank = 0
        orphan_evidence = 0
        if run_ids:
            run_id_set = set(run_ids)
            rerank_rows = session.execute(select(RagRerank.run_id).where(RagRerank.run_id.in_(run_ids))).all()
            evidence_rows = session.execute(select(RagEvidenceUsed.run_id).where(RagEvidenceUsed.run_id.in_(run_ids))).all()
            rerank_count = len(rerank_rows)
            evidence_count = len(evidence_rows)
            orphan_rerank = sum(1 for row in rerank_rows if row[0] not in run_id_set)
            orphan_evidence = sum(1 for row in evidence_rows if row[0] not in run_id_set)

        join_rate = matched / len(session_ids)
        return {
            "enabled": True,
            "session_id_count": len(session_ids),
            "query_run_row_count": len(query_rows),
            "matched_session_count": matched,
            "missing_session_count": len(missing),
            "missing_sessions": missing[:30],
            "join_rate": join_rate,
            "rerank_row_count": rerank_count,
            "evidence_row_count": evidence_count,
            "orphan_rerank_rows_detected": orphan_rerank,
            "orphan_evidence_rows_detected": orphan_evidence,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": False,
            "error": str(exc),
            "join_rate": None,
        }
    finally:
        session.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run benchmark suite against query and stream endpoints.")
    parser.add_argument("--api-base", default="http://localhost:8000", help="Backend API base URL.")
    parser.add_argument(
        "--input",
        default="eval/benchmark/v1_cases.jsonl",
        help="Path to benchmark dataset (JSONL or JSON list).",
    )
    parser.add_argument(
        "--mode",
        choices=["query", "stream", "both"],
        default="both",
        help="Which endpoint surface(s) to evaluate.",
    )
    parser.add_argument(
        "--output-root",
        default="eval/reports/runs",
        help="Root directory for run artifacts.",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional explicit run id. Defaults to UTC timestamp.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=90.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Optional cap for quick smoke iterations.",
    )
    parser.add_argument(
        "--skip-health-preflight",
        action="store_true",
        default=False,
        help="Skip /api/health readiness check before running benchmark calls.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_base = str(args.api_base).rstrip("/")
    input_path = Path(args.input)
    run_id = args.run_id.strip() or f"eval_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    output_root = Path(args.output_root)
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cases = _load_cases(input_path)
    if args.max_cases and args.max_cases > 0:
        cases = cases[: args.max_cases]

    per_case_rows: list[dict[str, Any]] = []
    session_ids: list[str] = []
    health_preflight: dict[str, Any] | None = None

    with httpx.Client(timeout=args.timeout_sec) as client:
        if not args.skip_health_preflight:
            health_preflight = _preflight_api_health(
                client,
                api_base,
                timeout_sec=min(float(args.timeout_sec), 10.0),
            )
            print(
                "Health preflight passed:",
                json.dumps(
                    {
                        "status": health_preflight.get("status"),
                        "latency_ms": health_preflight.get("latency_ms"),
                    }
                ),
            )
        for case in cases:
            payload = _build_payload(case)
            session_id = f"eval-{run_id}-{case.id}"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-Session-Id": session_id,
            }
            session_ids.append(session_id)

            query_result = None
            stream_result = None
            if args.mode in {"query", "both"}:
                query_result = _run_query(client, api_base, payload, headers)
            if args.mode in {"stream", "both"}:
                stream_result = _run_stream(client, api_base, payload, headers)

            evidence_ids = []
            if query_result:
                evidence_ids.extend(_extract_query_evidence_ids(query_result.payload))
            if stream_result:
                evidence_ids.extend(_extract_stream_evidence_ids(stream_result.payload))

            evidence_results = _fetch_evidence(client, api_base, evidence_ids, headers)
            scored = _score_case(case, query_result, stream_result, evidence_results)
            scored["session_id"] = session_id
            per_case_rows.append(scored)

    summary, by_corpus, by_class, diagnostics = _build_metrics(per_case_rows, args.mode)
    telemetry_join = _analyze_telemetry_join(session_ids, args.mode)
    summary["hard_signals"]["telemetry_join_rate"] = telemetry_join.get("join_rate")
    summary["diagnostics"] = diagnostics
    summary["metadata"].update(
        {
            "run_id": run_id,
            "api_base": api_base,
            "input_file": str(input_path),
            "timeout_sec": args.timeout_sec,
            "output_dir": str(run_dir),
            "health_preflight": health_preflight,
        }
    )

    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "metrics_by_corpus.json").write_text(json.dumps(by_corpus, indent=2), encoding="utf-8")
    (run_dir / "metrics_by_class.json").write_text(json.dumps(by_class, indent=2), encoding="utf-8")
    (run_dir / "telemetry_join.json").write_text(json.dumps(telemetry_join, indent=2), encoding="utf-8")

    with (run_dir / "per_case.jsonl").open("w", encoding="utf-8") as handle:
        for row in per_case_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    print(f"Wrote run artifacts: {run_dir}")
    print(f"Summary: {run_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
