#!/usr/bin/env bash
# Preserve an immutable copy of EVERY original corpus file (included and excluded).
#
#   scripts/ops/archive_originals.sh CORPUS_ROOT [DEST_DIR]
#
# Writes DEST_DIR/originals_<revision>/ with:
#   originals.sha256   sha256 + relative path of every file (sorted)
#   originals.tar      the files themselves
#   REVISION           sha256 of originals.sha256 (the corpus revision id)
# The directory is made read-only. Re-running on an unchanged corpus is a no-op;
# a changed corpus produces a new revision directory. DEST_DIR defaults to
# $DATA_ROOT/originals (DATA_ROOT = parent of RAW_DATA_PATH in .env).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORPUS="$(cd "${1:?usage: archive_originals.sh CORPUS_ROOT [DEST_DIR]}" && pwd)"
env_value() { [[ -f "$ROOT_DIR/.env" ]] && sed -n "s/^$1=//p" "$ROOT_DIR/.env" | tail -1; }
RAW_DATA_PATH="$(env_value RAW_DATA_PATH)"
DEST="${2:-$(dirname "${RAW_DATA_PATH:?RAW_DATA_PATH must be set in .env}")/originals}"
mkdir -p "$DEST"

TMP="$(mktemp -d "$DEST/.incoming.XXXXXX")"
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT

(cd "$CORPUS" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$TMP/originals.sha256"
REVISION="$(sha256sum "$TMP/originals.sha256" | cut -c1-64)"
TARGET="$DEST/originals_${REVISION:0:16}"

if [[ -d "$TARGET" ]]; then
  echo "unchanged: corpus revision ${REVISION:0:16} already archived at $TARGET"
  exit 0
fi

echo "$REVISION" > "$TMP/REVISION"
(cd "$CORPUS" && cut -c67- "$TMP/originals.sha256" | tr '\n' '\0' | tar --null -T - -cf "$TMP/originals.tar")

# The tar must contain exactly the hashed files with identical content.
VERIFY="$(mktemp -d)"
tar -xf "$TMP/originals.tar" -C "$VERIFY"
(cd "$VERIFY" && sha256sum -c --quiet "$TMP/originals.sha256")
[[ "$(cd "$VERIFY" && find . -type f | wc -l)" == "$(wc -l < "$TMP/originals.sha256")" ]]
rm -rf "$VERIFY"

mv "$TMP" "$TARGET"
trap - EXIT
chmod -R a-w "$TARGET"
echo "archived $(wc -l < "$TARGET/originals.sha256") files, revision ${REVISION:0:16} -> $TARGET"
