#!/usr/bin/env bash
# Prove a backup is restorable WITHOUT touching live data.
#
#   scripts/ops/verify_restore.sh BACKUP_DIR
#
# Restores into isolated storage only: a scratch database inside the running
# PostgreSQL container, a throwaway Qdrant container on port 16333 with its own
# volume, and a temporary directory for working data. Compares row/point/file
# counts with the backup's sources, then removes all scratch storage.
set -euo pipefail

BACKUP="${1:?usage: verify_restore.sh BACKUP_DIR}"
SCRATCH_DB="theology_rag_restore_check"
SCRATCH_QDRANT="theo_rag_qdrant_restore_check"
SCRATCH_PORT=16333
LIVE_QDRANT="${QDRANT_URL:-http://127.0.0.1:6333}"
TMP="$(mktemp -d)"
PSQL=(docker exec -i theo_rag_postgres psql -v ON_ERROR_STOP=1 -U raguser -At)

cleanup() {
  "${PSQL[@]}" -d theology_rag -c "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null 2>&1 || true
  docker rm -f "$SCRATCH_QDRANT" >/dev/null 2>&1 || true
  docker volume rm "${SCRATCH_QDRANT}_data" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

(cd "$BACKUP" && sha256sum -c --quiet SHA256SUMS) && echo "checksums OK"

echo "== postgres"
"${PSQL[@]}" -d theology_rag -c "DROP DATABASE IF EXISTS $SCRATCH_DB" -c "CREATE DATABASE $SCRATCH_DB" >/dev/null
docker exec -i theo_rag_postgres pg_restore -U raguser -d "$SCRATCH_DB" --no-owner < "$BACKUP/postgres.dump"
fail=0
for table in lessons parent_chunks child_chunks source_files schema_migrations; do
  live="$("${PSQL[@]}" -d theology_rag -c "SELECT count(*) FROM $table")"
  restored="$("${PSQL[@]}" -d "$SCRATCH_DB" -c "SELECT count(*) FROM $table")"
  status=OK; [[ "$live" == "$restored" ]] || { status=MISMATCH; fail=1; }
  printf '  %-18s live=%-8s restored=%-8s %s\n' "$table" "$live" "$restored" "$status"
done
embedded="$("${PSQL[@]}" -d "$SCRATCH_DB" -c "SELECT count(*) FROM child_chunks WHERE embedding IS NOT NULL")"
echo "  restored embedded child chunks: $embedded"

echo "== qdrant"
docker run -d --name "$SCRATCH_QDRANT" -p "127.0.0.1:$SCRATCH_PORT:6333" \
  -v "${SCRATCH_QDRANT}_data:/qdrant/storage" qdrant/qdrant:v1.7.0 >/dev/null
until curl -fsS "http://127.0.0.1:$SCRATCH_PORT/collections" >/dev/null 2>&1; do sleep 1; done
for snapshot in "$BACKUP"/qdrant/*.snapshot; do
  collection="$(basename "$snapshot" .snapshot)"
  curl -fsS -X POST "http://127.0.0.1:$SCRATCH_PORT/collections/$collection/snapshots/upload?priority=snapshot" \
    -F "snapshot=@$snapshot" >/dev/null
  live="$(curl -fsS "$LIVE_QDRANT/collections/$collection" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])')"
  restored="$(curl -fsS "http://127.0.0.1:$SCRATCH_PORT/collections/$collection" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])')"
  status=OK; [[ "$live" == "$restored" ]] || { status=MISMATCH; fail=1; }
  printf '  %-18s live=%-8s restored=%-8s %s\n' "$collection" "$live" "$restored" "$status"
done

echo "== working data"
tar -C "$TMP" -xzf "$BACKUP/working_data.tar.gz"
for dir in raw canonical; do
  echo "  $dir files restored: $(find "$TMP/$dir" -type f | wc -l)"
done

if [[ "$fail" -ne 0 ]]; then
  echo "RESTORE CHECK FAILED (counts differ; live data may have changed since the backup)" >&2
  exit 1
fi
echo "restore check passed: $BACKUP"
