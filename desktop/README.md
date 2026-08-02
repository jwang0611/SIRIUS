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

Build the backend binary first (see *Production backend* below), then:

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
  and the frozen backend binary (`dist/sirius-backend/`, see below) are copied
  into the package under `resources/backend/` via `extraResources`.
- At runtime, packaged apps use a writable workspace under Electron's
  `app.getPath("userData")` (`.../backend/`) for uploads, processed files,
  generated specs, caches, sessions, and other mutable `data/*` paths. Bundled
  `data/` files are copied there only when missing, while `scripts/` are
  refreshed on each launch.

App icons live in `build/` — see *App icons* below.

> Note: cross-building macOS installers is only fully supported **on macOS**
> (code-signing/notarization included). Build the `.dmg` on a Mac and the
> Windows installer on Windows or Linux. The CI workflow (below) handles this
> by running each OS/arch on its matching GitHub-hosted runner.

## Production backend (recommended)

Shipping a Python interpreter to end users is fragile. For a self-contained
app, freeze the backend with [PyInstaller](https://pyinstaller.org/) using the
committed spec file, from the repo root:

```bash
uv sync --locked --no-dev --group build
uv run --locked --no-dev --group build pyinstaller sirius-backend.spec --noconfirm
```

`uv.lock` is authoritative. `requirements-build.txt` is a generated,
hash-pinned compatibility export and must not be overwritten or edited by
hand. For a pip-only build environment, install that export directly:

```bash
python -m pip install --require-hashes -r requirements-build.txt
pyinstaller sirius-backend.spec --noconfirm
```

Set `UV_DEFAULT_INDEX` (uv) or `PIP_INDEX_URL` (pip) when an approved,
versioned internal mirror is required.

This produces a `dist/sirius-backend/` folder (onedir mode, not `--onefile`
— the shell respawns the backend on every launch, and onedir avoids paying a
temp-dir self-extraction cost each time). `desktop/package.json`'s
`extraResources` already copies that folder into the packaged app at
`resources/backend/bin/sirius-backend/`, and `main.js` auto-detects it there.
Because of this, **`dist/sirius-backend/` must exist before running
`npm run dist:win` / `dist:mac`** — build the backend first.

Two ways to ship the binary:

- **Bundle it (default, recommended).** Build with the spec file above before
  running `electron-builder`; `extraResources` picks it up automatically.
- **Point at it explicitly.** Set `SIRIUS_BACKEND_BIN` to the binary's path
  (useful for testing a binary built elsewhere).

The shell always prefers a backend binary over the Python fallback, so a
bundled binary makes the app self-contained on a clean machine. The backend
entrypoint also dispatches `sirius-backend scripts/<tool>.py ...`, so existing
upload/preprocess flows keep working when the frozen executable is used as
`sys.executable`.

`data/knowledge_base/**` is intentionally not baked into the binary — it's
copied from `resources/backend/data` into the writable per-user runtime
directory the first time the app launches (see `prepareBackendRuntime` in
`main.js`), same as the Python-fallback path.

## App icons

`build/icon.svg` is the master mark. `build/icon.ico`, `icon.icns`, and
`icon.png` are generated from it and committed — `electron-builder` picks
them up automatically by filename convention, no config needed. Regenerate
after editing the SVG:

```bash
npm run icons
```

The generator (`build/generate-icons.js`) uses `sharp` + `png2icons`, both
pure JS, so it runs the same on any build machine without macOS-only tools
(`iconutil`) or system packages (`rsvg-convert`, `imagemagick`).

## CI: automated Windows + macOS builds

`.github/workflows/desktop-build.yml` builds installers for all three targets
on every push of a `v*.*.*` tag (or on demand via `workflow_dispatch`):

| Runner | Produces |
|---|---|
| `windows-latest` | NSIS installer (`.exe`, x64) |
| `macos-15-intel` (Intel) | `.dmg` (x64) |
| `macos-15` (Apple Silicon) | `.dmg` (arm64) |

Each job builds the PyInstaller backend for that OS/arch, smoke-tests it
(spawns the binary against a temp data-seeded working directory and checks
`GET /` and `GET /api/template-files`), then runs `electron-builder`. A
PyInstaller binary is single-arch, so macOS is split into two jobs — one Intel
runner, one Apple Silicon runner — each producing its own DMG with the
matching backend, rather than one combined universal build. Artifacts upload
via `actions/upload-artifact`; tag-triggered runs also attach them to a
GitHub Release.

**Installers are unsigned** (no Apple Developer ID / Windows code-signing
certificate configured), so Gatekeeper/SmartScreen will warn on first run.
`electron-builder` already supports signing via `CSC_LINK`/`CSC_KEY_PASSWORD`
secrets if certificates become available later — no workflow changes needed
beyond adding the secrets.

## Files

| File                                      | Role                                                        |
|--------------------------------------------|-------------------------------------------------------------|
| `main.js`                                   | Electron main process — spawns/monitors backend, opens window. |
| `preload.js`                                | Locked-down preload (contextIsolation on; minimal surface). |
| `package.json`                              | npm scripts + `electron-builder` (win/mac) configuration.   |
| `build/`                                    | Packaging resources: app icons + generator script.          |
| `../sirius-backend.spec`                    | PyInstaller spec for the frozen backend binary.              |
| `../.github/workflows/desktop-build.yml`    | CI: builds + smoke-tests + packages all three targets.      |
