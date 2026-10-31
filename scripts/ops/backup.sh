#!/usr/bin/env bash
# Back up PostgreSQL, Qdrant and the raw/canonical working data.
#
#   scripts/ops/backup.sh [DEST_DIR]
#
# Produces DEST_DIR/<timestamp>/ containing:
#   postgres.dump            pg_dump custom format (pg_restore-able)
#   qdrant/<collection>.snapshot
#   working_data.tar.gz      raw copies, canonical HTML, manifest, build reports
#   SHA256SUMS
# DEST_DIR defaults to $DATA_ROOT/backups (DATA_ROOT = parent of RAW_DATA_PATH).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
env_value() { [[ -f .env ]] && sed -n "s/^$1=//p" .env | tail -1; }

RAW_DATA_PATH="$(env_value RAW_DATA_PATH)"
DATA_ROOT="$(dirname "${RAW_DATA_PATH:?RAW_DATA_PATH must be set in .env}")"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
DEST="${1:-$DATA_ROOT/backups}/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$DEST/qdrant"

echo "postgres -> $DEST/postgres.dump"
docker exec theo_rag_postgres pg_dump -U raguser -d theology_rag -Fc > "$DEST/postgres.dump"

for collection in $(curl -fsS "$QDRANT_URL/collections" | python3 -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin)["result"]["collections"]))'); do
  name="$(curl -fsS -X POST "$QDRANT_URL/collections/$collection/snapshots" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')"
  echo "qdrant $collection -> $DEST/qdrant/$collection.snapshot"
  curl -fsS "$QDRANT_URL/collections/$collection/snapshots/$name" -o "$DEST/qdrant/$collection.snapshot"
  curl -fsS -X DELETE "$QDRANT_URL/collections/$collection/snapshots/$name" >/dev/null
done

echo "working data -> $DEST/working_data.tar.gz"
members=()
for item in raw canonical source_manifest.json build_reports; do
  [[ -e "$DATA_ROOT/$item" ]] && members+=("$item")
done
tar -C "$DATA_ROOT" -czf "$DEST/working_data.tar.gz" "${members[@]}"

(cd "$DEST" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
du -sh "$DEST"
echo "backup complete: $DEST"
