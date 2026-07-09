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
  local dir py
  for dir in "$VENV_DIR" ".venv" "venv"; do
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
    log "Creating virtual environment: $VENV_DIR"
    "$base_python" -m venv "$VENV_DIR"
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

PYTHON_BIN="$(select_python)"
ensure_dependencies "$PYTHON_BIN"

URL="http://127.0.0.1:${PORT}"

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

log "Starting Web UI at $URL (host=$HOST, reload=$RELOAD)"
exec "${cmd[@]}"
