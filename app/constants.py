"""Shared status vocabulary used by the API and the browser UI."""

from __future__ import annotations


class StreamStatus:
    ONLINE = "online"
    OFFLINE = "offline"
    CHECKING = "checking"
    UNKNOWN = "unknown"


class RecordingStatus:
    STARTING = "starting"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
