"""Health and runtime information endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import Settings, get_settings
from app.constants import StreamStatus
from app.database import get_db
from app.dependencies import get_recorder_manager
from app.logging_config import get_logger
from app.models import Stream
from app.schemas import HealthOut, SystemInfoOut
from app.services.recorder import RecorderManager
from app.timeutils import iso_utc, utcnow

router = APIRouter(prefix="/api", tags=["system"])
logger = get_logger("api.system")


@router.get("/health", response_model=HealthOut)
async def health(request: Request, db: AsyncSession = Depends(get_db)) -> HealthOut:
    database_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("数据库健康检查失败")
        database_status = "error"
    return HealthOut(
        status="ok" if database_status == "ok" else "degraded",
        app=get_settings().app_name,
        version=__version__,
        database=database_status,
        ffmpeg=getattr(request.app.state, "ffmpeg_version", "unknown"),
    )


@router.get("/system/info", response_model=SystemInfoOut)
async def system_info(
    db: AsyncSession = Depends(get_db),
    recorder: RecorderManager = Depends(get_recorder_manager),
    settings: Settings = Depends(get_settings),
) -> SystemInfoOut:
    total = await db.scalar(select(func.count()).select_from(Stream)) or 0
    online = (
        await db.scalar(
            select(func.count())
            .select_from(Stream)
            .where(Stream.status == StreamStatus.ONLINE)
        )
        or 0
    )
    return SystemInfoOut(
        app_name=settings.app_name,
        version=__version__,
        recordings_path=str(settings.recordings_path),
        data_path=str(settings.data_path),
        recording_bitrate=settings.recording_bitrate,
        stream_timeout_seconds=settings.stream_timeout_seconds,
        status_check_interval_seconds=settings.status_check_interval_seconds,
        preview_idle_timeout_seconds=settings.preview_idle_timeout_seconds,
        auto_reconnect_default=settings.auto_reconnect_default,
        server_time=iso_utc(utcnow()) or "",
        stream_count=total,
        online_count=online,
        active_recording_count=recorder.active_count,
    )
