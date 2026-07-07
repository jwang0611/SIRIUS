from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# Force UTF-8 encoding on Windows before importing anything else
import src.web.sitecustomize  # noqa: F401
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

app = FastAPI(title="ISDTAIM 控制台", version="1.0.0")

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
