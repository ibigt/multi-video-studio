"""On-demand browser preview workers.

Browsers cannot play RTSP directly, so each watched stream gets an isolated
FFmpeg process that transcodes to a short HLS playlist. Workers are started
only when a client asks for a preview and are reclaimed after the browser
stops requesting segments.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from app.config import Settings
from app.logging_config import get_logger
from app.models import Stream
from app.services.ffmpeg_log import pump_stderr
from app.services.ffmpeg_utils import (
    input_args,
    process_creation_kwargs,
    stop_process,
)

logger = get_logger("preview")


class PreviewWorker:
    def __init__(self, settings: Settings, stream: Stream) -> None:
        self.settings = settings
        self.stream_id = stream.id
        self.url = stream.url
        self.auto_reconnect = stream.auto_reconnect
        self.directory = settings.hls_path / str(stream.id)
        self.last_access = time.monotonic()
        self._desired = False
        self._task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._stop_event = asyncio.Event()

    @property
    def active(self) -> bool:
        return self._desired and self._task is not None and not self._task.done()

    @property
    def process_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def touch(self) -> None:
        self.last_access = time.monotonic()

    async def start(self) -> None:
        if self.active:
            self.touch()
            return
        self._desired = True
        self._stop_event.clear()
        self.touch()
        self._task = asyncio.create_task(
            self._supervise(), name=f"preview-{self.stream_id}"
        )
        logger.info("预览转码已启动 stream_id=%s", self.stream_id)

    async def stop(self) -> None:
        self._desired = False
        self._stop_event.set()
        process = self._process
        if process is not None:
            await stop_process(process, timeout=3)
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=6)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
        await asyncio.to_thread(shutil.rmtree, self.directory, True)
        logger.info("预览转码已停止 stream_id=%s", self.stream_id)

    async def _prepare_directory(self) -> None:
        await asyncio.to_thread(shutil.rmtree, self.directory, True)
        await asyncio.to_thread(self.directory.mkdir, parents=True, exist_ok=True)

    def _command(self) -> list[str]:
        s = self.settings
        playlist = self.directory / "index.m3u8"
        segment_pattern = self.directory / "segment_%05d.ts"
        gop = max(1, s.preview_fps)
        return [
            s.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostats",
            *input_args(self.url, s.stream_timeout_seconds),
            "-i",
            self.url,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"scale=w='min({s.preview_width},iw)':h=-2",
            "-r",
            str(s.preview_fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            s.preview_bitrate,
            "-maxrate",
            s.preview_bitrate,
            "-bufsize",
            s.preview_bitrate,
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-f",
            "hls",
            "-hls_time",
            str(s.preview_hls_time),
            "-hls_list_size",
            str(s.preview_hls_list_size),
            "-hls_flags",
            "delete_segments+omit_endlist+independent_segments+temp_file",
            "-hls_segment_filename",
            segment_pattern.as_posix(),
            "-y",
            playlist.as_posix(),
        ]

    async def _supervise(self) -> None:
        settings = self.settings
        attempt = 0
        while self._desired:
            await self._prepare_directory()
            attempt += 1
            log_path = settings.logs_path / f"preview_{self.stream_id}.log"

            process = None
            stderr_task: asyncio.Task[None] | None = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._command(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    **process_creation_kwargs(),
                )
                self._process = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(
                    pump_stderr(process.stderr, log_path, self.url),
                    name=f"preview-log-{self.stream_id}",
                )
                await process.wait()
                returncode = process.returncode
            except asyncio.CancelledError:
                if process is not None:
                    await stop_process(process, timeout=2)
                raise
            except OSError:
                logger.exception("无法启动预览 FFmpeg stream_id=%s", self.stream_id)
                returncode = -1
            finally:
                self._process = None
                if stderr_task is not None:
                    try:
                        await asyncio.wait_for(stderr_task, timeout=3)
                    except asyncio.TimeoutError:
                        stderr_task.cancel()
                    except asyncio.CancelledError:
                        stderr_task.cancel()

            if not self._desired:
                break

            logger.warning(
                "预览 FFmpeg 退出 stream_id=%s rc=%s attempt=%s",
                self.stream_id,
                returncode,
                attempt,
            )
            if not self.auto_reconnect:
                break

            # Exponential-ish backoff with a small cap, but wake immediately
            # when the user closes the preview.
            delay = min(settings.reconnect_delay_seconds + attempt, 10)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                continue

        if self._desired:
            self._desired = False


class PreviewManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._workers: dict[int, PreviewWorker] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._reaper = asyncio.create_task(self._reap_idle(), name="preview-reaper")

    async def shutdown(self) -> None:
        if self._reaper:
            self._reaper.cancel()
            try:
                await self._reaper
            except asyncio.CancelledError:
                pass
            self._reaper = None
        async with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        await asyncio.gather(*(worker.stop() for worker in workers), return_exceptions=True)

    async def start_preview(self, stream: Stream) -> PreviewWorker:
        async with self._lock:
            existing = self._workers.get(stream.id)
            if existing and existing.url == stream.url:
                existing.auto_reconnect = stream.auto_reconnect
                await existing.start()
                return existing
            if existing:
                await existing.stop()
                self._workers.pop(stream.id, None)

            worker = PreviewWorker(self.settings, stream)
            self._workers[stream.id] = worker
            await worker.start()
            return worker

    async def stop_preview(self, stream_id: int) -> bool:
        # Keep the lock while the process tree is being stopped. Otherwise a
        # new start could recreate the same HLS directory and the old worker
        # would delete its files during cleanup.
        async with self._lock:
            worker = self._workers.pop(stream_id, None)
            if worker is None:
                return False
            await worker.stop()
        return True

    def is_active(self, stream_id: int) -> bool:
        worker = self._workers.get(stream_id)
        return bool(worker and worker.active)

    def preview_url(self, stream_id: int) -> str:
        return f"/media/hls/{stream_id}/index.m3u8"

    def touch(self, stream_id: int) -> None:
        worker = self._workers.get(stream_id)
        if worker is not None:
            worker.touch()

    def is_known(self, stream_id: int) -> bool:
        return stream_id in self._workers

    def is_playlist_ready(self, stream_id: int) -> bool:
        """True once FFmpeg has produced at least one complete HLS segment."""
        worker = self._workers.get(stream_id)
        if worker is None or not worker.active:
            return False
        playlist = self.playlist_path(stream_id)
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return "#EXTM3U" in text and "#EXTINF" in text and "segment_" in text

    def playlist_path(self, stream_id: int) -> Path:
        return self.settings.hls_path / str(stream_id) / "index.m3u8"

    async def _reap_idle(self) -> None:
        interval = 10
        while True:
            try:
                await asyncio.sleep(interval)
                cutoff = time.monotonic() - self.settings.preview_idle_timeout_seconds
                async with self._lock:
                    for stream_id, worker in list(self._workers.items()):
                        if worker.last_access >= cutoff:
                            continue
                        self._workers.pop(stream_id, None)
                        logger.info(
                            "预览空闲回收 stream_id=%s idle>%ss",
                            worker.stream_id,
                            self.settings.preview_idle_timeout_seconds,
                        )
                        await worker.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("预览空闲回收任务异常")
