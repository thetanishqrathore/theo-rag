# Phase 1 baseline restoration notes

Record of how the Phase 1 system was restored on the reference workstation
from the public release, what was changed and why, and what the first valid
baseline measured. Behaviour that was deliberately **not** changed is listed
too.

## Starting point and references

| Item | Value |
| --- | --- |
| Public release (starting commit) | `theo-rag@68fc9cb512746a797c8ea2babd210e4bec609c1f` |
| Restoration branch | `restore/phase1-baseline` |
| Private development history (read-only reference) | `corpus_rag@9f3d0d58204cb1a9f30fe77481195e00ced0c679` (2026-02-26) |
| Private commits consulted | `4fd181d` dean_v4_marker (Marker CLI usage), `833b2fa`/`a9182ab` course_notes, `50ff0a9` phase_1_final_processing, `f90b5ec` bible_html_bug |

The private history confirmed that the public tree is a cleaned export of the
same application. No runtime code had to be brought back from it.

## Runtime as restored

| Component | Version / identity |
| --- | --- |
| GPU | NVIDIA RTX PRO 6000 Blackwell Max-Q (97.9 GB), driver 595.99.02 |
| Generation | Ollama 0.40.13, `gpt-oss:120b` digest `714d86958b6e870a9320e78126ac1ce3477727a1cd9bf7194b8ace0d84c62fd8` (MXFP4, 65.4 GB), 32,768 ctx, temperature 0.1, no secondary model |
| Embeddings / rerank | `BAAI/bge-m3` (1,024-d), `BAAI/bge-reranker-large` (unchanged) |
| Python stack | Python 3.12.3, torch 2.15.0+cu132, sentence-transformers 6.2.0, docling 2.87.0, PyMuPDF 1.23.8, FastAPI 0.104.1, qdrant-client 1.7.0, ollama-python 0.1.6 (all in `uv.lock`) |
| Extraction tools | LibreOffice 24.2.7 (user-space install), Marker 1.10.2 |
| Infrastructure | `pgvector/pgvector:pg16@sha256:7b822b0a…`, `qdrant/qdrant:v1.7.0` |
| Frontend | Node 22.21.1, lockfile regenerated (see below) |

## Defects found and repaired

