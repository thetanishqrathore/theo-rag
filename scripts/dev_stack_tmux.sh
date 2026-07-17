#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="${SESSION_NAME:-rag-demo}"
TUNNEL_NAME="${TUNNEL_NAME:-rag-demo}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-4173}"
GPU_PANE_ENABLED="${GPU_PANE_ENABLED:-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WINDOW_NAME="stack"

usage() {
  cat <<'EOF'
Usage: scripts/dev_stack_tmux.sh [up|down|status|attach]

Commands:
  up      Start cloudflared + API + frontend preview (+ nvtop) in a tmux session and attach.
  down    Stop the tmux session.
  status  Show whether the session is running and list pane commands.
  attach  Attach to an existing session.

Environment overrides:
  SESSION_NAME  (default: rag-demo)
  TUNNEL_NAME   (default: rag-demo)
  API_PORT      (default: 8000)
  WEB_PORT      (default: 4173)
  GPU_PANE_ENABLED (default: 1; set to 0 to skip nvtop pane)
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

is_running() {
  tmux has-session -t "${SESSION_NAME}" 2>/dev/null
}

pane_count() {
  local count
  if count="$(tmux list-panes -t "${SESSION_NAME}:${WINDOW_NAME}" 2>/dev/null | wc -l | tr -d ' ')"; then
    echo "${count}"
    return 0
  fi
  echo "0"
}

start_stack() {
  require_cmd tmux
  require_cmd cloudflared

  local expected_panes=3
  if [[ "${GPU_PANE_ENABLED}" != "0" ]]; then
    expected_panes=4
  fi

  if is_running; then
    if [[ "$(pane_count)" -lt "${expected_panes}" ]]; then
      echo "Session '${SESSION_NAME}' exists but is incomplete. Recreating..."
      tmux kill-session -t "${SESSION_NAME}"
    else
      echo "Session '${SESSION_NAME}' already exists. Attaching..."
      exec tmux attach -t "${SESSION_NAME}"
    fi
  fi

  tmux new-session -d -s "${SESSION_NAME}" -n "${WINDOW_NAME}" -c "${REPO_ROOT}"
  local tunnel_pane api_pane web_pane gpu_pane
  tunnel_pane="$(tmux display-message -p -t "${SESSION_NAME}:${WINDOW_NAME}" '#{pane_id}')"

  tmux send-keys -t "${tunnel_pane}" "cloudflared tunnel run ${TUNNEL_NAME}" C-m

  api_pane="$(tmux split-window -d -h -t "${tunnel_pane}" -c "${REPO_ROOT}" -P -F '#{pane_id}')"
  tmux send-keys -t "${api_pane}" \
    ".venv/bin/python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port ${API_PORT}" C-m

  web_pane="$(tmux split-window -d -v -t "${api_pane}" -c "${REPO_ROOT}/frontend" -P -F '#{pane_id}')"
  tmux send-keys -t "${web_pane}" \
    "npm run preview -- --host 0.0.0.0 --port ${WEB_PORT}" C-m

  if [[ "${GPU_PANE_ENABLED}" != "0" ]]; then
    gpu_pane="$(tmux split-window -d -v -t "${tunnel_pane}" -c "${REPO_ROOT}" -P -F '#{pane_id}')"
    tmux send-keys -t "${gpu_pane}" \
      "if command -v nvtop >/dev/null 2>&1; then exec nvtop; elif command -v nvidia-smi >/dev/null 2>&1; then exec watch -n 1 nvidia-smi; else echo 'Neither nvtop nor nvidia-smi is installed.'; exec bash; fi" C-m
  fi

  tmux select-layout -t "${SESSION_NAME}:${WINDOW_NAME}" tiled
  tmux select-pane -t "${api_pane}"
  exec tmux attach -t "${SESSION_NAME}"
}

stop_stack() {
  require_cmd tmux
  if is_running; then
    tmux kill-session -t "${SESSION_NAME}"
    echo "Stopped tmux session '${SESSION_NAME}'."
  else
    echo "Session '${SESSION_NAME}' is not running."
  fi
}

status_stack() {
  require_cmd tmux
  if is_running; then
    echo "Session '${SESSION_NAME}' is running."
    if tmux list-panes -t "${SESSION_NAME}:${WINDOW_NAME}" >/dev/null 2>&1; then
      tmux list-panes -t "${SESSION_NAME}:${WINDOW_NAME}" -F "pane #{pane_index}: cmd=#{pane_current_command} dead=#{pane_dead}"
    else
      tmux list-panes -t "${SESSION_NAME}" -F "pane #{pane_index}: cmd=#{pane_current_command} dead=#{pane_dead}"
    fi
  else
    echo "Session '${SESSION_NAME}' is not running."
  fi
}

attach_stack() {
  require_cmd tmux
  if is_running; then
    exec tmux attach -t "${SESSION_NAME}"
  fi
  echo "Session '${SESSION_NAME}' is not running."
  exit 1
}

COMMAND="${1:-up}"
case "${COMMAND}" in
  up)
    start_stack
    ;;
  down)
    stop_stack
    ;;
  status)
    status_stack
    ;;
  attach)
    attach_stack
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: ${COMMAND}" >&2
    usage
    exit 1
    ;;
esac
