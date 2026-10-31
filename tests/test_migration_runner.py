"""Integration tests for scripts/migrate.py (skipped when PostgreSQL is unreachable)."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.getenv("DATABASE_URL", "postgresql://raguser:dev_password@127.0.0.1:5432/theology_rag")


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate", ROOT / "scripts" / "migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def scratch_cursor():
    try:
        admin = psycopg2.connect(ADMIN_URL, connect_timeout=3)
    except psycopg2.OperationalError:
        pytest.skip("PostgreSQL not reachable")
    admin.autocommit = True
    name = f"migtest_{uuid.uuid4().hex[:8]}"
    with admin.cursor() as cur:
        cur.execute(f"CREATE DATABASE {name}")
    conn = psycopg2.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{name}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE TABLE existing (id int)")  # non-empty schema, like after init.sql
    try:
        yield cur
    finally:
        cur.close()
        conn.close()
        with admin.cursor() as c:
            c.execute(f"DROP DATABASE IF EXISTS {name}")
        admin.close()


def test_failed_migration_records_nothing(scratch_cursor, tmp_path, monkeypatch) -> None:
    migrate = _load_migrate()
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE ok_table (id int);\n")
    # Explicit BEGIN/COMMIT, a literal %, then an error after real DDL.
    (tmp_path / "002_bad.sql").write_text(
        "BEGIN;\nCREATE TABLE half_done (id int);\nSELECT format('%I', 'x');\nSELECT 1/0;\nCOMMIT;\n"
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

    with pytest.raises(SystemExit, match="002_bad.sql failed"):
        migrate.apply_pending(scratch_cursor)

    scratch_cursor.execute("SELECT filename FROM schema_migrations ORDER BY 1")
    assert [r[0] for r in scratch_cursor.fetchall()] == ["001_ok.sql"]
    scratch_cursor.execute("SELECT to_regclass('half_done')")
    assert scratch_cursor.fetchone()[0] is None  # DDL rolled back with the ledger row


def test_checksum_drift_fails_unless_allowed(scratch_cursor, tmp_path, monkeypatch) -> None:
    migrate = _load_migrate()
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE ok_table (id int);\n")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    assert migrate.apply_pending(scratch_cursor) == 1
    assert migrate.apply_pending(scratch_cursor) == 0

    (tmp_path / "001_ok.sql").write_text("CREATE TABLE ok_table (id int, extra int);\n")
    with pytest.raises(SystemExit, match="checksum mismatch"):
        migrate.apply_pending(scratch_cursor)
    assert migrate.apply_pending(scratch_cursor, allow_drift=True) == 0
