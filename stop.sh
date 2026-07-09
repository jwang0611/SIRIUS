#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
RUN_DIR="${RUN_DIR:-$ROOT/.run}"
PID_FILE="${PID_FILE:-$RUN_DIR/isdtaim-${PORT}.pid}"
TIMEOUT="${TIMEOUT:-10}"
KILL_PORT="${KILL_PORT:-1}"

log() {
  printf '[iSDTaiM] %s\n' "$*" >&2
}

is_pid_running() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1
}

wait_for_exit() {
  local pid="$1"
  local elapsed=0
  while is_pid_running "$pid"; do
    if (( elapsed >= TIMEOUT )); then
      return 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 0
}

stop_pid() {
  local pid="$1"
  local source="$2"

  if ! is_pid_running "$pid"; then
    log "Skipping stale $source PID $pid"
    return 1
  fi

  log "Stopping $source PID $pid"
  kill "$pid" >/dev/null 2>&1 || true
  if ! wait_for_exit "$pid"; then
    log "PID $pid did not exit within ${TIMEOUT}s; forcing it"
    kill -9 "$pid" >/dev/null 2>&1 || true
    wait_for_exit "$pid" || true
  fi
  return 0
}

stopped=0

if [[ -f "$PID_FILE" ]]; then
  pid="$(<"$PID_FILE")"
  if stop_pid "$pid" "PID file"; then
    stopped=1
  fi
  rm -f "$PID_FILE"
fi

if [[ "$KILL_PORT" == "1" ]] && command -v lsof >/dev/null 2>&1; then
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    if stop_pid "$pid" "port $PORT"; then
      stopped=1
    fi
  done < <(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sort -u)
fi

if (( stopped == 0 )); then
  log "No running iSDTaiM service found for port $PORT"
else
  log "Service stopped for port $PORT"
fi
