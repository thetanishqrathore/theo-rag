# `test/` — sample source documents

Drop your own source documents here (PDF, HTML, or DOCX) to exercise the
ingestion pipeline, then run:

```bash
./reset.sh          # tears down + rebuilds the stack and ingests test/
# or, manually:
python -m src.ingestion test/ --batch --tag transcripts
```

> The original teaching transcripts used to build the production corpora are
> **not distributed** with this repository. Supply your own documents to run
> the pipeline end to end.
