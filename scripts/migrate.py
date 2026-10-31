"""Idempotent schema migration runner.

Bootstrap contract:
  1. An empty database is initialized ONCE from scripts/init.sql (docker compose
     does this automatically on an empty volume; use --bootstrap otherwise).
  2. Every file in scripts/migrations/*.sql is applied at most once and recorded
     in schema_migrations with its checksum. Re-running is a no-op.
  3. --check compares the live schema with the SQLAlchemy ORM models.

Usage:
  python scripts/migrate.py              # apply pending migrations
  python scripts/migrate.py --status     # list applied / pending
  python scripts/migrate.py --check      # verify ORM tables/columns exist
  python scripts/migrate.py --bootstrap  # run init.sql if the DB has no tables
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.config import get_config  # noqa: E402

INIT_SQL = ROOT / "scripts" / "init.sql"
MIGRATIONS_DIR = ROOT / "scripts" / "migrations"

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
)
"""


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_txn_control(sql: str) -> bool:
    upper = sql.upper()
    return "BEGIN;" in upper and "COMMIT;" in upper


_TXN_LINE_RE = re.compile(r"^\s*(BEGIN|COMMIT)\s*;\s*$", re.IGNORECASE | re.MULTILINE)


def _migration_body(sql: str, filename: str, checksum: str) -> tuple[str, tuple[str, str]]:
    """One transaction containing the migration and its ledger row.

    Top-level BEGIN;/COMMIT; lines in the file are removed so an early COMMIT
    cannot split the migration from its ledger entry (DO-block BEGIN ... END is
    untouched: it has no semicolon after BEGIN).
    """
    # Executed with parameters, so literal % (e.g. format('%I')) must be escaped.
    inner = _TXN_LINE_RE.sub("", sql).replace("%", "%%")
    body = (
        "BEGIN;\n"
        + inner
        + "\n;INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s);\nCOMMIT;"
    )
    return body, (filename, checksum)


def _connect(database_url: str):
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    return conn


def _table_count(cur) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name <> 'schema_migrations'"
    )
    return int(cur.fetchone()[0])


def _applied(cur) -> dict[str, str]:
    cur.execute(_LEDGER_DDL)
    cur.execute("SELECT filename, checksum FROM schema_migrations")
    return {name: checksum for name, checksum in cur.fetchall()}


def bootstrap(cur) -> bool:
    """Run init.sql on an empty database. Returns True when it ran."""
    if _table_count(cur) > 0:
        print("bootstrap: database already initialized; skipping init.sql")
        return False
    cur.execute("BEGIN;" + INIT_SQL.read_text() + "\nCOMMIT;")
    print("bootstrap: applied init.sql")
    return True


def apply_pending(cur, allow_drift: bool = False) -> int:
    if _table_count(cur) == 0:
        raise SystemExit("Database is empty. Run with --bootstrap (or start docker compose on a fresh volume).")
    # Serialize concurrent runners for the whole pass (released when the connection closes).
    cur.execute("SELECT pg_advisory_lock(hashtext('theo_rag_schema_migrations'))")
    applied = _applied(cur)
    drift = [
        path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
        if path.name in applied and applied[path.name] != _checksum(path)
    ]
    if drift and not allow_drift:
        raise SystemExit(
            "applied migration(s) changed on disk (checksum mismatch): " + ", ".join(drift)
            + ". Add a new migration instead of editing an applied one (or pass --allow-drift after review)."
        )
    count = 0
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        checksum = _checksum(path)
        if path.name in applied:
            continue
        body, params = _migration_body(path.read_text(), path.name, checksum)
        try:
            cur.execute(body, params)
        except Exception as exc:
            cur.execute("ROLLBACK;")
            raise SystemExit(f"migration {path.name} failed (nothing recorded): {exc}") from exc
        print(f"applied {path.name}")
        count += 1
    print(f"{count} migration(s) applied; {len(applied) + count} recorded")
    return count


def status(cur) -> None:
    applied = _applied(cur)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        state = "applied" if path.name in applied else "PENDING"
        print(f"{state:8} {path.name}")


def check_orm(cur) -> bool:
    """Verify every ORM table and column exists in the live schema."""
    from src.db.models import Base

    cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
    )
    live: dict[str, set[str]] = {}
    for table, column in cur.fetchall():
        live.setdefault(table, set()).add(column)

    ok = True
    for table in Base.metadata.sorted_tables:
        if table.name not in live:
            print(f"MISSING TABLE {table.name}")
            ok = False
            continue
        for column in table.columns:
            if column.name not in live[table.name]:
                print(f"MISSING COLUMN {table.name}.{column.name}")
                ok = False
    print("orm check: OK" if ok else "orm check: FAILED")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bootstrap", action="store_true", help="Run init.sql if the DB has no tables, then migrate")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--allow-drift", action="store_true", help="Continue although an applied migration file changed")
    args = parser.parse_args()

    conn = _connect(args.database_url or get_config().database_url)
    try:
        with conn.cursor() as cur:
            if args.status:
                status(cur)
                return
            if args.check:
                sys.exit(0 if check_orm(cur) else 1)
            if args.bootstrap:
                bootstrap(cur)
            apply_pending(cur, allow_drift=args.allow_drift)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
