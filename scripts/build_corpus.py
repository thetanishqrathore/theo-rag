"""Resumable, manifest-driven corpus build.

Runs ingest -> extract -> structure -> validate -> canonical -> parent -> child
-> embed -> index for every included source in a manifest produced by
scripts/source_manifest.py. Progress is driven by lesson status, so re-running
resumes where the previous run stopped; a failing stage never advances status.

Every per-file / per-lesson outcome is appended to <report-dir>/<run_id>.jsonl
and a summary is written to <report-dir>/<run_id>_summary.json.

Exit codes: 0 = every included source is embedded and Qdrant IDs match exactly;
1 = some source failed or is incomplete; 2 = index parity mismatch.

Usage:
  python scripts/build_corpus.py --manifest ~/theo-rag-data/source_manifest.json \
      --report-dir ~/theo-rag-data/build_reports [--tags transcripts,course_notes] \
      [--paths-file pilot.txt] [--skip-index]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from src.db.models import SourceFile, TranscriptLesson, TranscriptParentChunk  # noqa: E402
from src.db.operations import get_file_tag_id  # noqa: E402
from src.db.session import get_sessionmaker  # noqa: E402
from src.ingestion.tag_registry import get_tag_spec  # noqa: E402
from src.utils.config import get_config  # noqa: E402

# Ingest order matters only for readability of reports; the manifest already
# removed exact duplicates across tags.
TAG_ORDER = [
    "transcripts",
    "course_notes",
    "dean_bible_books",
    "dean_topical_html",
    "dean_topical_pdfs",
    "dean_topical_doc",
    "dean_topical_rtf",
]
OK_STATUSES = {"success", "validated"}


class Reporter:
    def __init__(self, report_dir: Path, run_id: str) -> None:
        report_dir.mkdir(parents=True, exist_ok=True)
        self.path = report_dir / f"{run_id}.jsonl"
        self.summary_path = report_dir / f"{run_id}_summary.json"
        self.failures: list[dict] = []
        self.counts: dict[str, dict[str, int]] = {}

    def record(self, stage: str, ok: bool, **fields) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), "stage": stage, "ok": ok, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
        bucket = self.counts.setdefault(stage, {"ok": 0, "failed": 0})
        bucket["ok" if ok else "failed"] += 1
        if not ok:
            self.failures.append(event)
            print(f"  FAIL [{stage}] {fields.get('path') or fields.get('lesson_id')}: {fields.get('message', '')[:200]}")


def _ingest(path: Path, tag: str) -> dict:
    from src.ingestion.__main__ import _ingest_one

    return _ingest_one(path, tag)


def _lesson_ids(session, tag: str, status: str, checksums: set[str] | None, need_canonical: bool | None = None) -> list[int]:
    tag_id = get_file_tag_id(session, get_tag_spec(tag).corpus, tag)
    stmt = (
        select(TranscriptLesson.id)
        .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
        .where(TranscriptLesson.status == status, SourceFile.file_tag_id == tag_id)
        .order_by(TranscriptLesson.id)
    )
    if checksums is not None:
        stmt = stmt.where(SourceFile.checksum.in_(checksums))
    if need_canonical is True:
        stmt = stmt.where(TranscriptLesson.canonical_html_path.is_not(None))
    elif need_canonical is False:
        stmt = stmt.where(TranscriptLesson.canonical_html_path.is_(None))
    return list(session.execute(stmt).scalars().all())


def _stage_functions() -> list[tuple[str, str, bool | None, Callable]]:
    from src.chunking.child import process_lesson_child_chunking
    from src.chunking.parent import process_lesson_chunking
    from src.embedding.generate import process_lesson_embeddings
    from src.processing.__main__ import _extract_for_lesson
    from src.processing.canonical import generate_canonical_html
    from src.processing.structure import process_lesson_structure
    from src.processing.validate import validate_lesson_structure

    def canonical(lesson_id, session, _tag):
        path = generate_canonical_html(lesson_id, session)
        return {"status": "success", "message": str(path)}

    # (stage, input status, canonical filter, fn)
    return [
        ("extract", "pending", None, lambda lid, s, tag: _extract_for_lesson(s, lid, tag)),
        ("structure", "extracted", None, lambda lid, s, _tag: process_lesson_structure(lid, s)),
        ("validate", "structured", None, lambda lid, s, _tag: validate_lesson_structure(lid, s)),
        ("canonical", "validated", False, canonical),
        ("parent", "validated", True, lambda lid, s, _tag: process_lesson_chunking(lid, s)),
        ("child", "chunked", None, lambda lid, s, _tag: process_lesson_child_chunking(lid, s)),
        ("embed", "child_chunked", None, lambda lid, s, _tag: process_lesson_embeddings(lid, s)),
    ]


def run_stages(session, tag: str, checksums: set[str] | None, reporter: Reporter) -> None:
    for stage, status, need_canonical, fn in _stage_functions():
        lesson_ids = _lesson_ids(session, tag, status, checksums, need_canonical)
        if not lesson_ids:
            continue
        print(f"[{tag}] {stage}: {len(lesson_ids)} lesson(s)")
        started = time.perf_counter()
        for lesson_id in lesson_ids:
            try:
                result = fn(lesson_id, session, tag) or {}
                ok = result.get("status") in OK_STATUSES
                message = result.get("message", "")
                if stage == "validate" and not ok:
                    failed = [k for k, v in (result.get("checks") or {}).items() if isinstance(v, dict) and not v.get("passed", True)]
                    message = f"{message}; failed checks: {failed}" if failed else message
            except Exception as exc:  # stage failure must not advance status
                session.rollback()
                ok = False
                message = f"{type(exc).__name__}: {exc}"
                traceback.print_exc(limit=3)
            reporter.record(stage, ok, tag=tag, lesson_id=lesson_id, message=message)
        print(f"[{tag}] {stage}: done in {time.perf_counter() - started:.1f}s")


def reset_failed_lessons(session, tags: list[str], checksums: set[str]) -> int:
    """Return failed, not-yet-chunked lessons to 'pending' so extraction re-runs.

    Extraction replaces a lesson's paragraphs/nodes, so this is safe for any lesson
    that failed before parent chunking. Chunked lessons are never touched.
    """
    reset = 0
    for tag in tags:
        tag_id = get_file_tag_id(session, get_tag_spec(tag).corpus, tag)
        lessons = session.execute(
            select(TranscriptLesson)
            .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
            .where(
                SourceFile.file_tag_id == tag_id,
                SourceFile.checksum.in_(checksums),
                TranscriptLesson.status == "failed",
                ~select(TranscriptParentChunk.id).where(TranscriptParentChunk.lesson_id == TranscriptLesson.id).exists(),
            )
        ).scalars().all()
        for lesson in lessons:
            lesson.status = "pending"
            lesson.validation_report = None
            lesson.canonical_html_path = None
            reset += 1
    session.commit()
    return reset


def incomplete_lessons(session, tags: list[str], checksums: set[str] | None) -> list[dict]:
    rows: list[dict] = []
    for tag in tags:
        tag_id = get_file_tag_id(session, get_tag_spec(tag).corpus, tag)
        stmt = (
            select(TranscriptLesson.id, TranscriptLesson.status, SourceFile.filename)
            .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
            .where(SourceFile.file_tag_id == tag_id, TranscriptLesson.status != "embedded")
        )
        if checksums is not None:
            stmt = stmt.where(SourceFile.checksum.in_(checksums))
        rows.extend({"tag": tag, "lesson_id": r[0], "status": r[1], "filename": r[2]} for r in session.execute(stmt))
    return rows


def index_and_verify(session, corpora: list[str], reporter: Reporter) -> bool:
    from src.embedding.index import index_corpus_to_qdrant, verify_qdrant_id_parity
    from src.utils.qdrant_client import get_qdrant_client

    client = get_qdrant_client(timeout=60.0)
    all_ok = True
    for corpus in corpora:
        print(f"[index] {corpus}")
        result = index_corpus_to_qdrant(corpus, session)
        parity = verify_qdrant_id_parity(corpus, session, client)
        ok = result.get("status") == "success" and bool(parity.get("exact_match"))
        reporter.record("index", ok, corpus=corpus, message=result.get("message", ""), parity=parity)
        all_ok = all_ok and ok
    return all_ok


def acquire_corpus_lock():
    """Exclusive lock shared with scripts/ops/backup.sh: builds and backups never overlap."""
    data_root = Path(get_config().raw_data_path).expanduser().resolve().parent
    data_root.mkdir(parents=True, exist_ok=True)
    handle = open(data_root / ".corpus.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another build or a backup holds {data_root / '.corpus.lock'}; retry later")
    return handle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--tags", default=",".join(TAG_ORDER))
    parser.add_argument("--paths-file", help="Restrict to these manifest-relative paths (one per line)")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="Re-run failed lessons that have no chunks yet")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    root = Path(manifest["root"])
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    selected_paths: set[str] | None = None
    if args.paths_file:
        selected_paths = {line.strip() for line in Path(args.paths_file).read_text().splitlines() if line.strip()}

    entries = [
        e for e in manifest["entries"]
        if e["include"] and e["tag"] in tags and (selected_paths is None or e["path"] in selected_paths)
    ]
    # Always scope stages and completeness checks to the manifest's included sources,
    # so lessons from excluded sources (e.g. ingested before an exclusion) never block.
    checksums = {e["sha256"] for e in entries}

    run_id = datetime.now().strftime("build_%Y%m%d_%H%M%S")
    reporter = Reporter(Path(args.report_dir).expanduser(), run_id)
    print(f"run {run_id}: {len(entries)} source(s), tags={tags}")

    lock_handle = acquire_corpus_lock()  # noqa: F841 - held until exit
    session = get_sessionmaker()()
    try:
        if args.retry_failed:
            print(f"reset {reset_failed_lessons(session, tags, checksums)} failed lesson(s) to pending")
        known = set(session.execute(select(SourceFile.checksum)).scalars().all())
        for tag in [t for t in TAG_ORDER if t in tags]:
            todo = [e for e in entries if e["tag"] == tag and e["sha256"] not in known]
            if todo:
                print(f"[{tag}] ingest: {len(todo)} new source(s)")
            for entry in todo:
                result = _ingest(root / entry["path"], tag)
                ok = result.get("status") == "success"
                reporter.record("ingest", ok, tag=tag, path=entry["path"], lesson_id=result.get("lesson_id"), message=result.get("message", ""))
            # Ingestion lives in its own sessions; refresh ours.
            session.expire_all()
            run_stages(session, tag, checksums, reporter)

        session.expire_all()
        known = set(session.execute(select(SourceFile.checksum)).scalars().all())
        not_ingested = [e["path"] for e in entries if e["sha256"] not in known]
        incomplete = incomplete_lessons(session, tags, checksums)

        index_ok = True
        if not args.skip_index:
            corpora = sorted({get_tag_spec(t).corpus for t in tags})
            index_ok = index_and_verify(session, corpora, reporter)
    finally:
        session.close()

    summary = {
        "run_id": run_id,
        "sources_selected": len(entries),
        "stage_counts": reporter.counts,
        "not_ingested": not_ingested,
        "incomplete_lessons": incomplete,
        "index_ok": index_ok,
        "failures": len(reporter.failures),
    }
    reporter.summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"incomplete_lessons", "not_ingested"}}, indent=2))
    print(f"not ingested: {len(not_ingested)}; incomplete lessons: {len(incomplete)}")
    print(f"report: {reporter.path}")
    if not index_ok:
        sys.exit(2)
    if not_ingested or incomplete:
        sys.exit(1)


if __name__ == "__main__":
    main()
