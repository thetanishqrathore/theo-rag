#!/usr/bin/env bash
# Start, stop and inspect the whole TheoRAG system on this workstation.
#
#   ./theorag.sh start [--local]   Containers -> API + web UI -> Cloudflare Tunnel,
#                                  waiting for each to be healthy. --local skips
#                                  the tunnel (site reachable on localhost/LAN only).
#   ./theorag.sh stop [--force]    Tunnel -> API -> containers, then unload
#                                  GPT-OSS from the GPU. Refuses while a corpus
#                                  build or backup runs, unless --force.
#   ./theorag.sh restart [--local] stop, then start.
#   ./theorag.sh status            Every component, GPU use, boot setting, public URL.
#   ./theorag.sh logs [api|tunnel] Follow a service's journal (default: api).
#   ./theorag.sh boot on|off       Start at boot (default) or stay off after a reboot.
#
# Scope: only TheoRAG's own pieces (theo-rag + theo-rag-tunnel user services,
# the theo_rag_* containers). Other containers on this machine (e.g. the
# Obsidian sync stack) and the Ollama system service are never stopped; stop
# only unloads TheoRAG's model so the GPU memory is free. No data is deleted.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_UNIT=theo-rag.service
TUNNEL_UNIT=theo-rag-tunnel.service
API_URL="http://127.0.0.1:8000"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
PUBLIC_URL="${PUBLIC_URL:-https://bibleai.raeonlabs.com}"
START_TIMEOUT_SEC="${START_TIMEOUT_SEC:-300}"

env_value() {
  # Read KEY=value from .env without sourcing it.
  [[ -f "$ROOT_DIR/.env" ]] && sed -n "s/^$1=//p" "$ROOT_DIR/.env" | tail -1
}

RAW_DATA_PATH="$(env_value RAW_DATA_PATH)"
DATA_ROOT="$(dirname "${RAW_DATA_PATH:-$HOME/theo-rag-data/raw}")"
OLLAMA_MODEL="$(env_value OLLAMA_MODEL)"
OLLAMA_MODEL="${OLLAMA_MODEL:-gpt-oss:120b}"

if [[ -t 1 ]]; then
  GREEN=$'\e[32m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
else
  GREEN=""; RED=""; YELLOW=""; BOLD=""; RESET=""
fi
ok()   { echo "  ${GREEN}✔${RESET} $*"; }
bad()  { echo "  ${RED}✘${RESET} $*"; }
warn() { echo "  ${YELLOW}!${RESET} $*"; }
step() { echo "${BOLD}$*${RESET}"; }

compose() { docker compose --project-directory "$ROOT_DIR" "$@"; }
unit_state() { systemctl --user is-active "$1" 2>/dev/null || true; }
unit_boot() { systemctl --user is-enabled "$1" 2>/dev/null || true; }
has_unit() { systemctl --user cat "$1" >/dev/null 2>&1; }

api_healthy() {
  curl -fsS --max-time 5 "$API_URL/api/health" 2>/dev/null | grep -q '"status": *"healthy"'
}

public_code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$PUBLIC_URL/api/health" 2>/dev/null || true
}

wait_until() {
  # wait_until SECONDS DESCRIPTION COMMAND...
  local limit="$1" what="$2"; shift 2
  local deadline=$((SECONDS + limit))
  until "$@"; do
    if (( SECONDS >= deadline )); then
      bad "$what: not ready after ${limit}s"
      return 1
    fi
    sleep 3
  done
}

corpus_busy() {
  # True while a corpus build or backup holds the shared lock.
  local lock="$DATA_ROOT/.corpus.lock"
  [[ -e "$lock" ]] || return 1
  ! flock -n "$lock" true 2>/dev/null
}

start() {
  local with_tunnel=1
  [[ "${1:-}" == "--local" ]] && with_tunnel=0

  step "1/3 Databases (PostgreSQL, Qdrant)"
  "$ROOT_DIR/scripts/ops/wait_for_deps.sh" | sed 's/^/  /'

  step "2/3 API + web UI"
  systemctl --user start "$API_UNIT"
  echo "  loading embedder, reranker and $OLLAMA_MODEL (first start can take ~1 min)..."
  wait_until "$START_TIMEOUT_SEC" "API health" api_healthy
  ok "healthy at $API_URL"

  step "3/3 Cloudflare Tunnel"
  if (( with_tunnel == 0 )); then
    warn "skipped (--local); public site stays offline"
  elif ! has_unit "$TUNNEL_UNIT"; then
    warn "$TUNNEL_UNIT not installed (scripts/ops/install_tunnel.sh); public site offline"
  else
    systemctl --user start "$TUNNEL_UNIT"
    if wait_until 60 "public URL" bash -c "[[ \$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 '$PUBLIC_URL/api/health') == 200 ]]"; then
      ok "online at $PUBLIC_URL"
    else
      warn "tunnel started but $PUBLIC_URL is not answering yet (DNS or Cloudflare); check: ./theorag.sh logs tunnel"
    fi
  fi
  echo
  echo "${GREEN}TheoRAG is running.${RESET} Local: http://localhost:8000"
}

