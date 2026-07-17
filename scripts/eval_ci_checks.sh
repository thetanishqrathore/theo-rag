#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=""
cd "$ROOT_DIR"

python scripts/eval_validate_benchmark.py \
  --input eval/benchmark/v1_cases.jsonl \
  --schema eval/benchmark/v1_schema.json \
  --stats-output eval/reports/benchmark_v1_stats.json

python -m compileall src
pytest -q
npm --prefix frontend run build
npm --prefix frontend run test
