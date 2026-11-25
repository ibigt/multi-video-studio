"""Isolated, per-stream MP4 recording processes.

Each recording is a dedicated FFmpeg process. Stopping is done gracefully by
writing ``q`` to FFmpeg's stdin so the MP4 trailer (moov atom) is committed and
the file remains seekable in a browser.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

from app.config import Settings
from app.constants import RecordingStatus
from app.database import SessionLocal
from app.logging_config import get_logger
from app.models import Recording, Stream
from app.services.ffmpeg_log import pump_stderr
from app.services.ffmpeg_utils import (
    input_args,
    process_creation_kwargs,
    safe_filename_stem,
    sanitize_ffmpeg_message,
    stop_process,
)
from app.timeutils import local_timestamp, utcnow

logger = get_logger("recorder")


class RecordingConflict(RuntimeError):
    pass


class RecordingStartError(RuntimeError):
    pass


@dataclass
class ActiveRecording:
    recording_id: int
    stream_id: int
    stream_name: str
    filename: str
    final_path: Path
    part_path: Path
    log_path: Path
    started_at: datetime
    process: asyncio.subprocess.Process
    stderr_task: asyncio.Task[None] | None = None
    stop_requested: bool = False
    task: asyncio.Task[None] | None = None
    finalize_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finalized: bool = False

    @property
    def running(self) -> bool:
        return not self.finalized and self.process.returncode is None


class RecorderManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._active: dict[int, ActiveRecording] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ API
    async def start_recording(self, stream: Stream) -> Recording:
        async with self._lock:
            if stream.id in self._active and self._active[stream.id].running:
                raise RecordingConflict("该流正在录制中")

            filename, final_path, part_path = await asyncio.to_thread(
                self._allocate_path, stream
            )
            started_at = utcnow()
            async with SessionLocal() as db:
                row = Recording(
                    stream_id=stream.id,
                    stream_name=stream.name,
                    filename=filename,
                    status=RecordingStatus.STARTING,
                    started_at=started_at,
                )
                db.add(row)
                await db.commit()
                await db.refresh(row)

            log_path = self.settings.logs_path / f"recording_{stream.id}_{row.id}.log"
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._command(stream.url, part_path),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    **process_creation_kwargs(),
                )
            except (OSError, ValueError) as exc:
                await self._mark_start_failed(row.id, f"无法启动 FFmpeg：{exc}")
                raise RecordingStartError(f"无法启动 FFmpeg：{exc}") from exc

            assert process.stderr is not None
            stderr_task = asyncio.create_task(
                pump_stderr(process.stderr, log_path, stream.url),
                name=f"recording-log-{row.id}",
            )
            active = ActiveRecording(
                recording_id=row.id,
                stream_id=stream.id,
                stream_name=stream.name,
                filename=filename,
                final_path=final_path,
                part_path=part_path,
                log_path=log_path,
                started_at=started_at,
                process=process,
                stderr_task=stderr_task,
            )
            self._active[stream.id] = active
            active.task = asyncio.create_task(
                self._supervise(active), name=f"recording-{row.id}"
            )

            async with SessionLocal() as db:
                await db.execute(
                    update(Recording)
                    .where(Recording.id == row.id)
                    .values(status=RecordingStatus.RECORDING)
                )
                await db.commit()

            logger.info(
                "开始录制 stream_id=%s recording_id=%s file=%s",
                stream.id,
                row.id,
                filename,
            )
            return await self._load_recording(row.id)

    async def stop_recording(self, stream_id: int) -> Recording | None:
        async with self._lock:
            active = self._active.get(stream_id)
        if active is None:
            return None

        active.stop_requested = True
        await self._request_graceful_stop(active)

        if active.task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(active.task),
                    timeout=self.settings.recording_stop_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "录制停止超时，强制结束 recording_id=%s", active.recording_id
                )
                await stop_process(active.process, timeout=3)
                try:
                    await asyncio.wait_for(asyncio.shield(active.task), timeout=6)
                except asyncio.TimeoutError:
                    # Last resort so the DB never stays stuck in "recording".
                    await self._finalize(active, forced=True)

        return await self._load_recording(active.recording_id)

    async def stop_all(self) -> None:
        async with self._lock:
            stream_ids = list(self._active)
        if stream_ids:
            await asyncio.gather(
                *(self.stop_recording(stream_id) for stream_id in stream_ids),
                return_exceptions=True,
            )

    def is_recording(self, stream_id: int) -> bool:
        active = self._active.get(stream_id)
        return bool(active and not active.finalized)

    def active_recording_id(self, stream_id: int) -> int | None:
        active = self._active.get(stream_id)
        return active.recording_id if active and not active.finalized else None

    def is_active_recording_id(self, recording_id: int) -> bool:
        return any(
            item.recording_id == recording_id and not item.finalized
            for item in self._active.values()
        )

    @property
    def active_count(self) -> int:
        return sum(1 for item in self._active.values() if not item.finalized)

    # ------------------------------------------------------------- internals
    def _allocate_path(self, stream: Stream) -> tuple[str, Path, Path]:
        directory = self.settings.recordings_path
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{safe_filename_stem(stream.name)}_{local_timestamp()}"
        candidate = stem
        counter = 2
        while (directory / f"{candidate}.mp4").exists() or (
            directory / f"{candidate}.part.mp4"
        ).exists():
            candidate = f"{stem}_{counter}"
            counter += 1
        filename = f"{candidate}.mp4"
        return filename, directory / filename, directory / f"{candidate}.part.mp4"

    def _command(self, url: str, output: Path) -> list[str]:
        s = self.settings
        command = [
            s.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostats",
            *input_args(url, s.stream_timeout_seconds),
            "-i",
            url,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            s.recording_preset,
            "-b:v",
            s.recording_bitrate,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            s.recording_audio_bitrate,
            "-movflags",
            "+faststart",
        ]
        if s.recording_max_seconds > 0:
            command += ["-t", str(s.recording_max_seconds)]
        command += ["-f", "mp4", "-y", output.as_posix()]
        return command

    async def _request_graceful_stop(self, active: ActiveRecording) -> None:
        process = active.process
        if process.returncode is not None or process.stdin is None:
            return
        try:
            process.stdin.write(b"q\n")
            await process.stdin.drain()
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            await stop_process(process, timeout=3)

    async def _supervise(self, active: ActiveRecording) -> None:
        try:
            await active.process.wait()
        except asyncio.CancelledError:
            await stop_process(active.process, timeout=3)
            raise
        await self._finalize(active)

    async def _finalize(self, active: ActiveRecording, forced: bool = False) -> None:
        async with active.finalize_lock:
            if active.finalized:
                return
            active.finalized = True

            if active.process.returncode is None:
                await stop_process(active.process, timeout=3)
            try:
                if active.process.stdin is not None and not active.process.stdin.is_closing():
                    active.process.stdin.close()
            except (RuntimeError, OSError):
                pass
            if active.stderr_task is not None:
                try:
                    await asyncio.wait_for(active.stderr_task, timeout=3)
                except asyncio.TimeoutError:
                    active.stderr_task.cancel()
                except asyncio.CancelledError:
                    active.stderr_task.cancel()

            returncode = active.process.returncode
            success = False
            error_message: str | None = None
            duration: float | None = None
            file_size: int | None = None

            part_size = active.part_path.stat().st_size if active.part_path.exists() else 0
            if part_size > 0 and returncode == 0:
                probe = await self._probe_media(active.part_path)
                if probe is not None:
                    duration, probed_size = probe
                    file_size = probed_size or part_size
                    try:
                        os.replace(active.part_path, active.final_path)
                        success = True
                    except OSError as exc:
                        error_message = f"录制文件移动失败：{exc}"
                else:
                    error_message = "录制文件校验失败，可能没有有效的视频轨道"
            elif part_size == 0:
                error_message = "未收到任何音视频数据"
            elif forced:
                error_message = "FFmpeg 未能正常退出，录制文件可能不完整"
            else:
                error_message = self._log_tail(active.log_path)

            if not success:
                try:
                    active.part_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if active.final_path.exists() and active.final_path.stat().st_size == 0:
                    active.final_path.unlink(missing_ok=True)

            ended_at = utcnow()
            elapsed = max(0.0, (ended_at - active.started_at).total_seconds())
            status = RecordingStatus.COMPLETED if success else RecordingStatus.FAILED
            async with SessionLocal() as db:
                await db.execute(
                    update(Recording)
                    .where(Recording.id == active.recording_id)
                    .values(
                        status=status,
                        ended_at=ended_at,
                        duration_seconds=round(duration or elapsed, 2),
                        size_bytes=file_size if success else None,
                        error_message=None if success else (error_message or "录制失败")[:500],
                    )
                )
                await db.commit()

            async with self._lock:
                if self._active.get(active.stream_id) is active:
                    self._active.pop(active.stream_id, None)

            if success:
                logger.info(
                    "录制完成 recording_id=%s file=%s duration=%.1fs",
                    active.recording_id,
                    active.filename,
                    duration or elapsed,
                )
            else:
                logger.warning(
                    "录制失败 recording_id=%s rc=%s reason=%s",
                    active.recording_id,
                    returncode,
                    error_message,
                )

    async def _probe_media(self, path: Path) -> tuple[float, int] | None:
        command = [
            self.settings.ffprobe_bin,
            "-hide_banner",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration,size",
            "-of",
            "json",
            path.as_posix(),
        ]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                **process_creation_kwargs(),
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except asyncio.CancelledError:
            if process is not None:
                await stop_process(process, timeout=2)
            raise
        except (OSError, asyncio.TimeoutError):
            if process is not None:
                await stop_process(process, timeout=2)
            return None
        if process.returncode != 0:
            return None
        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
            fmt = payload.get("format") or {}
            duration = float(fmt.get("duration") or 0)
            size = int(fmt.get("size") or 0)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if duration <= 0:
            return None
        return duration, size

    def _log_tail(self, path: Path, limit: int = 4_000) -> str:
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return "FFmpeg 异常退出"
        return sanitize_ffmpeg_message(text)

    async def _mark_start_failed(self, recording_id: int, message: str) -> None:
        async with SessionLocal() as db:
            await db.execute(
                update(Recording)
                .where(Recording.id == recording_id)
                .values(
                    status=RecordingStatus.FAILED,
                    ended_at=utcnow(),
                    error_message=message[:500],
                )
            )
            await db.commit()

    async def _load_recording(self, recording_id: int) -> Recording:
        async with SessionLocal() as db:
            row = await db.scalar(select(Recording).where(Recording.id == recording_id))
            if row is None:
                raise LookupError("录制记录不存在")
            return row

    async def recover_interrupted(self) -> None:
        """Mark recordings left behind by an unclean shutdown and clean parts."""
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(Recording).where(
                        Recording.status.in_(
                            [RecordingStatus.STARTING, RecordingStatus.RECORDING]
                        )
                    )
                )
            ).scalars()
            interrupted = list(rows)
            for row in interrupted:
                row.status = RecordingStatus.FAILED
                row.ended_at = utcnow()
                row.error_message = "服务重启，录制被中断"
            await db.commit()

        for row in interrupted:
            part = self.settings.recordings_path / f"{Path(row.filename).stem}.part.mp4"
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass

        orphaned = 0
        for part in self.settings.recordings_path.glob("*.part.mp4"):
            try:
                part.unlink()
                orphaned += 1
            except OSError:
                pass
        if interrupted or orphaned:
            logger.warning(
                "已恢复中断录制：记录 %s 条，清理临时文件 %s 个",
                len(interrupted),
                orphaned,
            )
