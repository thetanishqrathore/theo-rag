#!/usr/bin/env bash
# Copy a verified backup to independent storage (external drive, NAS mount,
# another machine over ssh) and verify it THERE.
#
#   scripts/ops/copy_offsite.sh BACKUP_DIR DEST          # local path or mount
#   scripts/ops/copy_offsite.sh BACKUP_DIR host:/path    # over ssh (rsync)
#
# Copies file contents (hard links to the originals archive become real files),
# then re-runs `sha256sum -c SHA256SUMS` at the destination. Refuses a
# destination on the same filesystem as the backup, which would not survive a
# disk failure.
set -euo pipefail

BACKUP="$(cd "${1:?usage: copy_offsite.sh BACKUP_DIR DEST}" && pwd)"
DEST="${2:?usage: copy_offsite.sh BACKUP_DIR DEST}"
NAME="$(basename "$BACKUP")"

(cd "$BACKUP" && sha256sum -c --quiet SHA256SUMS) || { echo "source backup fails its checksums" >&2; exit 1; }

if [[ "$DEST" == *:* ]]; then
  host="${DEST%%:*}"; path="${DEST#*:}"
  rsync -a --info=progress2 "$BACKUP/" "$host:$path/$NAME/"
  ssh "$host" "cd '$path/$NAME' && sha256sum -c --quiet SHA256SUMS"
else
  mkdir -p "$DEST"
  if [[ "$(stat -c %d "$BACKUP")" == "$(stat -c %d "$DEST")" ]]; then
    echo "refusing: $DEST is on the same filesystem as the backup" >&2
    exit 2
  fi
  rsync -a --info=progress2 "$BACKUP/" "$DEST/$NAME/"
  (cd "$DEST/$NAME" && sha256sum -c --quiet SHA256SUMS)
fi
echo "off-site copy verified: $DEST/$NAME"
