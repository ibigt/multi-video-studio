"""Periodic RTSP connectivity checks using ffprobe worker subprocesses."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update

from app.config import Settings
from app.constants import StreamStatus
from app.database import SessionLocal
from app.logging_config import get_logger
from app.models import Stream
from app.services.ffmpeg_utils import (
    input_args,
    process_creation_kwargs,
    sanitize_ffmpeg_message,
    stop_process,
)
from app.timeutils import utcnow

logger = get_logger("status")


@dataclass(slots=True)
class ProbeResult:
    status: str
    message: str
    checked_at: datetime


class StatusMonitor:
    """Owns all connectivity probes.

    Probes run in isolated ffprobe processes, never on the event loop. A
    per-stream lock prevents a manual "check now" from racing the periodic
    scan for the same camera.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._locks: dict[int, asyncio.Lock] = {}

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="status-monitor")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        # Probe immediately on boot, then on a configurable cadence.
        while not self._stopping:
            try:
                await self.check_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("周期性流状态检测失败")
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.settings.status_check_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def trigger(self) -> None:
        self._wake.set()

    async def check_all(self) -> None:
        async with SessionLocal() as db:
            rows = (
                await db.execute(select(Stream.id, Stream.url).order_by(Stream.id))
            ).all()
        if not rows:
            return

        semaphore = asyncio.Semaphore(self.settings.status_concurrency)

        async def run_one(stream_id: int, url: str) -> None:
            async with semaphore:
                try:
                    await self.check_stream(stream_id, url)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("流状态检测异常 stream_id=%s", stream_id)

        await asyncio.gather(*(run_one(row.id, row.url) for row in rows))

    def _lock_for(self, stream_id: int) -> asyncio.Lock:
        return self._locks.setdefault(stream_id, asyncio.Lock())

    async def check_stream(
        self, stream_id: int, url: str | None = None, mark_checking: bool = False
    ) -> ProbeResult:
        async with self._lock_for(stream_id):
            if url is None:
                async with SessionLocal() as db:
                    url = await db.scalar(select(Stream.url).where(Stream.id == stream_id))
                if not url:
                    raise LookupError("流不存在")

            if mark_checking:
                await self._save(
                    stream_id,
                    StreamStatus.CHECKING,
                    "正在检测连接…",
                    utcnow(),
                )
            result = await self._probe(url)
            await self._save(stream_id, result.status, result.message, result.checked_at)
            return result

    async def _probe(self, url: str) -> ProbeResult:
        timeout = self.settings.stream_timeout_seconds
        command = [
            self.settings.ffprobe_bin,
            "-hide_banner",
            "-v",
            "error",
            *input_args(url, timeout),
        ]
        command += [
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate",
            "-of",
            "json",
            url,
        ]

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **process_creation_kwargs(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout + 1
                )
            except asyncio.TimeoutError:
                await stop_process(process, timeout=2)
                return ProbeResult(
                    StreamStatus.OFFLINE,
                    f"连接超时（>{timeout} 秒）",
                    utcnow(),
                )
        except asyncio.CancelledError:
            if process is not None:
                await stop_process(process, timeout=2)
            raise
        except OSError as exc:
            return ProbeResult(
                StreamStatus.OFFLINE,
                f"无法启动 ffprobe：{exc}"[:350],
                utcnow(),
            )

        if process.returncode != 0:
            message = sanitize_ffmpeg_message(
                stderr.decode("utf-8", errors="replace"), source_url=url
            )
            return ProbeResult(StreamStatus.OFFLINE, message, utcnow())

        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
            streams = payload.get("streams") or []
        except json.JSONDecodeError:
            return ProbeResult(StreamStatus.OFFLINE, "ffprobe 返回了无效数据", utcnow())

        if not streams:
            return ProbeResult(StreamStatus.OFFLINE, "未检测到视频轨道", utcnow())

        video = streams[0]
        codec = str(video.get("codec_name") or "video").upper()
        width = video.get("width")
        height = video.get("height")
        resolution = f"{width}×{height}" if width and height else "分辨率未知"
        fps_raw = str(video.get("avg_frame_rate") or "")
        fps_text = ""
        try:
            if "/" in fps_raw:
                numerator, denominator = fps_raw.split("/", 1)
                if float(denominator):
                    fps = float(numerator) / float(denominator)
                    fps_text = f" · {fps:.0f} fps" if fps > 0 else ""
        except (ValueError, ZeroDivisionError):
            pass
        return ProbeResult(
            StreamStatus.ONLINE,
            f"{codec} · {resolution}{fps_text}",
            utcnow(),
        )

    async def _save(
        self, stream_id: int, status: str, message: str, checked_at: datetime
    ) -> None:
        async with SessionLocal() as db:
            previous = await db.scalar(select(Stream.status).where(Stream.id == stream_id))
            result = await db.execute(
                update(Stream)
                .where(Stream.id == stream_id)
                .values(status=status, status_message=message, last_checked_at=checked_at)
            )
            await db.commit()
        if result.rowcount and previous != status:
            logger.info(
                "流状态变更 stream_id=%s %s -> %s (%s)",
                stream_id,
                previous,
                status,
                message,
            )
