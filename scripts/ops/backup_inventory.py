"""Record and verify what a backup must contain, independent of the live system.

  snapshot  Write inventory.json for the live system into a backup directory:
            row counts and sorted-ID digests per table, embedded chunk IDs,
            Qdrant collection config + point-ID digests, provenance.
  verify    Check a *restored* PostgreSQL database and Qdrant instance, plus an
            extracted working-data directory, against a backup's own
            inventory.json and working_files.sha256. Nothing is compared with
            the live system, so a backup can be proven after the live system is
            gone. Also cross-checks restored PostgreSQL embedded chunk IDs
            against restored Qdrant point IDs.

Exit status: 0 on success, 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2

ROOT = Path(__file__).resolve().parents[2]

# (name, SQL returning one text id per row)
ID_QUERIES = {
    "source_files": "SELECT id::text FROM source_files",
    "lessons": "SELECT id::text FROM lessons",
    "paragraphs": "SELECT id::text FROM paragraphs",
    "parent_chunks": "SELECT id::text FROM parent_chunks",
    "child_chunks": "SELECT id::text FROM child_chunks",
    "embedded_child_chunks": "SELECT id::text FROM child_chunks WHERE embedding IS NOT NULL",
    "schema_migrations": "SELECT filename || ':' || checksum FROM schema_migrations",
    "source_checksums": "SELECT checksum FROM source_files",
}
CORPUS_CHILD_QUERY = (
    "SELECT c.id::text FROM child_chunks c JOIN corpus k ON k.id = c.corpus_id "
    "WHERE c.embedding IS NOT NULL AND k.name = %s"
)


def _digest(ids: list[str]) -> str:
    h = hashlib.sha256()
    for value in sorted(ids):
        h.update(value.encode())
        h.update(b"\n")
    return h.hexdigest()


def _pg_ids(conn, sql: str, params: tuple = ()) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]


def _qdrant_ids(base: str, collection: str) -> list[str]:
    ids: list[str] = []
    offset = None
    with httpx.Client(timeout=60) as client:
        while True:
            body = {"limit": 5000, "with_payload": False, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            r = client.post(f"{base}/collections/{collection}/points/scroll", json=body)
            r.raise_for_status()
            result = r.json()["result"]
            ids.extend(str(p["id"]) for p in result["points"])
            offset = result.get("next_page_offset")
            if offset is None:
                return ids


def _qdrant_collections(base: str) -> dict[str, dict]:
    with httpx.Client(timeout=30) as client:
        names = [c["name"] for c in client.get(f"{base}/collections").json()["result"]["collections"]]
        out = {}
        for name in sorted(names):
            info = client.get(f"{base}/collections/{name}").json()["result"]
            vectors = info["config"]["params"]["vectors"]
            out[name] = {
                "points_count": info["points_count"],
                "vector_size": vectors.get("size"),
                "distance": vectors.get("distance"),
            }
        return out


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def describe(conn, qdrant_base: str) -> dict:
    tables = {}
    for name, sql in ID_QUERIES.items():
        ids = _pg_ids(conn, sql)
        tables[name] = {"count": len(ids), "digest": _digest(ids)}
    collections = _qdrant_collections(qdrant_base)
    for name, meta in collections.items():
        ids = _qdrant_ids(qdrant_base, name)
        meta["id_count"] = len(ids)
        meta["id_digest"] = _digest(ids)
        corpus = name.removesuffix("_chunks")
        pg_ids = _pg_ids(conn, CORPUS_CHILD_QUERY, (corpus,))
        meta["postgres_embedded_digest"] = _digest(pg_ids)
    return {"tables": tables, "qdrant": collections}


def snapshot(args) -> int:
    backup = Path(args.backup_dir)
    conn = psycopg2.connect(args.database_url)
    try:
        inventory = describe(conn, args.qdrant_url)
    finally:
        conn.close()
    inventory["created_at"] = datetime.now(timezone.utc).isoformat()
    inventory["git_sha"] = _git_sha()
    for key in ("manifest", "originals_manifest"):
        path = getattr(args, key)
        if path and Path(path).exists():
            inventory[f"{key}_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for name, meta in inventory["qdrant"].items():
        if meta["id_digest"] != meta["postgres_embedded_digest"]:
            print(f"WARNING: live {name} IDs differ from PostgreSQL embedded IDs", file=sys.stderr)
    (backup / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"inventory written: {backup / 'inventory.json'}")
    return 0


def _check_working_files(extract_dir: Path, sums_file: Path) -> list[str]:
    problems: list[str] = []
    expected = {}
    for line in sums_file.read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        expected[rel] = digest
    present = {str(p.relative_to(extract_dir)) for p in extract_dir.rglob("*") if p.is_file()}
    for rel, digest in expected.items():
        path = extract_dir / rel
        if not path.is_file():
            problems.append(f"working file missing: {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append(f"working file hash mismatch: {rel}")
    for rel in sorted(present - set(expected)):
        problems.append(f"unexpected working file: {rel}")
    return problems


def verify(args) -> int:
    backup = Path(args.backup_dir)
    expected = json.loads((backup / "inventory.json").read_text(encoding="utf-8"))
    conn = psycopg2.connect(args.database_url)
    try:
        actual = describe(conn, args.qdrant_url)
    finally:
        conn.close()

    problems: list[str] = []
    for name, exp in expected["tables"].items():
        got = actual["tables"].get(name)
        if got != exp:
            problems.append(f"table {name}: expected {exp}, restored {got}")
        else:
            print(f"  ok  {name:24} {exp['count']}")
    for name, exp in expected["qdrant"].items():
        got = actual["qdrant"].get(name)
        if got is None:
            problems.append(f"qdrant {name}: missing after restore")
            continue
        for key in ("points_count", "vector_size", "distance", "id_count", "id_digest"):
            if got.get(key) != exp.get(key):
                problems.append(f"qdrant {name}.{key}: expected {exp.get(key)}, restored {got.get(key)}")
        if got["id_digest"] != got["postgres_embedded_digest"]:
            problems.append(f"qdrant {name}: restored point IDs differ from restored PostgreSQL embedded IDs")
        if not any(p.startswith(f"qdrant {name}") for p in problems):
            print(f"  ok  {name:24} {exp['points_count']} points (IDs match PostgreSQL)")
    if args.working_dir:
        working_problems = _check_working_files(Path(args.working_dir), backup / "working_files.sha256")
        problems.extend(working_problems)
        if not working_problems:
            count = len((backup / "working_files.sha256").read_text().splitlines())
            print(f"  ok  working files            {count} (hashes match)")

    for problem in problems:
        print(f"MISMATCH {problem}", file=sys.stderr)
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--backup-dir", required=True)
        p.add_argument("--database-url", required=True)
        p.add_argument("--qdrant-url", required=True)
        if name == "snapshot":
            p.add_argument("--manifest", default="")
            p.add_argument("--originals-manifest", default="")
        else:
            p.add_argument("--working-dir", default="")
    args = parser.parse_args()
    sys.exit(snapshot(args) if args.command == "snapshot" else verify(args))


if __name__ == "__main__":
    main()
