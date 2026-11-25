"""REST endpoints for starting/stopping recordings and playback."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_utils import recording_to_out
from app.config import get_settings
from app.constants import RecordingStatus
from app.database import get_db
from app.dependencies import get_recorder_manager
from app.logging_config import get_logger
from app.models import Recording, Stream
from app.schemas import MessageOut, RecordingOut
from app.services.ffmpeg_utils import resolve_within
from app.services.recorder import (
    RecorderManager,
    RecordingConflict,
    RecordingStartError,
)

router = APIRouter(tags=["recordings"])
logger = get_logger("api.recordings")


async def _get_recording_or_404(db: AsyncSession, recording_id: int) -> Recording:
    recording = await db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="录制文件不存在")
    return recording


def _safe_file_path(filename: str) -> Path:
    settings = get_settings()
    try:
        path = resolve_within(settings.recordings_path, Path(filename).name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件名") from exc
    return path


@router.get("/api/recordings", response_model=list[RecordingOut])
async def list_recordings(
    stream_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[RecordingOut]:
    query = select(Recording).order_by(Recording.started_at.desc(), Recording.id.desc())
    if stream_id is not None:
        query = query.where(Recording.stream_id == stream_id)
    query = query.limit(limit)
    rows = (await db.execute(query)).scalars().all()
    return [recording_to_out(row) for row in rows]


@router.post(
    "/api/streams/{stream_id}/recording/start",
    response_model=RecordingOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_recording(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    recorder: RecorderManager = Depends(get_recorder_manager),
) -> RecordingOut:
    stream = await db.get(Stream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="流不存在")
    try:
        recording = await recorder.start_recording(stream)
    except RecordingConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecordingStartError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return recording_to_out(recording)


@router.post("/api/streams/{stream_id}/recording/stop", response_model=RecordingOut)
async def stop_recording(
    stream_id: int,
    recorder: RecorderManager = Depends(get_recorder_manager),
) -> RecordingOut:
    recording = await recorder.stop_recording(stream_id)
    if recording is None:
        raise HTTPException(status_code=409, detail="当前没有正在进行的录制")
    return recording_to_out(recording)


@router.get("/api/recordings/{recording_id}/file")
async def play_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    recording = await _get_recording_or_404(db, recording_id)
    if recording.status != RecordingStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="录制尚未完成")
    path = _safe_file_path(recording.filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="录制文件已丢失")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=recording.filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-cache"},
    )


@router.get("/api/recordings/{recording_id}/download")
async def download_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    recording = await _get_recording_or_404(db, recording_id)
    if recording.status != RecordingStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="录制尚未完成")
    path = _safe_file_path(recording.filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="录制文件已丢失")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=recording.filename,
        headers={"Cache-Control": "private, no-cache"},
    )


@router.delete("/api/recordings/{recording_id}", response_model=MessageOut)
async def delete_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    recorder: RecorderManager = Depends(get_recorder_manager),
) -> MessageOut:
    recording = await _get_recording_or_404(db, recording_id)
    if recorder.is_active_recording_id(recording_id):
        raise HTTPException(status_code=409, detail="录制进行中，请先停止录制")
    path = _safe_file_path(recording.filename)
    if path.exists():
        try:
            await asyncio.to_thread(path.unlink)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"无法删除文件：{exc}") from exc
    await db.delete(recording)
    await db.commit()
    logger.info("删除录制 recording_id=%s", recording_id)
    return MessageOut(message="录制文件已删除")