stop() {
  if [[ "${1:-}" != "--force" ]] && corpus_busy; then
    bad "a corpus build or backup is running ($DATA_ROOT/.corpus.lock); stopping now could leave it half-done"
    echo "  wait for it to finish, or re-run: ./theorag.sh stop --force" >&2
    exit 3
  fi

  step "1/4 Cloudflare Tunnel (public site goes offline)"
  if has_unit "$TUNNEL_UNIT"; then systemctl --user stop "$TUNNEL_UNIT"; ok stopped; else warn "not installed"; fi

  step "2/4 API + web UI"
  systemctl --user stop "$API_UNIT"
  ok "stopped (an answer in progress, if any, was cut off)"

  step "3/4 Databases (PostgreSQL, Qdrant)"
  compose stop 2>&1 | sed 's/^/  /'
  ok "stopped (data kept in Docker volumes)"

  step "4/4 GPU memory"
  local loaded
  loaded="$(curl -fsS --max-time 5 "$OLLAMA_URL/api/ps" 2>/dev/null | python3 -c 'import json,sys; print(" ".join(m["name"] for m in json.load(sys.stdin).get("models", [])))' 2>/dev/null || true)"
  if [[ " $loaded " == *" $OLLAMA_MODEL "* ]]; then
    curl -fsS --max-time 30 "$OLLAMA_URL/api/generate" -d "{\"model\":\"$OLLAMA_MODEL\",\"keep_alive\":0}" >/dev/null
    ok "unloaded $OLLAMA_MODEL (Ollama itself keeps running)"
  else
    ok "$OLLAMA_MODEL already unloaded (the API frees it on shutdown)"
  fi
  echo
  echo "${GREEN}TheoRAG is stopped.${RESET} It will start again at the next boot unless you run: ./theorag.sh boot off"
}

status() {
  local rc=0 state
  step "Services"
  for unit in "$API_UNIT" "$TUNNEL_UNIT"; do
    state="$(unit_state "$unit")"
    if [[ "$state" == active ]]; then ok "$unit: active (boot: $(unit_boot "$unit"))"
    else bad "$unit: ${state:-not installed} (boot: $(unit_boot "$unit"))"; rc=1; fi
  done

  step "Containers"
  for name in theo_rag_postgres theo_rag_qdrant; do
    state="$(docker inspect -f '{{.State.Status}}{{if .State.Health}} ({{.State.Health.Status}}){{end}}' "$name" 2>/dev/null || echo missing)"
    if [[ "$state" == running* ]]; then ok "$name: $state"; else bad "$name: $state"; rc=1; fi
  done

  step "Health"
  if api_healthy; then ok "API $API_URL/api/health: healthy"; else bad "API not healthy"; rc=1; fi
  local code; code="$(public_code)"
  if [[ "$code" == 200 ]]; then ok "public $PUBLIC_URL: online"; else bad "public $PUBLIC_URL: HTTP ${code:-none}"; rc=1; fi
  if corpus_busy; then warn "a corpus build or backup is running"; fi

  step "GPU"
  curl -fsS --max-time 5 "$OLLAMA_URL/api/ps" 2>/dev/null | python3 -c '
import json, sys
models = json.load(sys.stdin).get("models", [])
for m in models:
    name, vram, ctx = m.get("name"), m.get("size_vram", 0) / 2**30, m.get("context_length", "?")
    print(f"  {name}: {vram:.1f} GiB in VRAM, context {ctx}")
if not models:
    print("  no Ollama model loaded")' || warn "Ollama not answering at $OLLAMA_URL"
  if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader \
      | awk -F', ' '{print "  GPU memory " $1 " / " $2 ", utilisation " $3}'
  fi
  return "$rc"
}

logs() {
  case "${1:-api}" in
    api) journalctl --user -u "$API_UNIT" -f ;;
    tunnel) journalctl --user -u "$TUNNEL_UNIT" -f ;;
    *) echo "usage: ./theorag.sh logs [api|tunnel]" >&2; exit 1 ;;
  esac
}

boot() {
  local units=("$API_UNIT")
  has_unit "$TUNNEL_UNIT" && units+=("$TUNNEL_UNIT")
  case "${1:-}" in
    on) systemctl --user enable "${units[@]}" 2>&1 | sed 's/^/  /'; ok "starts at boot: ${units[*]}" ;;
    off) systemctl --user disable "${units[@]}" 2>&1 | sed 's/^/  /'; ok "stays off after reboot (containers stay stopped too once ./theorag.sh stop has run)" ;;
    *) echo "usage: ./theorag.sh boot on|off" >&2; exit 1 ;;
  esac
}

case "${1:-}" in
  start) shift; start "$@" ;;
  stop) shift; stop "$@" ;;
  restart) shift; stop; start "$@" ;;
  status) status ;;
  logs) shift; logs "$@" ;;
  boot) shift; boot "$@" ;;
  *) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
