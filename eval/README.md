# Evaluation data

This folder holds the fixed benchmark used to measure every change:

| File | Contents |
| --- | --- |
| `benchmark/v1_cases.jsonl` | 200 cases: 100 `clough` + 100 `dean`, 90 `gold` + 110 `silver` |
| `benchmark/v1_schema.json` | JSON schema for one case |
| `benchmark/v1_labeling_guide.md` | how to label a case |
| `phase1_queries.json` | queries for the earlier, lighter harness (`scripts/eval_phase1.py`) |
| `reports/` | local run outputs (gitignored: they contain corpus text) |

How to run the benchmark, what each metric means and how to compare runs
against the baseline: [`docs/EVALUATION.md`](../docs/EVALUATION.md).

Quick start:

```bash
.venv/bin/python scripts/eval_suite.py --api-base http://localhost:8000 \
  --input eval/benchmark/v1_cases.jsonl --mode both --output-root ~/theo-rag-data/eval_runs
.venv/bin/python scripts/eval_compare.py --baseline BASELINE/summary.json \
  --candidate CANDIDATE/summary.json --output CANDIDATE/verdict.json --fail-on-hard
```
