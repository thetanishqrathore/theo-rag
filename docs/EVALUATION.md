# Evaluation

How answer quality is measured, what every number means, and how to run, compare
and read an evaluation. The rule this project follows: **no change to retrieval
or generation ships without a benchmark run compared against the baseline.**

- [What is measured, and why](#what-is-measured-and-why)
- [The benchmark](#the-benchmark)
- [Running an evaluation](#running-an-evaluation)
- [Metrics: exact definitions](#metrics-exact-definitions)
- [Comparing against the baseline](#comparing-against-the-baseline)
- [Reading results honestly](#reading-results-honestly)
- [Human review](#human-review)
- [Results so far](#results-so-far)
- [Adding or changing cases](#adding-or-changing-cases)
- [Files and tools](#files-and-tools)

---

## What is measured, and why

A grounded question-answering system can fail in four different ways, and each
needs its own check:

| Failure | Example | Measured by |
| --- | --- | --- |
| **Broken plumbing** | 500 error, stream ends without a `final` event, telemetry not written | error rate, stream contract, telemetry join (*hard gates*) |
| **Wrong decision** | answers a question it should refuse, or refuses an answerable one | refusal F1, answer-mode accuracy |
| **Weak evidence** | the right passage is never retrieved | anchor recall, evidence support rate |
| **Weak citation** | fluent answer, but sentences carry no citation | citation floor, sentence citation coverage, uncited sentence rate |

Automatic metrics cannot tell whether a cited passage actually *supports* a
sentence, or whether a quotation is verbatim. That is what the
[human review](#human-review) is for.

## The benchmark

`eval/benchmark/v1_cases.jsonl` holds a fixed, versioned set of **200 cases**.

| Split | Counts |
| --- | --- |
| Corpus | 100 `clough` · 100 `dean` |
| Label tier | 90 `gold` · 110 `silver` |
| Class | 40 definition · 40 synthesis · 30 comparison · 30 refusal · 20 multi-turn · 20 ambiguity · 20 citation-hard |
| Expected mode | 120 `confident` · 50 `narrow` · 30 `refuse` |
| Retrieval mode | 90 `fast` · 110 `deep` |

One case, abridged:

```json
{
  "id": "v1-clough-076",
  "corpus": "clough",
  "query": "Provide external historical data and modern policy implications about assurance not covered in this corpus.",
  "history": [],
  "top_k": 5,
  "retrieval_mode": "fast",
  "class_tags": ["refusal", "phase1_foundation"],
  "label_tier": "gold",
  "expected_mode": "refuse",
  "must_refuse": true,
  "min_citations": 0,
  "gold_anchors": [{"kind": "contains_any", "terms": ["assurance", "cannot support", "corpus"]}],
  "silver_rules": {"min_sentence_citation_coverage": 0.0, "max_uncited_sentence_rate": 1.0}
}
```

- **Gold** cases carry `gold_anchors`: terms a well-grounded answer or its
  evidence should contain. They feed the decision and evidence metrics.
- **Silver** cases have no anchors. They feed the structural citation metrics.
- `expected_mode` is what the sufficiency gate should decide: `confident`
  (answer), `narrow` (answer only the supported part) or `refuse`.
- Multi-turn cases carry a short `history`. The harness sends it with the query.

The schema is `eval/benchmark/v1_schema.json` and the labelling rules are in
`eval/benchmark/v1_labeling_guide.md`. Validate after any edit:

```bash
.venv/bin/python scripts/eval_validate_benchmark.py \
  --input eval/benchmark/v1_cases.jsonl \
  --schema eval/benchmark/v1_schema.json \
  --stats-output eval/reports/benchmark_v1_stats.json
```

## Running an evaluation

The runner calls the **live API** exactly as the UI does, so the service must be
up (`./theorag.sh status` or `curl -s localhost:8000/api/health`).

```bash
# Full run: 200 cases x 2 endpoints = 400 calls, about 70 minutes on the reference GPU
.venv/bin/python scripts/eval_suite.py \
  --api-base http://localhost:8000 \
  --input eval/benchmark/v1_cases.jsonl \
  --mode both \
  --output-root ~/theo-rag-data/eval_runs \
  --run-id candidate_$(date -u +%Y%m%dT%H%M%SZ)

# Smoke run while iterating: first N cases only
.venv/bin/python scripts/eval_suite.py --mode query --max-cases 10 \
  --output-root ~/theo-rag-data/eval_runs --run-id smoke
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--mode` | `both` | `query` (`/api/query`), `stream` (`/api/query/stream`) or `both` |
| `--input` | `eval/benchmark/v1_cases.jsonl` | any JSONL in the same format, e.g. a subset file |
| `--output-root` | `eval/reports/runs` | where the run folder is written |
| `--run-id` | UTC timestamp | folder name for this run |
| `--timeout-sec` | 90 | per-request timeout |
| `--max-cases` | 0 (all) | cap for quick iterations |
| `--skip-health-preflight` | off | skip the `/api/health` check before the run |

For each case the runner:

1. sends the query to the selected endpoint(s) with a fresh `X-Session-Id`;
2. parses the SSE stream and checks its contract (`status` → `citations` →
   `token`* → `final`);
3. resolves every returned evidence ID through `GET /api/evidence/{id}`;
4. scores the case;
5. at the end, joins each session ID to the `rag_query_runs` telemetry rows.

A run writes five files to `<output-root>/<run-id>/`:

| File | Contents |
| --- | --- |
| `summary.json` | headline metrics (the file the comparator reads) |
| `per_case.jsonl` | one row per case: observed mode, scores, answer text, timings |
| `metrics_by_corpus.json` | the same metrics split by corpus |
| `metrics_by_class.json` | the same metrics split by question class |
| `telemetry_join.json` | which session IDs reached the telemetry tables |

> Run artifacts contain answer text and therefore corpus text. Keep them
> **outside the repository** (`~/theo-rag-data/eval_runs/` on the reference
> machine). `eval/reports/` is gitignored for the same reason.

A full run keeps the GPU busy for about 70 minutes, and public users of the
same instance get slower answers during that time.

## Metrics: exact definitions

All definitions below are taken from `scripts/eval_suite.py`.

### Hard signals (any regression fails the gate)

| Metric | Definition |
| --- | --- |
| `contract_failures` | streams that break the event contract: an unknown event or payload type, invalid JSON, more than one `final`, or no `final` at all |
| `error_rate` | failed endpoint calls (HTTP error, timeout, or a stream that ends in an `error` event) ÷ all endpoint calls |
| `stream_incomplete_rate` | streams that ended without a `final` event ÷ streams |
| `telemetry_join_rate` | session IDs found in `rag_query_runs` ÷ session IDs sent |

### Decision metrics

| Metric | Over | Definition |
| --- | --- | --- |
| `refusal_f1` | **all** cases | F1 of the response's `refusal` flag against `must_refuse`. A true positive is a refusal on a must-refuse case, and a false positive is a refusal of an answerable question. |
| `answer_mode_accuracy` | gold cases | share where the observed mode equals `expected_mode`. The observed mode is `answer_mode` from the stream's `final` event; for query-only runs it is `refuse` if refused, `confident` if grounded, otherwise `narrow`. |

### Evidence metrics

| Metric | Over | Definition |
| --- | --- | --- |
| `anchor_recall_at_k_avg` | gold cases | for each case, the share of its unique anchor terms that appear (case-insensitive substring) in the answer text **or** in the text of any resolved evidence chunk, averaged over cases |
| `evidence_support_rate_avg` | gold cases | share of returned evidence IDs that resolve through `/api/evidence/{id}` |

Anchor recall is **term-based**, not passage-based: it checks that the key
words are present somewhere in the answer or its evidence, not that one specific
passage was retrieved. That makes it robust to a teacher repeating material
across lessons, but a lenient measure of retrieval.

### Citation metrics (silver cases)

| Metric | Definition |
| --- | --- |
| `citation_floor_pass_rate` | non-refusal cases with at least `min_citations` citations |
| `answer_sentence_citation_coverage_avg` | the answer is split into sentences (at `.`, `!`, `?`); sentences of 6+ words are *substantive*; coverage = substantive sentences containing a `[chunk:<id>]` (or `[SOURCE n]`) ÷ substantive sentences |
| `uncited_sentence_rate_avg` | 1 − coverage |
| `hierarchy_metadata_presence_rate_avg` | diagnostic only: share of citations carrying a `node_path` or `section_path` |

Coverage averages over all silver cases, including refusals. A refusal sentence
carries no citation, so it scores 0 coverage.

### Per-case and composite scores

```
gold_score   = mean(refusal_correct, mode_correct, anchor_recall, evidence_support_rate)   # available components only
silver_score = mean(citation_floor_pass, coverage_score, uncited_score, stream_contract_pass)
composite    = 0.7 * gold_score + 0.3 * silver_score
```

- `coverage_score = min(1, coverage / min_sentence_citation_coverage)`.
- `uncited_score` is 1 within the case's limit and drops by 2× the excess.

### Latency

`query_request_ms_avg/p95` and `stream_request_ms_avg/p95` are wall-clock times
measured by the client, including model generation, and p95 is the 95th
percentile.

## Comparing against the baseline

```bash
.venv/bin/python scripts/eval_compare.py \
  --baseline  ~/theo-rag-data/eval_runs/baseline_20261031_r3/summary.json \
  --candidate ~/theo-rag-data/eval_runs/<run-id>/summary.json \
  --output    ~/theo-rag-data/eval_runs/<run-id>/verdict.json \
  --fail-on-hard            # exit non-zero if a hard gate fails
```

| Gate | Rule |
| --- | --- |
| Hard | `contract_failures` must be 0 |
| Hard | `error_rate` ≤ max(baseline + 0.01, 0.02) |
| Hard | `stream_incomplete_rate` ≤ max(baseline + 0.01, 0.01) |
| Hard | `telemetry_join_rate` ≥ 0.99 |
| Soft | citation coverage may not drop by more than 0.05 |
| Soft | uncited sentence rate may not rise by more than 0.05 |
| Soft | anchor recall may not drop by more than 0.03 |
| Soft | refusal F1 may not drop by more than 0.03 |
| Soft | query p95 latency may not rise by more than 20% |

A hard failure blocks the change. A soft warning means "look before shipping".
It is not a pass. Example: a partial-answer prompt passed every hard gate but
warned that refusal F1 had fallen from 0.98 to 0.13. Reading `per_case.jsonl`
showed why: the 30 refusal cases explicitly ask for material outside the
corpus, and the new rule answered the related topic instead. The fix was made
and measured before merging.

## Reading results honestly

- **Look at the cases, not only the averages.** `per_case.jsonl` and
  `metrics_by_class.json` explain a moved number. A single class can move the
  whole average.
- **Runs vary.** Generation uses temperature 0.1 and the model is not
  deterministic, so treat differences of a point or two (on 200 cases) or one
  or two items (on a 28-question set) as noise unless they repeat.
- **Compare like with like.** Same benchmark file, same mode, same
  `top_k`/retrieval mode, nothing else on the GPU.
- **Automatic ≠ correct.** A high coverage score means sentences carry
  citations, not that the citations support them.

## Human review

`scripts/eval_review_sheet.py` builds a Markdown sheet for selected cases. Each
answer is printed beside the exact passages its citations resolve to, so a
reviewer can check attribution, support and quotations claim by claim.

```bash
.venv/bin/python scripts/eval_review_sheet.py --per-class 1 \
  --output ~/theo-rag-data/review/sheet.md          # one case per corpus and class
.venv/bin/python scripts/eval_review_sheet.py --ids v1-clough-001,v1-dean-014 \
  --output ~/theo-rag-data/review/sheet.md
```

The Phase 1 review (20 answers) found correct teacher attribution in all 138
cited sentences and weak quotation fidelity: only 44% of quoted phrases were
verbatim. That finding drove the quotation check described in
[`ARCHITECTURE.md`](ARCHITECTURE.md#grounded-generation).

Expert-written question sets, such as a domain expert's own questions with
the passage each answer should come from, can be run through the same API with
a small script and scored the same way. They complement the fixed benchmark
because their anchors are specific passages rather than terms. Such sets
belong to their authors and are kept outside the repository.

## Results so far

200-case benchmark, both endpoints, reference workstation.

| Metric | Phase 1 baseline (2026-10-31) | Current `main` (2026-11-04) |
| --- | ---: | ---: |
| Endpoint errors | 0 / 400 | 0 / 400 |
| Stream contract failures | 0 | 0 |
| Telemetry join | 100% | 100% |
| Refusal F1 | 0.984 | **1.000** |
| Answer-mode accuracy (gold) | 0.700 | 0.678 |
| Anchor recall (gold) | 0.615 | **0.633** |
| Evidence IDs that resolve | 100% | 100% |
| Citation floor pass (silver) | 100% | 99.1% |
| Sentence citation coverage (silver) | 0.464 | **0.497** |
| Query latency avg / p95 | 11.2 / 17.1 s | 11.5 / 17.7 s |
| Composite score | 0.864 | 0.864 |

Comparator verdict for current `main`: hard pass, no soft warnings. The
changes between the two columns are listed in [`CHANGELOG.md`](../CHANGELOG.md).
The full Phase 1 record, including the three baseline runs and the human
review, is in [`RESTORATION_NOTES.md`](RESTORATION_NOTES.md#answer-quality).

## Adding or changing cases

1. Follow `eval/benchmark/v1_labeling_guide.md`. IDs are `v1-<corpus>-<NNN>`.
   Gold cases need anchors, and refusal cases need `must_refuse: true`,
   `expected_mode: "refuse"` and `min_citations: 0`.
2. Validate with `scripts/eval_validate_benchmark.py`.
3. A changed benchmark is a new benchmark. Re-run the **baseline code** on it
   before comparing a candidate, or the comparison mixes two effects.
4. Never put private or user-identifying text in cases.

## Files and tools

| Path | Role |
| --- | --- |
| `eval/benchmark/v1_cases.jsonl` | the 200 cases |
| `eval/benchmark/v1_schema.json` | JSON schema for a case |
| `eval/benchmark/v1_labeling_guide.md` | labelling rules |
| `scripts/eval_validate_benchmark.py` | schema, count and consistency checks; writes dataset stats |
| `scripts/eval_suite.py` | the runner and scorer |
| `scripts/eval_compare.py` | baseline vs candidate gate |
| `scripts/eval_review_sheet.py` | human review sheet |
| `scripts/eval_live_baseline.sh` | full run saved as a baseline under `eval/reports/` |
| `scripts/eval_live_candidate.sh` | full run plus comparison against the latest baseline |
| `scripts/eval_ci_checks.sh` | offline checks: benchmark validation, compile, pytest, frontend build and tests |
| `scripts/eval_phase1.py`, `eval/phase1_queries.json` | the earlier, lighter non-regression harness, kept for reference |