| Area | Defect | Fix |
| --- | --- | --- |
| Setup | Frontend `package-lock.json` lacked the test dependencies (in both repositories), so `npm ci` failed; `jsdom` (Vitest's environment) was never declared; jsdom < 27 has no `PointerEvent`, so the carousel drag test failed | Lockfile synced, `jsdom@^27` added, Node engine range declared |
| Setup | `ModelSelector` read `filtered` before declaration and set state inside effects (lint errors) | State updates moved into the open/query handlers |
| Setup | Tests used `ROOT = Path("")`; eval wrappers used `ROOT_DIR=""` (paths became `/scripts/...`) | Roots derived from file location |
| Setup | Marker 2.x routes layout inference through a vLLM container and fails with the CLI flags the extractor uses | Marker pinned to 1.x |
| Database | Schema bootstrap was documented twice; migration 008 failed on any schema created from the current `init.sql` (it indexes what is now a compatibility view); 010/011 add constraints `init.sql` lacks | Single path: `init.sql` once, then `scripts/migrate.py` (ledger + checksums, idempotent, `--check` against ORM); 008 guarded |
| Operations | `reset.sh` deleted all Docker volumes by default and stopped at canonical HTML | Non-destructive `start`/`rebuild`; `destroy` needs an explicit flag |
| Processing | Embedding model reloaded for every lesson | Cached per process |
| Processing | Parity checks compared counts only | Chunk-ID parity (`verify_qdrant_id_parity`) |
| Processing | Course notes volume 06: the top-scoring engine (PyMuPDF) kept running page headers ("Lesson 159 Page 17 Part VI", "Page ii"), so validation failed | Selection measures page-marker leakage exactly as validation does (derived paragraphs, any "Page ") and skips a leaking top candidate (Docling selected) |
| Processing | Dean semantic HTML extraction kept inline `<script>` as text (overLIB library became a 5,660-word paragraph in `Feasts_Dean_VV.htm`) | `<script>` stripped before parsing (`<style>` kept: depths come from its classes) |
| Validation | Duplicate check counted short lines: outline headings repeated in the body (19 Chafer papers) and slide titles failed as "duplicate content" although no paragraph > 12 words repeated | Only substantive paragraphs (> 12 words) count |
| Validation | Copyright-footer exemption required a space after the symbol ("©Biblical Awareness Ministries, 1988") | Optional whitespace |
| Ingestion | Topical "book" was the first folder under `topical_studies`; this corpus groups topics by format (`html/<Topic>/`), so all HTML topics shared one book and ~60 HTML plus most PDF topical sources failed on duplicate lesson numbers | Topic folder is the book; topic-first layouts unchanged |
| Ingestion | Unnumbered single-document topical HTML studies were rejected (DOC/PDF ingesters already sequence them) | Sequenced within the topic; Bible-book HTML still requires a number |
| Ingestion | Dean PDF ingestion ignored `DEAN_LESSON_OVERRIDES` | Honoured, same keys as HTML |
| Generation | Ollama reloads the model whenever `num_ctx` changes (~7 s); helper calls used 2048/4096 and warmup used 1, so each query reloaded GPT-OSS repeatedly | Calls to the primary model reuse `OLLAMA_NUM_CTX` |
| Generation | Warmup prompt made the reasoning model answer in full | `num_predict: 1` |
| Generation | GPT-OSS sometimes cites `[chunk:<first 8 hex>]` or uses U+2011 hyphens in IDs; such answers were marked ungrounded | Unambiguous refs resolved to the provided evidence ID; logged |
| Retrieval | Model-written rewrites contain U+2011 / narrow no-break spaces, which miss ASCII corpus text in lexical search | Folded to ASCII before retrieval |
| Streaming | Long reasoning phases emit no answer tokens | Rate-limited `status` heartbeats; reasoning never reaches answers, citations or telemetry |
| Frontend | Canonical `[chunk:<id>]` tokens were wrapped again as `[[chunk:<id>]]`: citation chips rendered inside literal brackets, and citations without a source card left `[]` | Bare-token normalizers skip canonical tokens |
| Frontend | Citation post-processing collapsed all whitespace, including newlines, so headings/lists/paragraphs ran together | Only horizontal whitespace collapsed; `"  \n"` hard breaks kept |
| Docs | README claimed answers are produced "without hallucinating" | Stated precisely what `grounded` means |

## Corpus decisions (please review)

Every file is listed in `source_manifest.json` with its reason. Decisions that
involved judgement:

1. **Clough transcripts**: the 227 original DOC files are canonical. The 227 PDF
   renditions and the July 2026 merged/split copies are excluded (alternate format
   / derived). `bf_Intro_to_transcripts.doc` is a 204-word transcriber's note and
   is excluded.
2. **Clough intro lessons**: `01..03-bf-intro-00N` are Part 1 lessons with no
   chapter. They carry lesson numbers 1–3 and are distinct from Part 2 lessons 1–3.
3. **Course notes 01** is a 3-page cover sheet (~315 words, one rationale
   paragraph) and is excluded. Volumes 02–06 are included.
4. **Exact duplicates**: 23 Dean Bible-book lessons also filed under topical
   series (Worship & Music, Ascension). The Bible-book copy is kept for its
   book/lesson metadata.
5. **Near-identical re-exports** (not byte-identical; 5-gram Jaccard ≥ 0.99):
   `1999-Galatians-063` (kept `1998-Galatians-063`; both are Lesson #063,
   Aug 29 1999), `Heb_152` (kept `Heb152`), `2015-Samuel-065c` (kept `065b`).
6. **Placeholders and index pages**: six 1 Corinthians pages reading only "This
   lesson is not available"; `Jude.html` (series index); `SCRIPTURE_INDEX_NEW.htm`.
7. **John lesson numbers**: `Jno015.htm` and `Jno051.htm` are distinct lessons
   whose filenames collide with `1998-John-015/051`. Their numbers are
   **inferred from passage order**: `Jno015` = John 1:19-28 → **14** (between 013,
   which moves into 1:19-28, and the topical 015); `Jno051` = John 6:51-59 →
   **52** (between 051 at 6:41-51 and 053 at 7:1-9). Both slots were gaps in the
   1998 series. Set via `DEAN_LESSON_OVERRIDES`.
   **Israel topic**: the folder holds `Israel_01..10.htm`, the unnumbered
   `Feasts_Dean_VV.htm`, and a separate 3-part 2017 "Israel Special" PDF series
   whose parts 1-3 collide with `Israel_01..03`. All are kept, with Feasts → 11
   and Israel Special parts 1-3 → 12-14.
8. **Reviewed validation exceptions** (`VALIDATION_ACCEPTED_EXCEPTIONS`): eight
   genuine short sources whose text is fully extracted but below the minimums
   (`1Jno51`, `2002-2John-014`, `1998-Galatians-019`, `1998-James-019`,
   `2004-Revelation-059b`, `2004-Revelation-174a`, `2003-Genesis-062`: 73–613 words;
   `Propitiation.doc`: 265 of 277 source words) accept `minimum_content`;
   `2008-Chafer-HistoryOfDoctrine-Paper-026.pdf` accepts `heading_list_coherence`
   (6 heading-depth jumps from Marker; text correct). All other checks apply.
   **Provenance caveat:** the seven Bible-book lessons were validated during the
   clean build under an earlier, broader setting (`dean_bible_html_v2` minimums
   lowered to 2 blocks / 60 words). That setting was replaced by the per-file
   exceptions before the retry run, so their saved validation reports show an
   ordinary pass rather than an accepted exception. See open items.

## Known limitations (unchanged behaviour)

- Dean topical PDF titles come from the first heading-like line (e.g.
  `Walking.pdf` is titled "1. Lexical" rather than "The Doctrine of Walking").
- Dean RTF topical lessons are speech-to-text transcripts without paragraph
  breaks, so their parent chunks exceed the 600-word target (~830 words); child
  chunks are unaffected.
- Lexical retrieval is PostgreSQL full-text search (`ts_rank_cd`), not BM25.
- The sufficiency decision is heuristic, and the citation guardrail is log-only.
- Optional retrieval experiments (RRF fusion, OR-fallback, child dedup, stable
  tie-break) stay disabled.
- Each query targets one corpus; there is no combined Clough+Dean search.
- At most 3 source cards are returned per answer (`max_cards=3`). An answer
  may cite further evidence it was given; those citations are valid but have
  no card, so the UI shows no chip for them.

## Build and baseline results

### Corpus build

| Run | Scope | Outcome |
| --- | --- | --- |
| `build_20261031_080749` | 9-source pilot (both corpora, every format) | 8 embedded, exact ID parity; course notes 01 rejected (cover sheet, later excluded) |
| `build_20261031_082311` | First full build | Stopped deliberately after the topical book-resolution defect surfaced (~100 topical ingest failures); no summary file |
| `build_20261031_085613` | Clean rebuild from empty volumes, 2,502 sources | 2,478 embedded, 24 validation failures (all diagnosed above) |
| `build_20261031_093442` | `--retry-failed` after the fixes | 24/24 embedded; 0 incomplete; exit 0 |

Final state: 2,502 included sources (of 2,812 files; 310 excluded with
recorded reasons) → 2,502 lessons `embedded`, 22,477 parent chunks, 95,382
child chunks with 1,024-d embeddings. Qdrant matches PostgreSQL by chunk ID
exactly: `clough_chunks` 21,878, `dean_chunks` 73,504 (0 missing, 0 extra).

### Extraction coverage audit

Extracted paragraph words per lesson compared with the words in its own
source (HTML text with scripts/styles removed, PDF text layer, LibreOffice
text conversion for DOC/RTF):

| Tag | Lessons | Median | Minimum |
| --- | ---: | ---: | ---: |
| transcripts | 227 | 0.995 | 0.989 |
| course_notes | 5 | 0.954 | 0.946 |
| dean_bible_books | 2,072 | 0.996 | 0.929 |
| dean_topical_html | 134 | 0.997 | 0.951 |
| dean_topical_pdfs | 42 | 0.982 | 0.962 |
| dean_topical_doc | 10 | 0.992 | 0.957 |
| dean_topical_rtf | 12 | 1.000 | 1.000 |

No lesson is below 0.929. The one value above 1.15 (`2017-IsraelSpecial-…-003.pdf`,
1.15) comes from Marker OCR of its 115 slide images. Clough transcripts total
2.27M extracted words against ~2.37M recorded in the July 2026 merge manifests.
The gap is headers, footers and page numbers; this was spot-checked, not proven
for every file. Metadata (titles, dates, lesson numbers) was not audited
exhaustively.

### Service and recovery

- systemd user unit enabled with lingering; `kill -9` of the API restarted it
  automatically and it returned healthy. Boot start not exercised (no reboot).
- Backup `20261031_094327` (1.4 GB) restored into scratch storage with matching
  table counts, Qdrant point counts and 2,502 raw + 2,502 canonical files. The
  verifier compared against the live system; see open items.

### Answer quality

Not yet measured. The 200-case benchmark (both endpoints) and the 20-answer
expert review are pending; until then this is a restored system, not a
validated baseline.

## Open items (from review, 2026-10-31)

1. Backup verification must fail on checksum errors and verify a backup against
   its own recorded inventory, not the live system; original corpus (all 2,812
   files), configuration and service files need an independent copy.
2. Citations beyond the three source cards are dropped from the user-facing
   answer; every cited source should resolve and display.
3. Clough intro lessons (Part 1, no chapter) lose series/part attribution.
   Inferred lesson numbers (John, Israel) should be marked as inferred.
4. Build/validation runs should record Git SHA, manifest digest, effective
   configuration and tool/model identities; the resumable build should detect
   changed or retired sources and stale outputs.
5. Migration SQL and its ledger row should commit atomically.
6. Browser chat history is capped and not backed up; application logs are not
   rotated.
