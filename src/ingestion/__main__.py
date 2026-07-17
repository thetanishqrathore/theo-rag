"""CLI entry point for ingestion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.ingestion.ingest_dean_html import ingest_dean_html
from src.ingestion.ingest_dean_doc import ingest_dean_doc
from src.ingestion.ingest_dean_pdf import ingest_dean_pdf
from src.ingestion.ingest_course_notes import ingest_course_notes
from src.ingestion.ingest_transcript_doc import ingest_transcript_doc
from src.ingestion.ingest import ingest_pdf
from src.ingestion.tag_registry import get_tag_spec
from src.utils.config import get_config


_TOPICAL_HTML_TAG = "dean_topical_html"
_TOPICAL_PDF_TAG = "dean_topical_pdfs"
_TOPICAL_DOC_TAG = "dean_topical_doc"
_TOPICAL_RTF_TAG = "dean_topical_rtf"


def _not_implemented(message: str) -> dict:
    return {"status": "error", "lesson_id": None, "checksum": None, "message": message}


def _allowed_suffixes(tag: str) -> set[str]:
    if tag == "transcripts":
        mode = (get_config().transcript_source_format or "doc").strip().lower()
        if mode == "pdf":
            return {".pdf"}
        if mode == "all":
            return {".pdf", ".doc", ".docx"}
        return {".doc", ".docx"}
    if tag == "course_notes":
        return {".pdf"}
    if tag == _TOPICAL_HTML_TAG:
        return {".htm", ".html"}
    if tag == _TOPICAL_PDF_TAG:
        return {".pdf"}
    if tag == _TOPICAL_DOC_TAG:
        return {".doc", ".docx"}
    if tag == _TOPICAL_RTF_TAG:
        return {".rtf"}
    if tag == "dean_bible_books":
        return {".htm", ".html"}
    return {".pdf"}


def _ingest_one(path: Path, tag: str) -> dict:
    spec = get_tag_spec(tag)
    if spec.corpus == "clough" and tag == "transcripts":
        if path.suffix.lower() in {".doc", ".docx"}:
            return ingest_transcript_doc(path, tag)
        return ingest_pdf(path, tag)
    if spec.corpus == "clough" and tag == "course_notes":
        return ingest_course_notes(path, tag)
    if spec.corpus == "dean":
        suffix = path.suffix.lower()
        if suffix in {".htm", ".html"}:
            return ingest_dean_html(path, tag)
        if tag == _TOPICAL_PDF_TAG and suffix == ".pdf":
            return ingest_dean_pdf(path, tag)
        if tag == _TOPICAL_DOC_TAG:
            if suffix in {".doc", ".docx"}:
                return ingest_dean_doc(path, tag)
            return _not_implemented(f"Unsupported file type '{suffix}' for tag '{tag}'.")
        if tag == _TOPICAL_RTF_TAG:
            if suffix == ".rtf":
                return ingest_dean_doc(path, tag)
            return _not_implemented(f"Unsupported file type '{suffix}' for tag '{tag}'.")
        if suffix == ".pdf":
            return ingest_dean_pdf(path, tag)
        return _not_implemented(f"Unsupported file type '{suffix}' for tag '{tag}'.")
    return ingest_pdf(path, tag)


def main() -> None:
    """Parse arguments and ingest PDF(s).

    Returns:
        None
    """
    parser = argparse.ArgumentParser(description="Ingest source files into raw storage and DB.")
    parser.add_argument("path", type=str, help="Source file path or directory")
    parser.add_argument("--tag", required=True, help="File tag (e.g., transcripts)")
    parser.add_argument("--batch", action="store_true", help="Ingest all supported files in a directory")
    args = parser.parse_args()

    target = Path(args.path)
    spec = get_tag_spec(args.tag)
    if args.batch:
        if not target.is_dir():
            raise SystemExit("Batch mode requires a directory path")
        if spec.corpus == "dean":
            allowed_suffixes = _allowed_suffixes(args.tag)
            files = sorted(
                p for p in target.rglob("*")
                if p.is_file()
                and p.suffix.lower() in allowed_suffixes
                and p.name.lower() != "scripture_index_new.htm"
                and "_files" not in p.as_posix()
            )
        elif args.tag == "transcripts":
            allowed_suffixes = _allowed_suffixes(args.tag)
            files = sorted(
                p for p in target.rglob("*")
                if p.is_file() and p.suffix.lower() in allowed_suffixes
            )
        else:
            files = sorted(target.glob("*.pdf"))
        results = [_ingest_one(path, args.tag) for path in files]
        success_count = sum(1 for result in results if result["status"] == "success")
        print(f"{success_count}/{len(results)} files ingested successfully")
        print(json.dumps(results, indent=2))
        return

    result = _ingest_one(target, args.tag)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
