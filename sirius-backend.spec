# PyInstaller spec for the SIRIUS FastAPI backend.
#
# Builds a onedir bundle (not --onefile): the desktop shell (desktop/main.js)
# respawns this process on every app launch, and onedir avoids the temp-dir
# self-extraction cost --onefile pays each time. Build with:
#
#   pyinstaller sirius-backend.spec --noconfirm
#
# Output lands in dist/sirius-backend/ (sirius-backend[.exe] plus its
# dependency files). desktop/main.js's resolveBackendBinary() looks for it at
# backend/bin/sirius-backend/sirius-backend[.exe] once bundled via
# electron-builder's extraResources.
#
# Note: data/knowledge_base/** is intentionally NOT bundled here. The desktop
# shell seeds a writable copy of data/ into the OS user-data directory at
# runtime (desktop/main.js: prepareBackendRuntime); a standalone binary run
# outside Electron must be launched with data/ present in its cwd instead.

from PyInstaller.utils.hooks import copy_metadata

datas = [
    ("src/web/static", "src/web/static"),
    ("src/prompts/templates", "src/prompts/templates"),
    ("src/prompts/rules", "src/prompts/rules"),
    ("src/prompts/examples", "src/prompts/examples"),
    ("scripts", "scripts"),
]

# These packages check their own version via importlib.metadata at runtime;
# PyInstaller's static analysis doesn't see that, so the dist-info has to be
# copied in explicitly or the frozen binary raises PackageNotFoundError.
for pkg in (
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "anyio",
    "slowapi",
    "limits",
    "openai",
):
    datas += copy_metadata(pkg)

hiddenimports = [
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.loops.auto",
    "uvicorn.lifespan.on",
    "multipart",
    "multipart.multipart",
    "email.mime.multipart",
    "email.mime.text",
    "email.mime.application",
    "pydantic_core",
    "python_calamine",
]

# Packages with native/rust extensions or plugin-style submodule discovery
# that PyInstaller's import scanner tends to miss.
tricky_packages = ["pyarrow", "python_calamine", "slowapi", "limits"]

from PyInstaller.utils.hooks import collect_all

binaries = []
for pkg in tricky_packages:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sirius-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="sirius-backend",
)
