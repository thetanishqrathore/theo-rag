# Evaluation Foundation (Phase 1)

This folder contains the evaluation foundation used to assess quality and reliability before Phase 2 tuning.

## Benchmark Dataset

- Schema: `eval/benchmark/v1_schema.json`
- Cases: `eval/benchmark/v1_cases.jsonl`
- Labeling guide: `eval/benchmark/v1_labeling_guide.md`

### Fixed benchmark targets
- 200 total cases
- 100 `clough` + 100 `dean`
- 90 `gold` + 110 `silver`

### Hierarchy policy
Hierarchy metadata is optional in scoring:
- if present, measured as a diagnostic signal
- if absent, never penalized

## Validation

Validate benchmark integrity and emit stats:

```bash
python scripts/eval_validate_benchmark.py \
  --input eval/benchmark/v1_cases.jsonl \
  --schema eval/benchmark/v1_schema.json \
  --stats-output eval/reports/benchmark_v1_stats.json
```

## Unified Eval Runner

Runner: `scripts/eval_suite.py`

### Features
- evaluates `/api/query`, `/api/query/stream`, or both
- runs a `/api/health` preflight and fails fast if the stack is not ready (override with `--skip-health-preflight`)
- validates stream contract (`status`, `citations`, `token`, `final`)
- fetches `/api/evidence/{evidence_id}` for citation fidelity checks
- computes gold/silver/composite scores
- correlates telemetry rows by `X-Session-Id -> user_session_id`
- writes full artifact bundle under `/eval/reports/runs/<run_id>/`

### Usage

```bash
python scripts/eval_suite.py \
  --api-base http://localhost:8000 \
  --input eval/benchmark/v1_cases.jsonl \
  --mode both
```

## Scoring model

- Gold components: refusal correctness, answer-mode correctness, anchor recall@k, evidence support rate
- Silver components: citation floor, sentence citation coverage, uncited sentence rate, stream contract pass
- Composite score: `0.7 * gold + 0.3 * silver`

## Gate Comparator

Comparator: `scripts/eval_compare.py`

Hard gates (fail build/deploy):
- contract failures > 0
- error-rate regression above cap
- stream incomplete-rate regression above cap
- telemetry join completeness < 99%

Soft warnings (non-blocking):
- citation coverage drop
- uncited sentence rate increase
- anchor recall drop
- refusal F1 drop
- query p95 latency increase

### Usage

```bash
python scripts/eval_compare.py \
  --baseline /path/to/baseline_summary.json \
  --candidate /path/to/candidate_summary.json \
  --output /path/to/gate_verdict.json \
  --fail-on-hard
```

## Deterministic CI checks

Script: `scripts/eval_ci_checks.sh`

Runs:
- benchmark validation
- backend compile check
- pytest
- frontend build
- frontend test

## Live VM scripts

- Baseline: `scripts/eval_live_baseline.sh`
- Candidate + compare: `scripts/eval_live_candidate.sh`

Both scripts produce timestamped report bundles and keep baseline/candidate summary snapshots in `/eval/reports/`.
