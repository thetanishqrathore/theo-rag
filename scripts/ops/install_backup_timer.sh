#!/usr/bin/env bash
# Opt-in: nightly backup + isolated restore verification as a systemd user timer.
#   scripts/ops/install_backup_timer.sh [HH:MM]     (default 03:30)
# Failures show in `systemctl --user status theo-rag-backup` and the journal.
# Off-site copies still need a destination: see scripts/ops/copy_offsite.sh.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WHEN="${1:-03:30}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/theo-rag-backup.service" <<UNIT
[Unit]
Description=theo-rag backup and restore verification

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'set -e; out=\$("$ROOT_DIR/scripts/ops/backup.sh" | tail -1); dir=\${out#backup complete: }; "$ROOT_DIR/scripts/ops/verify_restore.sh" "\$dir"'
UNIT

cat > "$UNIT_DIR/theo-rag-backup.timer" <<UNIT
[Unit]
Description=Nightly theo-rag backup

[Timer]
OnCalendar=*-*-* $WHEN:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now theo-rag-backup.timer
systemctl --user list-timers theo-rag-backup.timer --no-pager
