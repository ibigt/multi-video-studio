"""FastAPI application entry point.

A single process serves the REST API, the HLS preview assets, recorded MP4
files and the compiled Tailwind single-page UI.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update

from app import __version__
from app.config import get_settings
from app.constants import StreamStatus
from app.database import SessionLocal, close_db, init_db
from app.logging_config import configure_logging, get_logger
from app.models import Stream
from app.routers import media, recordings, streams, system
from app.services.ffmpeg_utils import ffmpeg_available
from app.services.recorder import RecorderManager
from app.services.status_monitor import StatusMonitor
from app.services.stream_manager import PreviewManager

settings = get_settings()
configure_logging(settings)
logger = get_logger("main")

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _clean_stale_hls() -> None:
    def clean() -> None:
        for child in settings.hls_path.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                logger.warning("清理 HLS 临时文件失败: %s", child)

    await asyncio.to_thread(clean)


async def _reset_statuses() -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(Stream).values(
                status=StreamStatus.UNKNOWN,
                status_message="服务已启动，等待连接检测…",
            )
        )
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.ensure_directories()
    await init_db()
    await _clean_stale_hls()
    await _reset_statuses()

    ffmpeg_ok, ffmpeg_version = await ffmpeg_available(settings.ffmpeg_bin)
    ffprobe_ok, _ = await ffmpeg_available(settings.ffprobe_bin)
    if not ffmpeg_ok or not ffprobe_ok:
        logger.error(
            "FFmpeg/ffprobe 不可用。请确认已安装并加入 PATH，或配置 FFMPEG_BIN / FFPROBE_BIN。"
        )
        raise RuntimeError(
            "FFmpeg 或 ffprobe 未找到。请安装 FFmpeg 并将其加入 PATH，"
            "或在 .env 中配置 FFMPEG_BIN / FFPROBE_BIN。"
        )
    app.state.ffmpeg_version = ffmpeg_version

    preview_manager = PreviewManager(settings)
    recorder_manager = RecorderManager(settings)
    status_monitor = StatusMonitor(settings)
    app.state.preview_manager = preview_manager
    app.state.recorder_manager = recorder_manager
    app.state.status_monitor = status_monitor

    await recorder_manager.recover_interrupted()
    await preview_manager.start()
    await status_monitor.start()

    logger.info("=" * 66)
    logger.info("%s v%s 已启动", settings.app_name, __version__)
    logger.info("地址: http://127.0.0.1:%s", settings.port)
    logger.info("录制目录: %s", settings.recordings_path)
    logger.info("视频引擎: %s", ffmpeg_version)
    logger.info("=" * 66)

    try:
        yield
    finally:
        logger.info("正在关闭服务…")
        await status_monitor.stop()
        await preview_manager.shutdown()
        await recorder_manager.stop_all()
        await close_db()
        logger.info("服务已关闭")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="RTSP 多路预览、录制与回放平台",
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    if errors:
        first = errors[0]
        location = ".".join(
            str(part) for part in first.get("loc", ()) if part not in {"body", "query"}
        )
        message = str(first.get("msg", "参数校验失败")).removeprefix("Value error, ")
        detail = f"{location}: {message}" if location else message
    else:
        detail = "请求参数校验失败"
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常 path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请查看日志排查。"},
    )


app.include_router(system.router)
app.include_router(streams.router)
app.include_router(recordings.router)
app.include_router(media.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/favicon.svg", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api", include_in_schema=False)
async def api_root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
    }
