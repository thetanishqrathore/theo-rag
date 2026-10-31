#!/usr/bin/env bash
# Install theo-rag as a systemd *user* service that starts at boot.
#   scripts/ops/install_service.sh            # build frontend, install, enable, start
#   scripts/ops/install_service.sh --no-build # skip the frontend build
# Requires lingering for boot start without a login: loginctl enable-linger "$USER"
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/theo-rag.service"
HOST="${THEO_RAG_HOST:-0.0.0.0}"
PORT="${THEO_RAG_PORT:-8000}"
NODE_BIN="${NODE_BIN:-$(dirname "$(command -v node)")}"

if [[ "${1:-}" != "--no-build" ]]; then
  PATH="$NODE_BIN:$PATH" npm --prefix "$ROOT_DIR/frontend" ci --no-audit --no-fund
  PATH="$NODE_BIN:$PATH" npm --prefix "$ROOT_DIR/frontend" run build
fi

[[ -x "$ROOT_DIR/.venv/bin/uvicorn" ]] || { echo "missing $ROOT_DIR/.venv (run: uv sync)" >&2; exit 1; }
[[ -f "$ROOT_DIR/.env" ]] || { echo "missing $ROOT_DIR/.env (copy .env.public.example)" >&2; exit 1; }

mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<UNIT
[Unit]
Description=theo-rag API and web UI (port $PORT)
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
WorkingDirectory=$ROOT_DIR
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStartPre="$ROOT_DIR/scripts/ops/wait_for_deps.sh"
# One worker: the GPU-resident embedder/reranker and the generation semaphore are per process.
ExecStart="$ROOT_DIR/.venv/bin/uvicorn" src.api.main:app --host $HOST --port $PORT --workers 1
Restart=always
RestartSec=5
TimeoutStartSec=300
TimeoutStopSec=60

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable theo-rag.service
systemctl --user restart theo-rag.service
echo "installed $UNIT"
systemctl --user --no-pager status theo-rag.service | head -5
