"""Pydantic request/response models (the public API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from app.constants import RecordingStatus, StreamStatus
from app.timeutils import iso_utc

StreamStatusLiteral = Literal["online", "offline", "checking", "unknown"]
RecordingStatusLiteral = Literal["starting", "recording", "completed", "failed", "stopped"]


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _validate_stream_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"rtsp", "rtsps", "http", "https"}:
        raise ValueError("地址必须以 rtsp://、rtsps://、http:// 或 https:// 开头")
    if not parsed.hostname:
        raise ValueError("地址缺少有效的主机名")
    return value


def _validate_stream_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("名称不能为空")
    if len(value) > 80:
        raise ValueError("名称不能超过 80 个字符")
    return value


class StreamCreate(APIModel):
    name: str
    url: str
    auto_reconnect: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_stream_name(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_stream_url(value)


class StreamUpdate(APIModel):
    name: str | None = None
    url: str | None = None
    auto_reconnect: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _validate_stream_name(value) if value is not None else None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_stream_url(value) if value is not None else None


class StreamOut(APIModel):
    id: int
    name: str
    url: str
    auto_reconnect: bool
    status: StreamStatusLiteral
    status_message: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Runtime state, filled by the router from the in-memory managers.
    is_recording: bool = False
    preview_active: bool = False
    preview_url: str | None = None
    active_recording_id: int | None = None

    @field_serializer("last_checked_at", "created_at", "updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class RecordingOut(APIModel):
    id: int
    stream_id: int | None
    stream_name: str
    filename: str
    status: RecordingStatusLiteral
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    size_bytes: int | None = None
    error_message: str | None = None
    playback_url: str | None = None
    download_url: str | None = None
    file_exists: bool = False

    @field_serializer("started_at", "ended_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class PreviewOut(APIModel):
    stream_id: int
    preview_url: str
    preview_active: bool
    message: str


class PreviewReadyOut(APIModel):
    stream_id: int
    ready: bool
    preview_url: str


class MessageOut(APIModel):
    message: str


class HealthOut(APIModel):
    status: str
    app: str
    version: str
    database: str
    ffmpeg: str


class SystemInfoOut(APIModel):
    app_name: str
    version: str
    recordings_path: str
    data_path: str
    recording_bitrate: str
    stream_timeout_seconds: int
    status_check_interval_seconds: int
    preview_idle_timeout_seconds: int
    auto_reconnect_default: bool
    server_time: str
    stream_count: int
    online_count: int
    active_recording_count: int


__all__ = [
    "HealthOut",
    "MessageOut",
    "PreviewOut",
    "PreviewReadyOut",
    "RecordingOut",
    "RecordingStatus",
    "RecordingStatusLiteral",
    "StreamCreate",
    "StreamOut",
    "StreamStatus",
    "StreamStatusLiteral",
    "StreamUpdate",
    "SystemInfoOut",
]
