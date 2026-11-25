"""Conversions from ORM rows to API responses."""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.constants import RecordingStatus
from app.models import Recording, Stream
from app.schemas import RecordingOut, StreamOut
from app.services.recorder import RecorderManager
from app.services.stream_manager import PreviewManager


def stream_to_out(
    stream: Stream, preview: PreviewManager, recorder: RecorderManager
) -> StreamOut:
    active = preview.is_active(stream.id)
    payload = StreamOut.model_validate(stream)
    return payload.model_copy(
        update={
            "is_recording": recorder.is_recording(stream.id),
            "preview_active": active,
            "preview_url": preview.preview_url(stream.id) if active else None,
            "active_recording_id": recorder.active_recording_id(stream.id),
        }
    )


def recording_to_out(recording: Recording) -> RecordingOut:
    settings = get_settings()
    filename = Path(recording.filename).name
    path = settings.recordings_path / filename
    try:
        file_exists = (
            recording.status == RecordingStatus.COMPLETED
            and path.is_file()
            and path.stat().st_size > 0
        )
    except OSError:
        file_exists = False
    payload = RecordingOut.model_validate(recording)
    return payload.model_copy(
        update={
            "playback_url": f"/api/recordings/{recording.id}/file" if file_exists else None,
            "download_url": f"/api/recordings/{recording.id}/download"
            if file_exists
            else None,
            "file_exists": file_exists,
        }
    )
