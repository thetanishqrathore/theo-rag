#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=""
cd "$ROOT_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="baseline_${TS}"
RUN_ROOT="$ROOT_DIR/eval/reports/runs"

python "$ROOT_DIR/scripts/eval_suite.py" \
  --api-base "${API_BASE:-http://localhost:8000}" \
  --input "$ROOT_DIR/eval/benchmark/v1_cases.jsonl" \
  --mode both \
  --output-root "$RUN_ROOT" \
  --run-id "$RUN_ID"

SUMMARY_PATH="$RUN_ROOT/$RUN_ID/summary.json"
BASELINE_COPY="$ROOT_DIR/eval/reports/foundation_baseline_${TS}.json"
cp "$SUMMARY_PATH" "$BASELINE_COPY"
printf '%s\n' "$SUMMARY_PATH" > "$ROOT_DIR/eval/reports/latest_baseline_summary.txt"

printf 'Baseline summary: %s\n' "$SUMMARY_PATH"
printf 'Baseline copy: %s\n' "$BASELINE_COPY"
