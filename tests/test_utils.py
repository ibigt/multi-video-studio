from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.ffmpeg_utils import (
    input_args,
    redact_url,
    resolve_within,
    safe_filename_stem,
    sanitize_ffmpeg_message,
)
from app.services.recorder import RecorderManager
from app.services.stream_manager import PreviewWorker


def test_filename_sanitization() -> None:
    assert safe_filename_stem("前门 / 摄像头:01") == "前门_摄像头_01"
    assert safe_filename_stem("..") == "stream"
    assert safe_filename_stem("CON") == "stream_CON"
    assert len(safe_filename_stem("x" * 200)) <= 48


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    base = tmp_path / "recordings"
    base.mkdir()
    assert resolve_within(base, "ok.mp4") == (base / "ok.mp4").resolve()
    with pytest.raises(ValueError):
        resolve_within(base, "../secret.txt")
    with pytest.raises(ValueError):
        resolve_within(base, "../recordings-evil/file.mp4")


def test_credentials_are_redacted() -> None:
    url = "rtsp://admin:secret@10.0.0.2:554/live"
    assert redact_url(url) == "rtsp://***@10.0.0.2:554/live"
    message = sanitize_ffmpeg_message(f"failed to open {url}", source_url=url)
    assert "secret" not in message

    http_message = sanitize_ffmpeg_message(
        "Error opening input file http://admin:hunter2@127.0.0.1:8080/missing.ts."
    )
    assert "hunter2" not in http_message
    assert "http://***@127.0.0.1:8080/missing.ts" in http_message

    # A password containing "@" must be redacted as a whole, not just up to
    # the first "@".
    tricky = "rtsp://admin:SAIda@0551@192.168.201.221:554/Streaming/Channels/101"
    masked = redact_url(tricky)
    assert masked == "rtsp://***@192.168.201.221:554/Streaming/Channels/101"
    assert "0551" not in masked and "SAIda" not in masked


def test_input_args_include_rtsp_tcp_and_timeout() -> None:
    args = input_args("rtsp://camera/live", 8)
    assert args == ["-rtsp_transport", "tcp", "-timeout", "8000000"]
    # FFmpeg 7+ rejects -rw_timeout as an input option; passing it aborts the
    # preview with "Option rw_timeout not found".
    assert "-rw_timeout" not in args

    http_args = input_args("https://example.com/live.m3u8", 8)
    assert "-rtsp_transport" not in http_args
    assert "-timeout" not in http_args


def test_preview_and_recording_commands_use_valid_rtsp_timeout(
    tmp_path: Path,
) -> None:
    """Regression: every FFmpeg call site must use the RTSP demuxer timeout."""
    settings = Settings(data_dir=tmp_path)
    stream = SimpleNamespace(
        id=7,
        url="rtsp://admin:secret@camera/live",
        auto_reconnect=True,
    )

    commands = [
        PreviewWorker(settings, stream)._command(),
        RecorderManager(settings)._command(stream.url, tmp_path / "out.mp4"),
    ]
    expected = ["-timeout", str(settings.stream_timeout_seconds * 1_000_000)]
    for command in commands:
        assert "-rw_timeout" not in command
        assert command.count("-timeout") == 1
        start = command.index("-timeout")
        assert command[start : start + 2] == expected
        assert start < command.index("-i")
