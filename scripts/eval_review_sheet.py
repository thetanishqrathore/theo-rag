"""Build a human review sheet for selected benchmark cases.

For each case: run it against /api/query, then resolve every citation the
answer uses through /api/evidence and print the answer beside the cited
passages, so a reviewer can check attribution and support claim by claim.
The sheet contains corpus text; write it outside the repository.

Usage:
  python scripts/eval_review_sheet.py --ids v1-clough-001,v1-dean-014 \
      --output ~/theo-rag-data/review/sheet.md [--api-base http://localhost:8000]
  python scripts/eval_review_sheet.py --per-class 1 --output ...   # one case per corpus/class
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "benchmark" / "v1_cases.jsonl"
CLASSES = ("definition", "synthesis", "comparison", "multi_turn", "ambiguity", "refusal", "citation_hard")
CHUNK_REF_RE = re.compile(r"\[chunk:([0-9a-fA-F-]{36})\]")


def _case_class(case: dict) -> str:
    return next((tag for tag in case.get("class_tags", []) if tag in CLASSES), "unknown")


def select_cases(cases: list[dict], ids: list[str], per_class: int) -> list[dict]:
    if ids:
        by_id = {c["id"]: c for c in cases}
        return [by_id[i] for i in ids]
    picked: list[dict] = []
    for corpus in ("clough", "dean"):
        for cls in CLASSES:
            pool = [c for c in cases if c["corpus"] == corpus and _case_class(c) == cls]
            gold_first = sorted(pool, key=lambda c: (c.get("label_tier") != "gold", c["id"]))
            picked.extend(gold_first[:per_class])
    return picked


def run_case(client: httpx.Client, api_base: str, case: dict) -> dict:
    payload = {
        "query": case["query"],
        "corpus": case["corpus"],
        "top_k": case.get("top_k", 5),
        "retrieval_mode": case.get("retrieval_mode"),
        "history": case.get("history") or [],
    }
    started = time.perf_counter()
    response = client.post(f"{api_base}/api/query", json=payload, headers={"X-Session-Id": f"review-{case['id']}"})
    elapsed = time.perf_counter() - started
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    answer = str(body.get("answer") or "")
    evidence: dict[str, dict] = {}
    for chunk_id in dict.fromkeys(CHUNK_REF_RE.findall(answer)):
        ev = client.get(f"{api_base}/api/evidence/{chunk_id}")
        evidence[chunk_id] = ev.json() if ev.status_code == 200 else {"error": f"HTTP {ev.status_code}"}
    return {"case": case, "status": response.status_code, "elapsed_s": elapsed, "body": body, "evidence": evidence}


def render(results: list[dict]) -> str:
    lines = ["# Answer review sheet", ""]
    for result in results:
        case, body = result["case"], result["body"]
        lines += [
            f"## {case['id']} · {case['corpus']} · {_case_class(case)} · expected `{case.get('expected_mode')}`",
            "",
            f"**Q:** {case['query']}",
        ]
        if case.get("history"):
            lines.append(f"**History:** `{json.dumps(case['history'])[:400]}`")
        lines += [
            "",
            f"HTTP {result['status']} · {result['elapsed_s']:.1f}s · grounded={body.get('grounded')} · "
            f"reason={body.get('grounding_reason')} · refusal={body.get('refusal')} · "
            f"cards={len(body.get('citations') or [])}",
            "",
            "**Answer:**",
            "",
            str(body.get("answer") or body)[:6000],
            "",
            "**Cited passages:**",
            "",
        ]
        if not result["evidence"]:
            lines.append("_(no chunk citations in the answer)_")
        for chunk_id, ev in result["evidence"].items():
            meta = ev.get("metadata") or {}
            text = str(ev.get("primary_text") or ev)[:1200]
            label = " · ".join(str(v) for v in (ev.get("label"), meta.get("series"), meta.get("part")) if v)
            lines += [f"- `{chunk_id}` {label}", f"  > {text}".replace("\n", " "), ""]
        lines += ["**Review:** attribution ☐  support ☐  mode ☐  notes:", "", "---", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--ids", default="", help="Comma-separated case ids")
    parser.add_argument("--per-class", type=int, default=1, help="Cases per corpus/class when --ids is empty")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-sec", type=float, default=900.0)
    args = parser.parse_args()

    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = select_cases(cases, [i.strip() for i in args.ids.split(",") if i.strip()], args.per_class)
    results = []
    with httpx.Client(timeout=args.timeout_sec) as client:
        for case in selected:
            print(f"running {case['id']}", flush=True)
            results.append(run_case(client, args.api_base.rstrip("/"), case))
    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(results), encoding="utf-8")
    (out.with_suffix(".json")).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
