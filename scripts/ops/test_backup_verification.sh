#!/usr/bin/env bash
# Negative tests for verify_restore.sh: a damaged or inconsistent backup must FAIL.
#
#   scripts/ops/test_backup_verification.sh BACKUP_DIR
#
# Works on hard-linked copies of BACKUP_DIR; any file a case modifies is
# unlinked first, so the real backup is never changed. Cases:
#   corrupted_archive   one byte flipped in working_data.tar.gz
#   missing_file        a qdrant snapshot deleted
#   wrong_ids           inventory child_chunks digest changed, count unchanged,
#                       SHA256SUMS regenerated so checksums pass
#   good_copy           untouched copy must pass
set -uo pipefail

SOURCE="$(cd "${1:?usage: test_backup_verification.sh BACKUP_DIR}" && pwd)"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d "$(dirname "$SOURCE")/.verify-test.XXXXXX")"
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT
failures=0

copy_backup() {
  cp -al "$SOURCE" "$WORK/$1"
  echo "$WORK/$1"
}

unlink_copy() {  # replace a hard link with a private copy before editing
  cp --remove-destination "$(readlink -f "$1")" "$1.tmp" && mv "$1.tmp" "$1"
}

resum() {
  local sums="$WORK/sums.$$"
  (cd "$1" && rm -f SHA256SUMS && find . -type f -print0 | sort -z | xargs -0 sha256sum > "$sums" && mv "$sums" SHA256SUMS)
}

expect() {  # expect <pass|fail> <name> <dir> [required output pattern]
  local want="$1" name="$2" dir="$3" pattern="${4:-}"
  local out rc
  out="$("$ROOT_DIR/scripts/ops/verify_restore.sh" "$dir" 2>&1)"; rc=$?
  if [[ "$want" == pass && $rc -eq 0 ]] || [[ "$want" == fail && $rc -ne 0 && ( -z "$pattern" || "$out" == *"$pattern"* ) ]]; then
    echo "PASS  $name (exit $rc)"
  else
    echo "FAIL  $name: expected $want, got exit $rc"
    echo "$out" | tail -5
    failures=$((failures + 1))
  fi
}

d="$(copy_backup corrupted_archive)"
unlink_copy "$d/working_data.tar.gz"
printf '\xff' | dd of="$d/working_data.tar.gz" bs=1 seek=4096 conv=notrunc status=none
expect fail corrupted_archive "$d" "CHECKSUM FAILURE"

d="$(copy_backup missing_file)"
rm -f "$d"/qdrant/dean_chunks.snapshot
expect fail missing_file "$d" "CHECKSUM FAILURE"

d="$(copy_backup wrong_ids)"
rm -f "$d/inventory.json"
python3 - "$SOURCE/inventory.json" "$d/inventory.json" <<'EOF'
import json, sys
inv = json.load(open(sys.argv[1]))
inv["tables"]["child_chunks"]["digest"] = "0" * 64   # same count, different IDs
json.dump(inv, open(sys.argv[2], "w"), indent=2)
EOF
resum "$d"
expect fail wrong_ids "$d" "MISMATCH table child_chunks"

d="$(copy_backup good_copy)"
expect pass good_copy "$d"

echo "$failures failure(s)"
exit "$failures"
