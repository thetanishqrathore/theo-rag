#!/usr/bin/env bash
# Back up everything needed to rebuild or restore theo-rag on another machine.
#
#   scripts/ops/backup.sh [DEST_DIR]
#
# Produces DEST_DIR/<timestamp>/ containing:
#   postgres.dump              pg_dump custom format
#   qdrant/<collection>.snapshot
#   working_data.tar.gz        raw copies, canonical HTML, manifest, build reports
#   working_files.sha256       per-file hashes of everything in working_data.tar.gz
#   originals/                 immutable archive of ALL original corpus files
#                              (hard links to $DATA_ROOT/originals/<revision>)
#   config/                    .env, compose file, systemd unit, tool identities
#   inventory.json             expected tables/IDs/collections (see backup_inventory.py)
#   SHA256SUMS                 checksum of every file above
#
# Holds $DATA_ROOT/.corpus.lock for the whole run, so no corpus build can
# write while the database, index and files are captured. DEST_DIR defaults to
# $DATA_ROOT/backups (DATA_ROOT = parent of RAW_DATA_PATH). config/.env contains
# credentials: keep backups private.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
env_value() { [[ -f .env ]] && sed -n "s/^$1=//p" .env | tail -1; }

RAW_DATA_PATH="$(env_value RAW_DATA_PATH)"
DATA_ROOT="$(dirname "${RAW_DATA_PATH:?RAW_DATA_PATH must be set in .env}")"
DATABASE_URL="$(env_value DATABASE_URL)"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
MANIFEST="${MANIFEST:-$DATA_ROOT/source_manifest.json}"
DEST="${1:-$DATA_ROOT/backups}/$(date +%Y%m%d_%H%M%S)"
PY="$ROOT_DIR/.venv/bin/python"

exec 9>"$DATA_ROOT/.corpus.lock"
flock -n 9 || { echo "a corpus build (or another backup) holds $DATA_ROOT/.corpus.lock; retry later" >&2; exit 3; }

mkdir -p "$DEST/qdrant" "$DEST/config" "$DEST/originals"

# Originals: archive (no-op when unchanged) and link the matching revision.
CORPUS_ROOT="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["root"])' "$MANIFEST")"
scripts/ops/archive_originals.sh "$CORPUS_ROOT" "$DATA_ROOT/originals" >/dev/null
REVISION_DIR="$(ls -td "$DATA_ROOT"/originals/originals_* | head -1)"
expected_rev="$(cat "$REVISION_DIR/REVISION")"
current_rev="$( (cd "$CORPUS_ROOT" && find . -type f -print0 | sort -z | xargs -0 sha256sum) | sha256sum | cut -c1-64)"
[[ "$expected_rev" == "$current_rev" ]] || { echo "corpus changed during backup" >&2; exit 1; }
ln "$REVISION_DIR"/{originals.tar,originals.sha256,REVISION} "$DEST/originals/"
echo "originals: revision ${expected_rev:0:16} ($(wc -l < "$REVISION_DIR/originals.sha256") files)"

echo "postgres -> postgres.dump"
docker exec theo_rag_postgres pg_dump -U raguser -d theology_rag -Fc > "$DEST/postgres.dump"

for collection in $(curl -fsS "$QDRANT_URL/collections" | "$PY" -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin)["result"]["collections"]))'); do
  name="$(curl -fsS -X POST "$QDRANT_URL/collections/$collection/snapshots" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')"
  echo "qdrant $collection -> qdrant/$collection.snapshot"
  curl -fsS "$QDRANT_URL/collections/$collection/snapshots/$name" -o "$DEST/qdrant/$collection.snapshot"
  curl -fsS -X DELETE "$QDRANT_URL/collections/$collection/snapshots/$name" >/dev/null
done

echo "working data -> working_data.tar.gz"
members=()
for item in raw canonical source_manifest.json build_reports; do
  [[ -e "$DATA_ROOT/$item" ]] && members+=("$item")
done
(cd "$DATA_ROOT" && find "${members[@]}" -type f -print0 | sort -z | xargs -0 sha256sum) > "$DEST/working_files.sha256"
tar -C "$DATA_ROOT" -czf "$DEST/working_data.tar.gz" "${members[@]}"

echo "config and tool identities -> config/"
cp .env docker-compose.yml uv.lock "$DEST/config/"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/theo-rag.service"
[[ -f "$unit" ]] && cp "$unit" "$DEST/config/"
{
  echo "git_sha=$(git rev-parse HEAD)"
  echo "git_branch=$(git rev-parse --abbrev-ref HEAD)"
  echo "git_dirty=$(git status --porcelain --untracked-files=no | wc -l) tracked file(s) modified"
  echo "corpus_revision=$expected_rev"
  echo "corpus_root=$CORPUS_ROOT"
  curl -fsS localhost:11434/api/tags | "$PY" -c 'import json,sys; [print("ollama_model=%s %s" % (m["name"], m["digest"])) for m in json.load(sys.stdin)["models"]]' || true
  echo "ollama_version=$(curl -fsS localhost:11434/api/version || echo unknown)"
  for image in $(docker compose config --images); do
    echo "image=$image $(docker image inspect "$image" --format '{{index .RepoDigests 0}}' 2>/dev/null || echo unknown)"
  done
  echo "soffice=$(command -v soffice || echo missing)"
  echo "marker=$(~/.local/share/uv/tools/marker-pdf/bin/python -c 'import importlib.metadata as m; print(m.version("marker-pdf"))' 2>/dev/null || echo unknown)"
  echo "nvidia_driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || echo unknown)"
} > "$DEST/config/tool_identities.txt"

DATABASE_URL="$DATABASE_URL" "$PY" scripts/ops/backup_inventory.py snapshot \
  --backup-dir "$DEST" --database-url "$DATABASE_URL" --qdrant-url "$QDRANT_URL" \
  --manifest "$MANIFEST" --originals-manifest "$DEST/originals/originals.sha256"

(cd "$DEST" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
du -sh "$DEST"
echo "backup complete: $DEST"
