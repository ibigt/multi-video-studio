"""REST endpoints for stream CRUD, connectivity checks and previews."""

from __future__ import annotations

import asyncio
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_utils import stream_to_out
from app.config import Settings, get_settings
from app.constants import StreamStatus
from app.database import get_db
from app.dependencies import get_preview_manager, get_recorder_manager, get_status_monitor
from app.logging_config import get_logger
from app.models import Stream
from app.schemas import (
    MessageOut,
    PreviewOut,
    PreviewReadyOut,
    StreamCreate,
    StreamOut,
    StreamUpdate,
)
from app.services.recorder import RecorderManager
from app.services.status_monitor import StatusMonitor
from app.services.stream_manager import PreviewManager
from app.timeutils import utcnow

router = APIRouter(prefix="/api/streams", tags=["streams"])
logger = get_logger("api.streams")


async def _get_stream_or_404(db: AsyncSession, stream_id: int) -> Stream:
    stream = await db.get(Stream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="流不存在")
    return stream


@router.get("", response_model=list[StreamOut])
async def list_streams(
    db: AsyncSession = Depends(get_db),
    preview: PreviewManager = Depends(get_preview_manager),
    recorder: RecorderManager = Depends(get_recorder_manager),
) -> list[StreamOut]:
    rows = (await db.execute(select(Stream).order_by(Stream.id))).scalars().all()
    return [stream_to_out(row, preview, recorder) for row in rows]


@router.post("", response_model=StreamOut, status_code=status.HTTP_201_CREATED)
async def create_stream(
    payload: StreamCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    preview: PreviewManager = Depends(get_preview_manager),
    recorder: RecorderManager = Depends(get_recorder_manager),
    monitor: StatusMonitor = Depends(get_status_monitor),
    settings: Settings = Depends(get_settings),
) -> StreamOut:
    stream = Stream(
        name=payload.name,
        url=payload.url,
        auto_reconnect=(
            settings.auto_reconnect_default
            if payload.auto_reconnect is None
            else payload.auto_reconnect
        ),
        status=StreamStatus.UNKNOWN,
        status_message="等待连接检测…",
    )
    db.add(stream)
    await db.commit()
    await db.refresh(stream)
    background.add_task(monitor.check_stream, stream.id, stream.url, True)
    logger.info("新增流 id=%s name=%s", stream.id, stream.name)
    return stream_to_out(stream, preview, recorder)


@router.get("/{stream_id}", response_model=StreamOut)
async def get_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    preview: PreviewManager = Depends(get_preview_manager),
    recorder: RecorderManager = Depends(get_recorder_manager),
) -> StreamOut:
    stream = await _get_stream_or_404(db, stream_id)
    return stream_to_out(stream, preview, recorder)


@router.put("/{stream_id}", response_model=StreamOut)
async def update_stream(
    stream_id: int,
    payload: StreamUpdate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    preview: PreviewManager = Depends(get_preview_manager),
    recorder: RecorderManager = Depends(get_recorder_manager),
    monitor: StatusMonitor = Depends(get_status_monitor),
) -> StreamOut:
    stream = await _get_stream_or_404(db, stream_id)
    if recorder.is_recording(stream_id):
        raise HTTPException(status_code=409, detail="录制进行中，请先停止录制再编辑该流")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return stream_to_out(stream, preview, recorder)

    url_changed = "url" in changes and changes["url"] != stream.url
    reconnect_changed = (
        "auto_reconnect" in changes
        and changes["auto_reconnect"] != stream.auto_reconnect
    )
    was_previewing = preview.is_active(stream_id)
    if was_previewing and (url_changed or reconnect_changed):
        await preview.stop_preview(stream_id)

    for field, value in changes.items():
        setattr(stream, field, value)

    if url_changed:
        stream.status = StreamStatus.CHECKING
        stream.status_message = "地址已更新，正在检测连接…"
        stream.last_checked_at = utcnow()
    stream.updated_at = utcnow()
    await db.commit()
    await db.refresh(stream)

    if was_previewing and (url_changed or reconnect_changed):
        await preview.start_preview(stream)
    if url_changed:
        background.add_task(monitor.check_stream, stream.id, stream.url, True)

    logger.info("更新流 id=%s fields=%s", stream.id, sorted(changes))
    return stream_to_out(stream, preview, recorder)


@router.delete("/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    preview: PreviewManager = Depends(get_preview_manager),
    recorder: RecorderManager = Depends(get_recorder_manager),
) -> Response:
    stream = await _get_stream_or_404(db, stream_id)
    if recorder.is_recording(stream_id):
        await recorder.stop_recording(stream_id)
    await preview.stop_preview(stream_id)
    await db.delete(stream)
    await db.commit()

    hls_directory = get_settings().hls_path / str(stream_id)
    await asyncio.to_thread(shutil.rmtree, hls_directory, True)
    logger.info("删除流 id=%s", stream_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{stream_id}/check", response_model=StreamOut)
async def check_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    preview: PreviewManager = Depends(get_preview_manager),
    recorder: RecorderManager = Depends(get_recorder_manager),
    monitor: StatusMonitor = Depends(get_status_monitor),
) -> StreamOut:
    stream = await _get_stream_or_404(db, stream_id)
    await monitor.check_stream(stream.id, stream.url, mark_checking=True)
    await db.refresh(stream)
    return stream_to_out(stream, preview, recorder)


@router.post("/{stream_id}/preview/start", response_model=PreviewOut)
async def start_preview(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    preview: PreviewManager = Depends(get_preview_manager),
) -> PreviewOut:
    stream = await _get_stream_or_404(db, stream_id)
    worker = await preview.start_preview(stream)
    return PreviewOut(
        stream_id=stream.id,
        preview_url=preview.preview_url(stream.id),
        preview_active=worker.active,
        message="预览转码已启动",
    )


@router.get("/{stream_id}/preview/ready", response_model=PreviewReadyOut)
async def preview_ready(
    stream_id: int,
    preview: PreviewManager = Depends(get_preview_manager),
) -> PreviewReadyOut:
    preview.touch(stream_id)
    return PreviewReadyOut(
        stream_id=stream_id,
        ready=preview.is_playlist_ready(stream_id),
        preview_url=preview.preview_url(stream_id),
    )


@router.post("/{stream_id}/preview/stop", response_model=MessageOut)
async def stop_preview(
    stream_id: int,
    preview: PreviewManager = Depends(get_preview_manager),
) -> MessageOut:
    await preview.stop_preview(stream_id)
    return MessageOut(message="预览已停止")
