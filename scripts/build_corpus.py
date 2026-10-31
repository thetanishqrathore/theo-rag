"""Resumable, manifest-driven corpus build.

Runs ingest -> extract -> structure -> validate -> canonical -> parent -> child
-> embed -> index for every included source in a manifest produced by
scripts/source_manifest.py. Progress is driven by lesson status, so re-running
resumes where the previous run stopped; a failing stage never advances status.

Every per-file / per-lesson outcome is appended to <report-dir>/<run_id>.jsonl
and a summary is written to <report-dir>/<run_id>_summary.json.

Before processing, the build checks that the manifest still matches the files
on disk, reconciles the database against the manifest (changed, removed or
newly excluded sources are "retired": reported, and deleted together with their
chunks, Qdrant points and working files only with --retire-stale), verifies
that embedded lessons still have their raw copy and canonical HTML, and reports
lessons built under a different processing configuration. After indexing,
Qdrant points with no PostgreSQL row are removed. The run summary records the
Git revision, manifest digest, effective processing configuration and tool
versions.

Exit codes: 0 = every included source is embedded and Qdrant IDs match exactly;
1 = some source failed, is incomplete, or lost an output; 2 = index parity
mismatch; 4 = manifest does not match the files on disk; 5 = retired sources
present (re-run with --retire-stale); 6 = lessons built under another processing
config with --require-current-config.

Usage:
  python scripts/build_corpus.py --manifest ~/theo-rag-data/source_manifest.json \
      --report-dir ~/theo-rag-data/build_reports [--tags transcripts,course_notes] \
      [--paths-file pilot.txt] [--skip-index]
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
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

from src.db.models import (  # noqa: E402
    Corpus,
    FileTag,
    SourceFile,
    TranscriptChildChunk,
    TranscriptLesson,
    TranscriptParentChunk,
)
from src.db.operations import get_file_tag_id  # noqa: E402
from src.db.session import get_sessionmaker  # noqa: E402
from src.ingestion.tag_registry import get_tag_spec  # noqa: E402
from src.utils import provenance  # noqa: E402
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest_on_disk(root: Path, entries: list[dict]) -> list[str]:
    """Every selected source must still exist with the checksum the manifest recorded."""
    problems = []
    for entry in entries:
        path = root / entry["path"]
        if not path.is_file():
            problems.append(f"missing on disk: {entry['path']}")
        elif _sha256_file(path) != entry["sha256"]:
            problems.append(f"changed since manifest: {entry['path']}")
    return problems


def _tag_ids(session, tags: list[str]) -> dict[int, str]:
    return {get_file_tag_id(session, get_tag_spec(t).corpus, t): t for t in tags}


def find_retired_sources(session, tags: list[str], checksums: set[str]) -> list[dict]:
    """Ingested sources (in these tags) whose checksum is no longer an included manifest entry."""
    tag_ids = _tag_ids(session, tags)
    rows = session.execute(
        select(SourceFile.id, SourceFile.filename, SourceFile.checksum, SourceFile.raw_path, SourceFile.file_tag_id)
        .where(SourceFile.file_tag_id.in_(tag_ids.keys()))
    ).all()
    return [
        {"source_file_id": r[0], "filename": r[1], "checksum": r[2], "raw_path": r[3], "tag": tag_ids[r[4]]}
        for r in rows
        if r[2] not in checksums
    ]


def retire_sources(session, retired: list[dict]) -> int:
    """Delete retired sources with every derived artifact (rows, Qdrant points, files)."""
    from qdrant_client.models import PointIdsList

    from src.utils.qdrant_client import get_qdrant_client

    config = get_config()
    canonical_root = Path(config.canonical_path).expanduser().resolve()
    raw_root = Path(config.raw_data_path).expanduser().resolve()
    client = get_qdrant_client(timeout=60.0)
    for item in retired:
        lessons = session.execute(
            select(TranscriptLesson.id, TranscriptLesson.canonical_html_path)
            .where(TranscriptLesson.source_file_id == item["source_file_id"])
        ).all()
        lesson_ids = [lesson[0] for lesson in lessons]
        points = session.execute(
            select(TranscriptChildChunk.id, Corpus.name)
            .join(TranscriptParentChunk, TranscriptChildChunk.parent_chunk_id == TranscriptParentChunk.id)
            .join(Corpus, TranscriptChildChunk.corpus_id == Corpus.id)
            .where(TranscriptParentChunk.lesson_id.in_(lesson_ids))
        ).all() if lesson_ids else []
        by_corpus: dict[str, list[str]] = {}
        for chunk_id, corpus in points:
            by_corpus.setdefault(corpus, []).append(str(chunk_id))
        for corpus, ids in by_corpus.items():
            for start in range(0, len(ids), 1000):
                client.delete(f"{corpus}_chunks", points_selector=PointIdsList(points=ids[start:start + 1000]))
        session.execute(SourceFile.__table__.delete().where(SourceFile.id == item["source_file_id"]))
        session.commit()
        for _lesson_id, rel in lessons:
            if rel:
                (canonical_root / rel).unlink(missing_ok=True)
        raw = Path(item["raw_path"]).resolve()
        if raw_root in raw.parents:
            raw.unlink(missing_ok=True)
        print(f"  retired {item['tag']}/{item['filename']}: {len(lesson_ids)} lesson(s), {len(points)} point(s)")
    return len(retired)


def check_outputs(session, tags: list[str], checksums: set[str], root: Path, entries: list[dict], repair: bool) -> list[str]:
    """Embedded lessons must keep a raw copy with the source checksum and their canonical HTML."""
    from src.processing.canonical import generate_canonical_html

    canonical_root = Path(get_config().canonical_path).expanduser()
    by_checksum = {e["sha256"]: e for e in entries}
    rows = session.execute(
        select(TranscriptLesson.id, TranscriptLesson.canonical_html_path, SourceFile.raw_path, SourceFile.checksum)
        .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
        .where(
            SourceFile.file_tag_id.in_(_tag_ids(session, tags).keys()),
            SourceFile.checksum.in_(checksums),
            TranscriptLesson.status == "embedded",
        )
    ).all()
    problems = []
    raw_checked: set[str] = set()
    for lesson_id, canonical_rel, raw_path, checksum in rows:
        raw = Path(raw_path)
        if raw_path not in raw_checked:
            raw_checked.add(raw_path)
            if not raw.is_file() or _sha256_file(raw) != checksum:
                if repair and checksum in by_checksum:
                    raw.parent.mkdir(parents=True, exist_ok=True)
                    raw.write_bytes((root / by_checksum[checksum]["path"]).read_bytes())
                    print(f"  restored raw copy {raw}")
                else:
                    problems.append(f"raw copy missing or changed: {raw}")
        if not canonical_rel or not (canonical_root / canonical_rel).is_file():
            if repair:
                generate_canonical_html(lesson_id, session, require_validated=False)
                print(f"  regenerated canonical HTML for lesson {lesson_id}")
            else:
                problems.append(f"canonical HTML missing for lesson {lesson_id}: {canonical_rel}")
    return problems


def config_drift(session, tags: list[str], checksums: set[str]) -> dict[str, int]:
    """Count embedded lessons by whether they were validated under the current processing config."""
    current = provenance.processing_config_digest()
    reports = session.execute(
        select(TranscriptLesson.validation_report)
        .join(SourceFile, TranscriptLesson.source_file_id == SourceFile.id)
        .where(
            SourceFile.file_tag_id.in_(_tag_ids(session, tags).keys()),
            SourceFile.checksum.in_(checksums),
            TranscriptLesson.status == "embedded",
        )
    ).scalars().all()
    counts = {"current": 0, "different": 0, "unknown": 0}
    for report in reports:
        digest = ((report or {}).get("provenance") or {}).get("processing_config_digest")
        counts["unknown" if digest is None else "current" if digest == current else "different"] += 1
    return counts


def revalidate_reports(session, tags: list[str], checksums: set[str], reporter: "Reporter") -> None:
    """Re-run validation for embedded lessons without changing status; refreshes saved reports."""
    from src.processing.validate import validate_lesson_structure

    for tag in tags:
        ids = _lesson_ids(session, tag, "embedded", checksums)
        if not ids:
            continue
        print(f"[{tag}] revalidate (report only): {len(ids)} lesson(s)")
        for lesson_id in ids:
            result = validate_lesson_structure(lesson_id, session, update_status=False)
            ok = result["status"] == "validated"
            failed = [k for k, v in (result.get("checks") or {}).items() if not v.get("passed", True)]
            reporter.record("revalidate", ok, tag=tag, lesson_id=lesson_id, message=f"failed checks: {failed}" if failed else "ok")


def prune_orphan_points(session, corpus: str) -> int:
    """Delete Qdrant points whose chunk no longer has an embedded PostgreSQL row."""
    from qdrant_client.models import PointIdsList

    from src.embedding.index import verify_qdrant_id_parity
    from src.utils.qdrant_client import get_qdrant_client

    client = get_qdrant_client(timeout=60.0)
    parity = verify_qdrant_id_parity(corpus, session, client, sample=10**9)
    extra = parity.get("extra_sample") or []
    for start in range(0, len(extra), 1000):
        client.delete(f"{corpus}_chunks", points_selector=PointIdsList(points=extra[start:start + 1000]))
    return len(extra)


def index_and_verify(session, corpora: list[str], reporter: Reporter) -> bool:
    from src.embedding.index import index_corpus_to_qdrant, verify_qdrant_id_parity
    from src.utils.qdrant_client import get_qdrant_client

    client = get_qdrant_client(timeout=60.0)
    all_ok = True
    for corpus in corpora:
        print(f"[index] {corpus}")
        result = index_corpus_to_qdrant(corpus, session)
        pruned = prune_orphan_points(session, corpus)
        if pruned:
            print(f"[index] {corpus}: removed {pruned} point(s) with no PostgreSQL row")
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
    parser.add_argument("--retire-stale", action="store_true", help="Delete sources no longer in the manifest (rows, points, files)")
    parser.add_argument("--repair-outputs", action="store_true", help="Restore missing raw copies / canonical HTML")
    parser.add_argument("--revalidate", action="store_true", help="Re-check embedded lessons (report only, status unchanged)")
    parser.add_argument("--require-current-config", action="store_true", help="Fail if lessons were built under another processing config")
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
    run_provenance = {
        **provenance.full_record(),
        "manifest": str(Path(args.manifest).resolve()),
        "manifest_sha256": _sha256_file(Path(args.manifest)),
    }
    reporter.record("provenance", True, message="run provenance", provenance=run_provenance)

    stale = verify_manifest_on_disk(root, entries)
    if stale:
        for problem in stale[:50]:
            print(f"  STALE {problem}")
        print(f"{len(stale)} manifest entr(ies) do not match the files on disk; regenerate the manifest")
        sys.exit(4)

    session = get_sessionmaker()()
    try:
        # Reconcile only full-scope runs: a --paths-file subset says nothing about other sources.
        retired = find_retired_sources(session, tags, checksums) if selected_paths is None else []
        for item in retired:
            reporter.record("reconcile", args.retire_stale, tag=item["tag"], path=item["filename"],
                            message="source no longer in manifest (changed, removed or excluded)")
        if retired and not args.retire_stale:
            print(f"{len(retired)} ingested source(s) are no longer in the manifest; re-run with --retire-stale")
            sys.exit(5)
        if retired:
            print(f"retired {retire_sources(session, retired)} source(s)")

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
        output_problems = check_outputs(session, tags, checksums, root, entries, repair=args.repair_outputs)
        for problem in output_problems:
            reporter.record("outputs", False, message=problem)
        if args.revalidate:
            revalidate_reports(session, tags, checksums, reporter)
        drift = config_drift(session, tags, checksums)
        print(f"processing config: {drift['current']} current, {drift['different']} different, {drift['unknown']} unknown")

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
        "retired_sources": [r["filename"] for r in retired],
        "output_problems": output_problems,
        "config_drift": drift,
        "provenance": run_provenance,
        "failures": len(reporter.failures),
    }
    reporter.summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"incomplete_lessons", "not_ingested", "provenance"}}, indent=2, default=str))
    print(f"not ingested: {len(not_ingested)}; incomplete lessons: {len(incomplete)}")
    print(f"report: {reporter.path}")
    if not index_ok:
        sys.exit(2)
    if not_ingested or incomplete or output_problems or any(f["stage"] == "revalidate" for f in reporter.failures):
        sys.exit(1)
    if args.require_current_config and (drift["different"] or drift["unknown"]):
        sys.exit(6)


if __name__ == "__main__":
    main()
