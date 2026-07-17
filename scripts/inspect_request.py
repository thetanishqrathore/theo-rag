#!/usr/bin/env python3
"""Inspect log events for a single request_id."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _fmt_ts(ts: str | None) -> str:
    parsed = _parse_ts(ts)
    if not parsed:
        return "--:--:--"
    return parsed.strftime("%H:%M:%S.%f")[:-3]


def _short(event: dict[str, Any]) -> str:
    ev = event.get("event")
    span = event.get("span")
    if ev and span:
        return f"{ev} ({span})"
    return ev or "event"


def _summary(events: List[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for ev in events:
        if ev.get("event") == "request_end":
            summary["total_ms"] = ev.get("duration_ms")
        if ev.get("event") == "retrieval_end":
            summary["retrieval_ms"] = ev.get("duration_ms")
        if ev.get("event") == "generation_end":
            summary["generation_ms"] = ev.get("duration_ms")
        if ev.get("event") == "sufficiency_decision":
            summary["mode"] = ev.get("answer_mode")
            summary["s1"] = ev.get("s1")
            summary["s5"] = ev.get("s5")
        if ev.get("event") == "postprocess_end":
            summary["citations"] = ev.get("citations_count")
            summary["followups"] = ev.get("followups_count")
            summary["grounded"] = ev.get("grounded")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect log events for a request_id")
    parser.add_argument("request_id", help="Request ID to inspect")
    parser.add_argument("--log", default="data/logs/api.log", help="Path to log file")
    parser.add_argument("--json", action="store_true", help="Print raw JSON events")
    parser.add_argument("--spans-only", action="store_true", help="Show only span events")
    parser.add_argument("--stream-only", action="store_true", help="Show only stream events")
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        raise SystemExit(f"Log file not found: {log_path}")

    events: List[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("request_id") != args.request_id:
                continue
            if args.spans_only and not str(event.get("event", "")).endswith("_end"):
                continue
            if args.stream_only and not str(event.get("event", "")).startswith("stream_"):
                continue
            events.append(event)

    events.sort(
        key=lambda ev: _parse_ts(ev.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
    )

    if not events:
        raise SystemExit("No matching events found.")

    if args.json:
        for ev in events:
            print(json.dumps(ev, ensure_ascii=False))
        return

    first = events[0]
    route = first.get("route", "?")
    method = first.get("method", "?")
    print(f"Request: {args.request_id}")
    print(f"Route: {route} ({method})")
    print()

    for ev in events:
        ts = _fmt_ts(ev.get("timestamp"))
        event_name = ev.get("event")
        extra = ""
        if event_name == "vector_search_end":
            extra = f" candidates={ev.get('vector_candidates_count')} duration_ms={ev.get('duration_ms')}"
        if event_name == "bm25_search_end":
            extra = f" candidates={ev.get('bm25_candidates_count')} duration_ms={ev.get('duration_ms')}"
        if event_name == "candidate_merge_end":
            extra = f" pretrim={ev.get('union_candidates_pretrim_count')} posttrim={ev.get('union_candidates_posttrim_count')}"
        if event_name == "rerank_end":
            extra = f" reranked={ev.get('reranked_count')} duration_ms={ev.get('duration_ms')}"
        if event_name == "sufficiency_decision":
            extra = f" mode={ev.get('answer_mode')} s1={ev.get('s1')} s5={ev.get('s5')} overlap={ev.get('overlap')}"
        if event_name == "generation_end":
            extra = f" duration_ms={ev.get('duration_ms')}"
        if event_name == "postprocess_end":
            extra = f" citations={ev.get('citations_count')} followups={ev.get('followups_count')} grounded={ev.get('grounded')}"
        if event_name == "request_end":
            extra = f" duration_ms={ev.get('duration_ms')}"
        print(f"{ts} {event_name}{extra}")

    summary = _summary(events)
    print("\nSummary:")
    for key, value in summary.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
