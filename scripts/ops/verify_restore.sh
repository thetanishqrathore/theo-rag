#!/usr/bin/env bash
# Prove a backup is restorable WITHOUT touching or consulting live data.
#
#   scripts/ops/verify_restore.sh BACKUP_DIR
#
# 1. Verifies every file against SHA256SUMS (any mismatch aborts).
# 2. Restores into isolated scratch storage only: a scratch database inside the
#    PostgreSQL container, a throwaway Qdrant container on port 16333 with its
#    own volume, and temporary directories for working data and originals.
# 3. Compares the restored database, index and files with the backup's own
#    inventory.json / working_files.sha256 / originals.sha256 (counts, sorted-ID
#    digests, per-file hashes) and cross-checks restored PostgreSQL embedded
#    chunk IDs against restored Qdrant point IDs.
# Scratch storage is always removed. Exit 0 only if every check passes.
set -euo pipefail

BACKUP="$(cd "${1:?usage: verify_restore.sh BACKUP_DIR}" && pwd)"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT_DIR/.venv/bin/python"
SCRATCH_DB="theology_rag_restore_check"
SCRATCH_QDRANT="theo_rag_qdrant_restore_check"
SCRATCH_PORT=16333
SCRATCH_URL="postgresql://raguser:dev_password@127.0.0.1:5432/$SCRATCH_DB"
TMP="$(mktemp -d)"
PSQL=(docker exec -i theo_rag_postgres psql -v ON_ERROR_STOP=1 -U raguser -At)

cleanup() {
  "${PSQL[@]}" -d theology_rag -c "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null 2>&1 || true
  docker rm -f "$SCRATCH_QDRANT" >/dev/null 2>&1 || true
  docker volume rm "${SCRATCH_QDRANT}_data" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

for required in SHA256SUMS inventory.json working_files.sha256 postgres.dump working_data.tar.gz; do
  [[ -f "$BACKUP/$required" ]] || { echo "backup incomplete: missing $required" >&2; exit 1; }
done

echo "== checksums"
if ! (cd "$BACKUP" && sha256sum -c --quiet SHA256SUMS); then
  echo "CHECKSUM FAILURE: backup is damaged; not restoring" >&2
  exit 1
fi
listed="$(wc -l < "$BACKUP/SHA256SUMS")"
actual="$(cd "$BACKUP" && find . -type f ! -name SHA256SUMS | wc -l)"
[[ "$listed" == "$actual" ]] || { echo "CHECKSUM FAILURE: $actual files present, $listed listed" >&2; exit 1; }
echo "  ok  $listed files"

echo "== restore postgres (scratch database)"
"${PSQL[@]}" -d theology_rag -c "DROP DATABASE IF EXISTS $SCRATCH_DB" -c "CREATE DATABASE $SCRATCH_DB" >/dev/null
docker exec -i theo_rag_postgres pg_restore -U raguser -d "$SCRATCH_DB" --no-owner --exit-on-error < "$BACKUP/postgres.dump"

echo "== restore qdrant (throwaway container :$SCRATCH_PORT)"
docker run -d --name "$SCRATCH_QDRANT" -p "127.0.0.1:$SCRATCH_PORT:6333" \
  -v "${SCRATCH_QDRANT}_data:/qdrant/storage" qdrant/qdrant:v1.7.0 >/dev/null
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$SCRATCH_PORT/collections" >/dev/null 2>&1 && break
  sleep 1
done
for snapshot in "$BACKUP"/qdrant/*.snapshot; do
  collection="$(basename "$snapshot" .snapshot)"
  curl -fsS -X POST "http://127.0.0.1:$SCRATCH_PORT/collections/$collection/snapshots/upload?priority=snapshot" \
    -F "snapshot=@$snapshot" >/dev/null
done

echo "== extract working data"
mkdir -p "$TMP/working"
tar -C "$TMP/working" -xzf "$BACKUP/working_data.tar.gz"

echo "== compare with backup inventory"
"$PY" "$ROOT_DIR/scripts/ops/backup_inventory.py" verify --backup-dir "$BACKUP" \
  --database-url "$SCRATCH_URL" --qdrant-url "http://127.0.0.1:$SCRATCH_PORT" --working-dir "$TMP/working"

if [[ -f "$BACKUP/originals/originals.tar" ]]; then
  echo "== originals"
  mkdir -p "$TMP/originals"
  tar -C "$TMP/originals" -xf "$BACKUP/originals/originals.tar"
  (cd "$TMP/originals" && sha256sum -c --quiet "$BACKUP/originals/originals.sha256")
  count="$(find "$TMP/originals" -type f | wc -l)"
  [[ "$count" == "$(wc -l < "$BACKUP/originals/originals.sha256")" ]]
  [[ "$(sha256sum "$BACKUP/originals/originals.sha256" | cut -c1-64)" == "$(cat "$BACKUP/originals/REVISION")" ]]
  echo "  ok  $count original files (revision $(cut -c1-16 "$BACKUP/originals/REVISION"))"
fi

echo "restore check passed: $BACKUP"
