# `test/` — sample source documents

Drop your own source documents here (PDF, HTML or DOCX) to try the ingestion
pipeline on a small scale:

```bash
./reset.sh start                                           # PostgreSQL + Qdrant, migrations
.venv/bin/python -m src.ingestion test/ --batch --tag transcripts
```

That registers the files as lessons in `pending` status. The later stages
(extract → validate → chunk → embed → index) are listed in
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#offline-pipeline--content-build).
For a full, resumable build from a manifest, use `scripts/build_corpus.py`
([`docs/OPERATIONS.md`](../docs/OPERATIONS.md#2-building-the-corpus)).

Run experiments like this against a separate database and data root, not the
production corpus. See *Experiments* in `docs/OPERATIONS.md`.

> The original teaching documents used to build the production corpora are
> **not distributed** with this repository.
