"""Ingestion package."""

from src.ingestion.ingest_dean_html import ingest_dean_html
from src.ingestion.ingest_dean_doc import ingest_dean_doc
from src.ingestion.ingest_dean_pdf import ingest_dean_pdf
from src.ingestion.ingest import ingest_pdf

__all__ = ["ingest_pdf", "ingest_dean_html", "ingest_dean_pdf", "ingest_dean_doc"]
