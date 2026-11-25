"""FastAPI dependency accessors for app-scoped service instances."""

from __future__ import annotations

from fastapi import Request

from app.services.recorder import RecorderManager
from app.services.status_monitor import StatusMonitor
from app.services.stream_manager import PreviewManager


def get_preview_manager(request: Request) -> PreviewManager:
    return request.app.state.preview_manager


def get_recorder_manager(request: Request) -> RecorderManager:
    return request.app.state.recorder_manager


def get_status_monitor(request: Request) -> StatusMonitor:
    return request.app.state.status_monitor
