# Changelog

Each entry says what changed, why, and what it did to the measured numbers.
Benchmark figures come from the 200-case suite in
[`docs/EVALUATION.md`](docs/EVALUATION.md), compared against the
`phase1-baseline` run.

## 2026-11-06 — Phase 2, step 1: retrieval and answer fidelity

### Keyword search rebuilt (#3)

The keyword half of hybrid search almost never fired. It AND-ed every word of
the LLM-rewritten question, so 96% of benchmark questions returned no keyword
hits. It is now built from the user's own question:

- teacher names, question-framing words and terms found in more than 5% of
  passages are dropped;
- each remaining term is weighted by its rarity (IDF);
- passages are ranked by the summed weight of the terms they contain.

Per-term GIN index lookups keep a search at about 10 ms. Term statistics load
once at startup (about 6 s). Rollback: `RAG_KEYWORD_QUERY_MODE=legacy`.

### Partial answers instead of flat refusals (#3)

When a question reaches beyond the sources, but the sources address the related
subject, the answer now says plainly what is not covered and then gives what the
teacher does teach, with citations. The exact refusal is kept for two cases:
nothing relevant exists, or the question explicitly asks for material outside
the corpus. The second case is detected before generation. The prompt also
forbids attributing to the teacher any name or detail that is absent from the
sources. Rollback: `RAG_PARTIAL_ANSWERS_ENABLED=false`.

The first benchmark run of this rule caught a regression: refusal F1 fell from
0.98 to 0.13, because the model answered the related topic of explicit
out-of-corpus requests. The pre-generation check fixed it, and refusal F1 is
now 1.00.

### Quotation check (#3)

The Phase 1 expert review found that only 44% of quoted phrases were verbatim.
Quotation marks are now allowed only around exact source words. After
generation, any quote of 4 or more words that appears in no source passage
loses its quotation marks: the words stay, but they are no longer presented
as the teacher's. Rollback: `RAG_QUOTE_VERIFICATION_ENABLED=false`.

### Whole-system control script (#2)

`./theorag.sh start|stop|restart|status|logs|boot` starts and stops the
containers, the API and the tunnel in dependency order, and frees GPU memory
on stop.

### Measured effect

| Metric | Baseline | After |
| --- | ---: | ---: |
| Refusal F1 | 0.984 | 1.000 |
| Anchor recall (gold) | 0.615 | 0.633 |
| Sentence citation coverage (silver) | 0.464 | 0.497 |
| Answer-mode accuracy (gold) | 0.700 | 0.678 |
| Query latency p95 | 17.1 s | 17.7 s |
| Endpoint errors | 0 | 0 |

Comparator verdict: hard pass, no soft warnings. On a separate, expert-written
question set with passage-level anchors (28 questions, kept outside the
repository), the expected passage was cited for 15 of 33 anchors, up from 12. Quotes not found in the cited
passages fell from 6 to 2.

**Known limits:**
- On questions about topics the corpus does not cover, the choice between a
  partial answer and a refusal varies from run to run.
- Some lessons were indexed with a wrong character encoding or with hidden
  word-processor text. They need a parser fix and a rebuild, which is the next
  step.

## 2026-11-01 — Phase 1 baseline (`phase1-baseline`, #1)

The system was restored from its public release and made reproducible:

- a locked Python environment;
- a single migration path;
- an auditable source manifest;
- a resumable, provenance-stamped corpus build: 2,502 sources, 95,382
  passages, exact PostgreSQL/Qdrant ID parity;
- boot-time services;
- self-verifying backups;
- GPT-OSS 120B as the single local model.

Thirty-one defects were fixed across setup, ingestion, validation, generation,
citations and the evaluation harness itself. The full record, with corpus
decisions and baseline measurements, is in
[`docs/RESTORATION_NOTES.md`](docs/RESTORATION_NOTES.md).

## 2026-07-17 — Initial public release

Cleaned export of the original application: hybrid retrieval, parent/child
chunking, sufficiency-gated generation, typed SSE streaming and the 200-case
benchmark.
