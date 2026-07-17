#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=""
cd "$ROOT_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="candidate_${TS}"
RUN_ROOT="$ROOT_DIR/eval/reports/runs"

python "$ROOT_DIR/scripts/eval_suite.py" \
  --api-base "${API_BASE:-http://localhost:8000}" \
  --input "$ROOT_DIR/eval/benchmark/v1_cases.jsonl" \
  --mode both \
  --output-root "$RUN_ROOT" \
  --run-id "$RUN_ID"

CANDIDATE_SUMMARY="$RUN_ROOT/$RUN_ID/summary.json"
CANDIDATE_COPY="$ROOT_DIR/eval/reports/foundation_candidate_${TS}.json"
VERDICT_PATH="$RUN_ROOT/$RUN_ID/gate_verdict.json"
cp "$CANDIDATE_SUMMARY" "$CANDIDATE_COPY"

if [[ $# -gt 0 ]]; then
  BASELINE_SUMMARY="$1"
elif [[ -f "$ROOT_DIR/eval/reports/latest_baseline_summary.txt" ]]; then
  BASELINE_SUMMARY="$(cat "$ROOT_DIR/eval/reports/latest_baseline_summary.txt")"
else
  BASELINE_SUMMARY="$(ls -1t "$ROOT_DIR"/eval/reports/foundation_baseline_*.json 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "${BASELINE_SUMMARY:-}" || ! -f "$BASELINE_SUMMARY" ]]; then
  echo "No baseline summary found. Run scripts/eval_live_baseline.sh first or pass baseline path." >&2
  exit 1
fi

python "$ROOT_DIR/scripts/eval_compare.py" \
  --baseline "$BASELINE_SUMMARY" \
  --candidate "$CANDIDATE_SUMMARY" \
  --output "$VERDICT_PATH" \
  --fail-on-hard

printf 'Candidate summary: %s\n' "$CANDIDATE_SUMMARY"
printf 'Candidate copy: %s\n' "$CANDIDATE_COPY"
printf 'Gate verdict: %s\n' "$VERDICT_PATH"
printf 'Baseline used: %s\n' "$BASELINE_SUMMARY"
