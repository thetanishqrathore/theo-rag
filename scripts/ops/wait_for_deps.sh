#!/usr/bin/env bash
# Start infrastructure containers and wait until PostgreSQL, Qdrant and Ollama
# answer. Used as ExecStartPre by the theo-rag systemd unit; safe to run by hand.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TIMEOUT_SEC="${DEPS_TIMEOUT_SEC:-180}"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"

docker compose --project-directory "$ROOT_DIR" up -d postgres qdrant >/dev/null

deadline=$((SECONDS + TIMEOUT_SEC))
wait_for() {
  local name="$1"; shift
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "timed out waiting for $name" >&2
      return 1
    fi
    sleep 2
  done
  echo "$name ready"
}

wait_for postgres docker exec theo_rag_postgres pg_isready -U raguser -d theology_rag -h 127.0.0.1
wait_for qdrant curl -fsS "$QDRANT_URL/collections"
wait_for ollama curl -fsS "$OLLAMA_URL/api/version"
