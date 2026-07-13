from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# Force UTF-8 encoding on Windows before importing anything else
import src.web.sitecustomize  # noqa: F401
from src import __version__
from src.web.job_manager import job_manager
from src.web.routers import corrections, files, jobs, session, spec_mapper, upload
from src.web.security import (
    RATE_LIMIT_AI_JOB,
    RATE_LIMIT_GENERAL,
    RATE_LIMIT_READ,
    RATE_LIMIT_UPLOAD,
    limiter,
)
from src.web.session_manager import session_manager, start_cleanup_scheduler

# Wire up cross-module references
session_manager.set_job_manager(job_manager)

app = FastAPI(title="SIRIUS 控制台", version=__version__)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
cors_origins = [
    origin.strip()
    for origin in os.getenv("SIRIUS_CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
STATIC_DIR = Path(__file__).parent / "src" / "web" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

EXAMPLES_DIR = Path(__file__).parent / "data" / "examples"
if EXAMPLES_DIR.is_dir():
    app.mount("/examples", StaticFiles(directory=EXAMPLES_DIR), name="examples")

# Register routers
app.include_router(upload.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(session.router, prefix="/api")
app.include_router(spec_mapper.router, prefix="/api")
app.include_router(corrections.router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化后台任务"""
    start_cleanup_scheduler()
    print("[Startup] Session 清理调度器已启动")
    print("[Startup] API 速率限制已启用:")
    print(f"  - 上传: {RATE_LIMIT_UPLOAD}")
    print(f"  - AI 任务: {RATE_LIMIT_AI_JOB}")
    print(f"  - 一般 API: {RATE_LIMIT_GENERAL}")
    print(f"  - 读取操作: {RATE_LIMIT_READ}")


@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse({"message": "前端尚未部署"}, status_code=503)
    return FileResponse(index_file, headers={"Cache-Control": "no-cache"})


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.get("/version", include_in_schema=False)
def version():
    return {"name": "SIRIUS", "version": __version__}


def _resolve_script_path(script_arg: str) -> Path:
    script_path = Path(script_arg)
    candidates = []
    if script_path.is_absolute():
        candidates.append(script_path)
    else:
        candidates.extend(
            [
                Path.cwd() / script_path,
                Path(os.getenv("SIRIUS_BACKEND_RUNTIME_ROOT", "")) / script_path,
                Path(os.getenv("SIRIUS_BACKEND_SOURCE_ROOT", "")) / script_path,
                Path(__file__).parent / script_path,
            ]
        )

    for candidate in candidates:
        if str(candidate) and candidate.is_file():
            return candidate

    raise FileNotFoundError(f"Script not found: {script_arg}")


def _run_script(script_arg: str) -> None:
    script_path = _resolve_script_path(script_arg)
    sys.argv = [str(script_path), *sys.argv[2:]]
    runpy.run_path(str(script_path), run_name="__main__")


def _run_server() -> None:
    parser = argparse.ArgumentParser(description="Run the SIRIUS FastAPI backend.")
    parser.add_argument("--host", default=os.getenv("SIRIUS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SIRIUS_PORT", "8000")))
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


def main() -> None:
    # PyInstaller builds use this module as the executable entrypoint. The web
    # backend also launches helper scripts through sys.executable, so dispatch a
    # leading scripts/*.py argument to the script inside the embedded runtime.
    if len(sys.argv) > 1 and sys.argv[1].replace("\\", "/").startswith("scripts/"):
        _run_script(sys.argv[1])
        return
    _run_server()


if __name__ == "__main__":
    main()
