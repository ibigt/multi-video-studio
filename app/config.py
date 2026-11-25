"""Central application configuration.

Every operational knob lives here and can be overridden with environment
variables or a `.env` file in the project root.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Service ---------------------------------------------------------
    app_name: str = "Video Studio"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    access_log: bool = True

    # --- Storage ---------------------------------------------------------
    data_dir: Path = BASE_DIR / "data"
    recordings_dir: Path | None = None
    hls_dir: Path | None = None
    logs_dir: Path | None = None
    database_url: str | None = None

    # --- FFmpeg binaries -------------------------------------------------
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # --- Connectivity / monitoring --------------------------------------
    stream_timeout_seconds: int = Field(default=10, ge=2, le=120)
    status_check_interval_seconds: int = Field(default=15, ge=5, le=600)
    status_concurrency: int = Field(default=4, ge=1, le=16)
    auto_reconnect_default: bool = True
    reconnect_delay_seconds: int = Field(default=3, ge=1, le=60)

    # --- Browser preview (HLS transcode) ---------------------------------
    preview_width: int = Field(default=1280, ge=320, le=3840)
    preview_fps: int = Field(default=15, ge=1, le=60)
    preview_bitrate: str = "1500k"
    preview_hls_time: int = Field(default=1, ge=1, le=10)
    preview_hls_list_size: int = Field(default=6, ge=3, le=30)
    preview_idle_timeout_seconds: int = Field(default=75, ge=15, le=3600)

    # --- Recording --------------------------------------------------------
    recording_bitrate: str = "2500k"
    recording_audio_bitrate: str = "96k"
    recording_preset: str = "veryfast"
    recording_max_seconds: int = Field(default=0, ge=0, le=86400)
    recording_stop_timeout_seconds: int = Field(default=12, ge=2, le=60)

    # --- Runtime helpers ---------------------------------------------------
    @staticmethod
    def _resolve(path: Path) -> Path:
        return path.expanduser().resolve() if path.is_absolute() else (BASE_DIR / path).resolve()

    @property
    def data_path(self) -> Path:
        return self._resolve(self.data_dir)

    @property
    def recordings_path(self) -> Path:
        return self._resolve(self.recordings_dir or self.data_dir / "recordings")

    @property
    def hls_path(self) -> Path:
        return self._resolve(self.hls_dir or self.data_dir / "hls")

    @property
    def logs_path(self) -> Path:
        return self._resolve(self.logs_dir or self.data_dir / "logs")

    @property
    def database_url_resolved(self) -> str:
        if self.database_url:
            return self.database_url
        db_file = self.data_path / "rtsp_studio.db"
        return f"sqlite+aiosqlite:///{db_file.as_posix()}"

    def ensure_directories(self) -> None:
        for path in (
            self.data_path,
            self.recordings_path,
            self.hls_path,
            self.logs_path,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
