#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-1}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
CREATE_VENV="${CREATE_VENV:-1}"
INSTALL_DEPS="${INSTALL_DEPS:-1}"
VENV_DIR="${VENV_DIR:-venv}"
MIN_PYTHON="3.11"
RUN_DIR="${RUN_DIR:-$ROOT/.run}"
PID_FILE="${PID_FILE:-$RUN_DIR/isdtaim-${PORT}.pid}"

export PYTHONPATH="$ROOT"
export PYTHONIOENCODING="utf-8"

log() {
  printf '[iSDTaiM] %s\n' "$*" >&2
}

fail() {
  printf '[iSDTaiM] ERROR: %s\n' "$*" >&2
  exit 1
}

python_version_ok() {
  "$1" - "$MIN_PYTHON" <<'PY'
import sys

required = tuple(map(int, sys.argv[1].split(".")))
raise SystemExit(0 if sys.version_info[:2] >= required else 1)
PY
}

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    command -v "$PYTHON" >/dev/null 2>&1 || fail "PYTHON is set to '$PYTHON' but it is not executable"
    python_version_ok "$PYTHON" || fail "$PYTHON must be Python $MIN_PYTHON or newer"
    printf '%s\n' "$PYTHON"
    return
  fi

  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_version_ok "$candidate"; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  fail "Python $MIN_PYTHON or newer is required. Install Python 3.11+ or run with PYTHON=/path/to/python."
}

venv_python() {
  local dir="$1"
  if [[ -x "$dir/bin/python" ]]; then
    printf '%s\n' "$dir/bin/python"
  fi
}

select_python() {
  local candidate dir existing found py
  local venv_search_dirs=("$VENV_DIR")
  for candidate in ".venv" "venv"; do
    found=0
    for existing in "${venv_search_dirs[@]}"; do
      if [[ "$existing" == "$candidate" ]]; then
        found=1
        break
      fi
    done
    if [[ "$found" == "0" ]]; then
      venv_search_dirs+=("$candidate")
    fi
  done

  for dir in "${venv_search_dirs[@]}"; do
    py="$(venv_python "$dir" || true)"
    if [[ -n "$py" ]]; then
      if python_version_ok "$py"; then
        log "Using virtual environment: $dir"
        printf '%s\n' "$py"
        return
      fi
      log "Skipping $dir because it uses Python older than $MIN_PYTHON"
    fi
  done

  local base_python
  base_python="$(find_python)"
  if [[ "$CREATE_VENV" == "1" ]]; then
    local venv_args=()
    py="$(venv_python "$VENV_DIR" || true)"
    if [[ -n "$py" ]] && ! python_version_ok "$py"; then
      log "Recreating incompatible virtual environment: $VENV_DIR"
      venv_args+=(--clear)
    else
      log "Creating virtual environment: $VENV_DIR"
    fi
    "$base_python" -m venv "${venv_args[@]}" "$VENV_DIR"
    printf '%s\n' "$VENV_DIR/bin/python"
    return
  fi

  log "Using system Python: $base_python"
  printf '%s\n' "$base_python"
}

ensure_dependencies() {
  local py="$1"

  if "$py" - <<'PY' >/dev/null 2>&1
import fastapi
import slowapi
import uvicorn
PY
  then
    return
  fi

  [[ "$INSTALL_DEPS" == "1" ]] || fail "Dependencies are missing. Run '$py -m pip install -r requirements.txt' or set INSTALL_DEPS=1."

  log "Installing dependencies from requirements.txt"
  "$py" -m pip install -r requirements.txt
}

is_pid_running() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1
}

PYTHON_BIN="$(select_python)"
ensure_dependencies "$PYTHON_BIN"

URL="http://127.0.0.1:${PORT}"
mkdir -p "$RUN_DIR"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(<"$PID_FILE")"
  if is_pid_running "$existing_pid"; then
    fail "Service already appears to be running on port $PORT with PID $existing_pid. Run './stop.sh' first."
  fi
  log "Removing stale PID file: $PID_FILE"
  rm -f "$PID_FILE"
fi

if [[ "$OPEN_BROWSER" == "1" && -z "${CI:-}" ]]; then
  if command -v open >/dev/null 2>&1; then
    (sleep 2 && open "$URL" >/dev/null 2>&1) &
  elif command -v xdg-open >/dev/null 2>&1; then
    (sleep 2 && xdg-open "$URL" >/dev/null 2>&1) &
  fi
fi

cmd=("$PYTHON_BIN" -m uvicorn app:app --host "$HOST" --port "$PORT")
if [[ "$RELOAD" == "1" ]]; then
  cmd+=(--reload)
fi

cleanup() {
  rm -f "$PID_FILE"
}

stop_server() {
  if [[ -n "${SERVER_PID:-}" ]] && is_pid_running "$SERVER_PID"; then
    log "Stopping Web UI process $SERVER_PID"
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}

handle_signal() {
  local exit_code="$1"
  stop_server
  cleanup
  exit "$exit_code"
}

trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
trap cleanup EXIT

log "Starting Web UI at $URL (host=$HOST, reload=$RELOAD)"
"${cmd[@]}" &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" > "$PID_FILE"
log "PID file: $PID_FILE"
wait "$SERVER_PID"
