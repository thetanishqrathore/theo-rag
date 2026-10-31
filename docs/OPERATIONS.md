# Operations runbook

Single-workstation deployment: PostgreSQL + Qdrant in Docker (localhost only),
Ollama as a system service, and one FastAPI process that serves both the API and
the built web UI on port 8000. Commands assume the repository root.

## 1. One-time setup

| Requirement | Reference version | Notes |
| --- | --- | --- |
| Python | 3.12 via [uv](https://docs.astral.sh/uv/) | `uv sync --locked --extra dev` creates `.venv` from `uv.lock` |
| Node.js | 22 (`frontend/.nvmrc`) | Vite 7 needs `^20.19.0 \|\| >=22.12.0` |
| Docker + Compose | Compose v2+ | user must be in the `docker` group |
| Ollama | 0.40.x | `ollama pull gpt-oss:120b` (~65 GB; MXFP4) |
| LibreOffice | 24.2 (`soffice`) | `sudo apt install libreoffice-writer-nogui`, or without root `scripts/setup/install_libreoffice_userspace.sh` |
| Marker | **1.x** (`marker-pdf<2`) | `uv tool install "marker-pdf<2"`. Marker 2.x moved layout inference to a vLLM container and does not run with the CLI flags used here |
| GPU | ~95 GB VRAM peak during a full build | GPT-OSS 120B ≈ 60 GiB at 32k context; embedder + reranker ≈ 4 GB; build-time Docling/Marker ≈ 10 GB |

```bash
uv sync --locked --extra dev
cp .env.public.example .env            # then set data paths and tool commands
docker compose up -d                   # empty volume => scripts/init.sql runs once
.venv/bin/python scripts/migrate.py    # apply tracked migrations (idempotent)
.venv/bin/python scripts/migrate.py --check
```

Working data (raw copies, canonical HTML, logs, manifests, build reports,
backups) lives **outside the checkout** under the parent of `RAW_DATA_PATH`
(reference: `~/theo-rag-data`).

## 2. Building the corpus

```bash
# 1. Inventory every source file with include/exclude reasons.
.venv/bin/python scripts/source_manifest.py \
  --root "/path/to/bible_corpus" --output ~/theo-rag-data/source_manifest.json

# 2. Build (resumable). Re-run the same command after any interruption.
.venv/bin/python scripts/build_corpus.py \
  --manifest ~/theo-rag-data/source_manifest.json \
  --report-dir ~/theo-rag-data/build_reports
```

- Progress is driven by lesson status
  (`pending → extracted → structured → validated → chunked → child_chunked → embedded`);
  a failing stage never advances status.
- Per-file/per-stage outcomes: `build_reports/<run_id>.jsonl`; summary:
  `<run_id>_summary.json`.
- Before processing, every selected source is re-hashed against the manifest,
  the database is reconciled with the manifest, embedded lessons are checked
  for their raw copy and canonical HTML, and lessons validated under a
  different processing configuration are counted. After indexing, Qdrant
  points without a PostgreSQL row are removed.
- Exit codes: `0` complete with exact Qdrant ID parity; `1` an included source
  failed, is incomplete, or lost an output; `2` index parity mismatch; `4` the
  manifest no longer matches the files (regenerate it); `5` ingested sources
  are no longer in the manifest (changed, removed or excluded); `6` lessons
  built under another processing config with `--require-current-config`.
- Flags: `--retry-failed` re-runs failed lessons that never reached chunking;
  `--retire-stale` deletes retired sources with their rows, Qdrant points, raw
  copy and canonical HTML; `--repair-outputs` restores missing raw copies and
  canonical HTML; `--revalidate` re-checks embedded lessons and refreshes their
  saved reports without changing status.
- Every run summary records the Git revision, manifest digest, effective
  processing configuration and tool versions; every validation report carries
  a provenance stamp. Reviewed validation exceptions
  (`VALIDATION_ACCEPTED_EXCEPTIONS`) are tied to a source checksum.
- Builds and backups share `~/theo-rag-data/.corpus.lock` and never overlap.
- `scripts/ops/test_build_resilience.sh` exercises an interrupted build,
  edited/removed sources and a deleted output in an isolated scratch
  environment.
- Experiments that change processing should build a separate corpus (its own
  database, Qdrant collections and data root via `DATABASE_URL`, `QDRANT_PORT`,
  `RAW_DATA_PATH`, `CANONICAL_PATH`) and switch only after validation.
- Pilot a subset with `--paths-file pilot.txt` (manifest-relative paths).
- `./reset.sh rebuild` = start infrastructure, migrate, then the build above.

## 3. Service management (systemd user unit)

```bash
scripts/ops/install_service.sh        # builds frontend, writes ~/.config/systemd/user/theo-rag.service
loginctl enable-linger "$USER"         # start at boot without a login session
```

**Whole system at once:** `./theorag.sh` controls every TheoRAG piece in
dependency order. It never touches other containers or the Ollama system
service, and deletes no data.

| Command | Effect |
| --- | --- |
| `./theorag.sh start [--local]` | containers → API (waits for `/api/health`) → tunnel (waits for the public URL); `--local` keeps the public site offline |
| `./theorag.sh stop [--force]` | tunnel → API → containers, then makes sure GPT-OSS is out of GPU memory; refuses while a build or backup holds `.corpus.lock` |
| `./theorag.sh restart [--local]` | stop, then start |
| `./theorag.sh status` | services, containers, health, public URL, GPU; exit 1 if anything is down |
| `./theorag.sh logs [api\|tunnel]` | follow a journal |
| `./theorag.sh boot on\|off` | start at boot, or stay off after a reboot |

Individual units:

| Action | Command |
| --- | --- |
| Status | `systemctl --user status theo-rag` |
| Start / stop / restart | `systemctl --user start\|stop\|restart theo-rag` |
| Logs | `journalctl --user -u theo-rag -f`; structured app log in `$LOG_PATH/api.log`, rotated at 50 MB × 5 (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`) |
| Disable at boot | `systemctl --user disable theo-rag` |

`ExecStartPre` (`scripts/ops/wait_for_deps.sh`) starts the Postgres/Qdrant
containers and waits for Postgres, Qdrant and Ollama. The unit restarts on any
exit (`Restart=always`). Run exactly one uvicorn worker: the GPU models and the
generation semaphore are per process. `RAG_GENERATION_MAX_CONCURRENCY=1`
serializes generations; extra requests get a `busy` error instead of queueing.

Chats are stored only in the browser (IndexedDB, capped at 100 sessions × 400
messages) and are not part of server backups; use **Export chats** / **Import
chats** in the sidebar to keep them.

Access: `http://localhost:8000` and `http://<LAN address>:8000`. The API has no
authentication of its own; do not expose port 8000 beyond a trusted LAN.
Public access at `https://bibleai.raeonlabs.com` runs through a Cloudflare
Tunnel (`theo-rag-tunnel.service`) that publishes only the endpoints the UI
needs; see `docs/cloudflare_tunnel_setup.md`.

## 4. Health checks

```bash
curl -s localhost:8000/api/health      # {"status":"healthy","services":{postgres,qdrant,ollama}}
curl -s localhost:8000/api/stats       # Qdrant points per corpus
curl -s localhost:11434/api/ps         # loaded Ollama models, context length, VRAM
.venv/bin/python -m src.processing index clough --verify   # count parity
```

Smoke query:

```bash
curl -s localhost:8000/api/query -H 'Content-Type: application/json' \
  -d '{"query":"What is the faith-rest drill?","corpus":"clough","top_k":7}' | python3 -m json.tool | head -20
```

The first answer after a cold start includes loading GPT-OSS (~30 s from disk,
~7 s from page cache). Helper calls reuse `OLLAMA_NUM_CTX`, because Ollama
reloads the model whenever the context size changes.

## 5. Backup and restore

```bash
scripts/ops/archive_originals.sh "/path/to/bible_corpus"   # immutable copy of ALL original files (no-op if unchanged)
scripts/ops/backup.sh                                       # -> ~/theo-rag-data/backups/<timestamp>/
scripts/ops/verify_restore.sh ~/theo-rag-data/backups/<timestamp>
scripts/ops/copy_offsite.sh ~/theo-rag-data/backups/<timestamp> /media/<external-drive>/theo-rag
scripts/ops/install_backup_timer.sh 03:30                   # optional nightly backup + verify
```

A backup is self-describing and bound to one corpus revision:

| Item | Contents |
| --- | --- |
| `postgres.dump` | pg_dump custom format |
| `qdrant/*.snapshot` | one snapshot per collection |
| `working_data.tar.gz` + `working_files.sha256` | raw copies, canonical HTML, manifest, build reports, each file hashed |
| `originals/` | all 2,812 original files (`originals.tar`, per-file `originals.sha256`, `REVISION`), hard-linked from `~/theo-rag-data/originals/originals_<revision>/` |
| `config/` | `.env` (contains credentials: keep backups private), compose file, `uv.lock`, systemd unit, `tool_identities.txt` (Git SHA, corpus revision, model digest, image digests, tool versions) |
| `inventory.json` | expected row counts and sorted-ID digests per table, Qdrant config and point-ID digests |
| `SHA256SUMS` | every file above |

`backup.sh` holds `~/theo-rag-data/.corpus.lock` (also taken by
`build_corpus.py`), so a build cannot write while a backup runs.

`verify_restore.sh` aborts on any checksum failure, restores into isolated
scratch storage (scratch database, throwaway Qdrant on :16333, temp
directories), and compares the result with the backup's own inventory, not
with the live system. A backup can therefore be proven after the workstation
is gone. It also checks that restored Qdrant point IDs equal restored PostgreSQL
embedded chunk IDs, and that every working and original file hash matches.
`scripts/ops/test_backup_verification.sh BACKUP_DIR` confirms that a corrupted
archive, a missing file, and equal counts with wrong IDs all fail.

**Independent storage:** the reference workstation has a single NVMe disk, so
the corpus, checkout, working data and local backups share one failure domain.
`copy_offsite.sh` refuses a destination on the same filesystem and re-verifies
checksums at the destination; run it against an external drive, NAS mount or
`host:/path` after each backup.

Restoring into the live system (destructive, so stop the service first):

```bash
systemctl --user stop theo-rag
docker exec -i theo_rag_postgres psql -U raguser -d postgres -c 'DROP DATABASE theology_rag' -c 'CREATE DATABASE theology_rag'
docker exec -i theo_rag_postgres pg_restore -U raguser -d theology_rag --no-owner < BACKUP/postgres.dump
for s in BACKUP/qdrant/*.snapshot; do
  c=$(basename "$s" .snapshot)
  curl -fsS -X POST "localhost:6333/collections/$c/snapshots/upload?priority=snapshot" -F "snapshot=@$s"
done
tar -C ~/theo-rag-data -xzf BACKUP/working_data.tar.gz
systemctl --user start theo-rag
```

Alternatively, everything except the raw corpus is reproducible: `./reset.sh
destroy --yes-delete-all-data` followed by `./reset.sh rebuild` regenerates the
database and index from the source files (about 1–2 hours on the reference GPU).

## 6. Rollback

- **Code:** the baseline is tagged `phase1-baseline`. To roll back,
  `git checkout phase1-baseline && uv sync --locked && npm --prefix frontend ci && npm --prefix frontend run build && systemctl --user restart theo-rag`.
- **Data:** restore the most recent backup taken before the change (section 5).
- **Model:** pin the model by digest recorded in `docs/RESTORATION_NOTES.md`;
  `ollama list` shows the installed digest.
- **Experiments:** keep retrieval experiments behind their default-off flags
  (`RAG_RRF_FUSION_ENABLED`, `RAG_BM25_OR_FALLBACK_ENABLED`, …) and on separate
  branches; compare candidate runs against the baseline with
  `scripts/eval_compare.py`.

## 7. Evaluation

```bash
.venv/bin/python scripts/eval_suite.py --api-base http://localhost:8000 \
  --input eval/benchmark/v1_cases.jsonl --mode both --output-root ~/theo-rag-data/eval_runs
```

Run artifacts include corpus text and query logs, so they stay outside Git
(`eval/reports/` is gitignored).
