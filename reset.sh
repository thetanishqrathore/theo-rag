#!/usr/bin/env bash
# Stack lifecycle helper. Nothing here deletes data unless you ask explicitly.
#
#   ./reset.sh start      Start PostgreSQL + Qdrant, apply pending migrations.
#   ./reset.sh rebuild    start, then run the resumable corpus build
#                         (ingest -> ... -> embed -> Qdrant index + parity).
#   ./reset.sh destroy --yes-delete-all-data
#                         Delete the database and Qdrant volumes and the raw /
#                         canonical working copies, then start empty.
#
# Environment: MANIFEST (default $DATA_ROOT/source_manifest.json),
#              REPORT_DIR (default $DATA_ROOT/build_reports), where DATA_ROOT
#              is the parent of RAW_DATA_PATH from .env.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
PY="$ROOT_DIR/.venv/bin/python"

env_value() {
  # Read KEY=value from .env without sourcing it.
  [[ -f .env ]] && sed -n "s/^$1=//p" .env | tail -1
}

RAW_DATA_PATH="$(env_value RAW_DATA_PATH)"
CANONICAL_PATH="$(env_value CANONICAL_PATH)"
DATA_ROOT="$(dirname "${RAW_DATA_PATH:-$ROOT_DIR/data/raw}")"
MANIFEST="${MANIFEST:-$DATA_ROOT/source_manifest.json}"
REPORT_DIR="${REPORT_DIR:-$DATA_ROOT/build_reports}"

start() {
  scripts/ops/wait_for_deps.sh
  "$PY" scripts/migrate.py
  "$PY" scripts/migrate.py --check
}

rebuild() {
  start
  [[ -f "$MANIFEST" ]] || { echo "manifest not found: $MANIFEST (run scripts/source_manifest.py)" >&2; exit 1; }
  "$PY" scripts/build_corpus.py --manifest "$MANIFEST" --report-dir "$REPORT_DIR"
}

destroy() {
  if [[ "${1:-}" != "--yes-delete-all-data" ]]; then
    echo "Refusing: destroy deletes the database, Qdrant index and working copies." >&2
    echo "Re-run as: ./reset.sh destroy --yes-delete-all-data" >&2
    exit 2
  fi
  docker compose down -v
  for dir in "$RAW_DATA_PATH" "$CANONICAL_PATH"; do
    if [[ -n "$dir" && -d "$dir" && "$dir" != "/" ]]; then
      find "$dir" -mindepth 1 -delete
      echo "cleared $dir"
    fi
  done
  start
}

case "${1:-}" in
  start) start ;;
  rebuild) rebuild ;;
  destroy) shift; destroy "${1:-}" ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
