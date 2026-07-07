# SIRIUS Desktop (Windows & macOS)

Native desktop packaging of **SIRIUS** (SDTM Intelligent Recommendation &
Inference Unified System). It is a thin [Electron](https://www.electronjs.org/)
shell that:

1. starts the SIRIUS FastAPI backend (`app:app`) on a free local port,
2. waits for it to become healthy, and
3. opens a native window on `http://127.0.0.1:<port>/` — the exact same web UI
   that ships in `src/web/static/`.

Because it reuses the real backend, every feature works identically to the web
build (upload & preprocess, 4-level cascade recommendation, spec generation,
session isolation), just in a standalone application window with no browser.

```
┌──────────────────────────┐        spawn         ┌───────────────────────────┐
│  Electron main (main.js)  │ ───────────────────▶ │  uvicorn app:app (FastAPI) │
│  · picks free port        │  loadURL 127.0.0.1   │  · serves src/web/static   │
│  · BrowserWindow          │ ◀─────────────────── │  · /api/* endpoints        │
└──────────────────────────┘       HTTP            └───────────────────────────┘
```

## Prerequisites

- **Node.js** 18+ and npm (build machine)
- A working **SIRIUS Python backend** — either
  - a Python 3.11+ environment with `requirements.txt` installed (dev / fallback mode), or
  - a self-contained backend binary (recommended for shipping — see *Production backend* below).

## Develop / run locally

From this `desktop/` directory:

```bash
npm install
npm start
```

In dev mode the shell finds a Python interpreter automatically, preferring, in
order: `SIRIUS_PYTHON` → a repo virtualenv (`../venv` or `../.venv`) →
`python3`/`python` on `PATH`. It runs the backend from the repo root
(`desktop/..`), so `app.py`, `src/`, `scripts/`, and `data/` are used in
place.

Useful environment variables:

| Variable             | Purpose                                                        |
|----------------------|----------------------------------------------------------------|
| `SIRIUS_PYTHON`      | Explicit Python interpreter to run `uvicorn app:app`.          |
| `SIRIUS_BACKEND_BIN` | Path to a self-contained backend executable (skips Python).    |
| `SIRIUS_PORT`        | Force a specific port instead of auto-selecting a free one. Read by the shell (passed to the backend via `--port`); the backend itself does not read this variable. |

Remember the backend still needs its own configuration (API keys, etc.) — copy
`../env_template.txt` to `.env` at the repo root just like the web deployment.

## Build installers

```bash
# Windows installer (.exe / NSIS)   → dist/
npm run dist:win

# macOS disk image (.dmg)           → dist/
npm run dist:mac

# current platform
npm run dist
```

`electron-builder` config lives in `package.json` (`build` key):

- **Windows** → NSIS installer, x64, desktop + start-menu shortcuts, user can
  choose the install directory.
- **macOS** → DMG for both `arm64` and `x64`.
- The backend source (`app.py`, `src/`, `scripts/`, `data/`, `requirements.txt`)
  is copied into the package under `resources/backend/` via `extraResources`.
- At runtime, packaged apps use a writable workspace under Electron's
  `app.getPath("userData")` (`.../backend/`) for uploads, processed files,
  generated specs, caches, sessions, and other mutable `data/*` paths. Bundled
  `data/` files are copied there only when missing, while `scripts/` are
  refreshed on each launch.

App icons can be generated from `build/icon.svg` — see `build/README.md`.

> ⚠️ **The default build bundles backend _source_, not a runnable backend.** It
> does not include a Python interpreter or the installed dependencies, so an app
> launched from Finder / the Start Menu will only start if a compatible Python
> (with `requirements.txt` installed) is discoverable, or a self-contained
> backend binary is present (see below). For a distributable that "just works"
> on a clean machine, bundle a frozen backend as described in
> *Production backend*.

> Note: cross-building macOS installers is only fully supported **on macOS**
> (code-signing/notarization included). Build the `.dmg` on a Mac and the
> Windows installer on Windows or Linux.

## Production backend (recommended)

Shipping a Python interpreter to end users is fragile. For a self-contained
app, freeze the backend with [PyInstaller](https://pyinstaller.org/) and point
the shell at it:

```bash
# from the repo root, in your configured Python env
pyinstaller --onefile --name sirius-backend \
  --add-data "scripts:scripts" \
  --collect-all src \
  --add-data "src/web/static:src/web/static" \
  app.py
```

Then ship the binary one of two ways:

- **Bundle it (recommended).** Add it to `extraResources` so it lands at
  `resources/backend/sirius-backend` (or `sirius-backend.exe` on Windows).
  `main.js` auto-detects that conventional name at startup — no env var and no
  code changes needed.
- **Point at it explicitly.** Set `SIRIUS_BACKEND_BIN` to the binary's path.

The shell always prefers a backend binary over the Python fallback, so a
bundled binary makes the app self-contained on a clean machine. The backend
entrypoint also dispatches `sirius-backend scripts/<tool>.py ...`, so existing
upload/preprocess flows keep working when the frozen executable is used as
`sys.executable`.

## Files

| File            | Role                                                             |
|-----------------|------------------------------------------------------------------|
| `main.js`       | Electron main process — spawns/monitors backend, opens window.   |
| `preload.js`    | Locked-down preload (contextIsolation on; minimal surface).      |
| `package.json`  | npm scripts + `electron-builder` (win/mac) configuration.        |
| `build/`        | Packaging resources (app icons).                                 |
