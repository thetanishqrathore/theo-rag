# Benchmark V1 Labeling Guide

This guide defines how to label `eval/benchmark/v1_cases.jsonl`.

## Scope
- Total cases: 200
- Corpus split: 100 `clough`, 100 `dean`
- Label split: 90 `gold`, 110 `silver`

## Class Taxonomy
- `definition`
- `synthesis`
- `comparison`
- `multi_turn`
- `ambiguity`
- `refusal`
- `citation_hard`

## Required Fields
- `expected_mode`: `confident` | `narrow` | `refuse`
- `must_refuse`: set `true` for out-of-scope or unsupported requests
- `min_citations`: minimum citations expected for non-refusal answers
- `hierarchy_policy`:
  - `optional`: hierarchy helps, but absence is neutral
  - `expected`: hierarchy metadata likely present; still never hard-fail solely on missing hierarchy
  - `not_applicable`: flat/transcript-style sources where hierarchy is not expected

## Gold Label Rules
- `label_tier` = `gold`
- `gold_anchors` must be non-empty.
- Each anchor is `{"kind":"contains_any","terms":[...]}` and should include concrete theological terms expected in grounded answers.
- Gold cases are used for:
  - refusal correctness
  - answer-mode correctness
  - anchor recall
  - evidence support checks

## Silver Label Rules
- `label_tier` = `silver`
- `gold_anchors` should be empty.
- Silver cases rely on structural quality checks:
  - citation floor
  - sentence citation coverage
  - uncited sentence rate
  - stream final-event contract

## Hierarchy Policy (Non-Penalizing)
- Missing `node_path`/`section_path` never causes a hard failure.
- If hierarchy is present, it is measured as a diagnostic quality signal.
- Transcript-like cases should usually be tagged `not_applicable` or `optional`.

## Refusal Cases
- `must_refuse` must be `true`.
- `expected_mode` must be `refuse`.
- `min_citations` should be `0`.
- Refusal correctness is measured by outcome mode and refusal flag, not citation count.

## Multi-turn Cases
- Include short `history` (2-4 turns max).
- Query should depend on prior context.
- Keep history neutral and deterministic (no private or user-identifying text).

## Source Tags
- `expected_source_tags` is an allow-list of likely tags (diagnostic use).
- Do not hard-fail solely because an expected tag is absent unless coupled with other failures.

## Review Checklist
- IDs are unique and in `v1-{corpus}-{NNN}` format.
- Corpus and tier counts match target.
- No empty query strings.
- No contradictory labels (for example `must_refuse=true` with `expected_mode=confident`).
