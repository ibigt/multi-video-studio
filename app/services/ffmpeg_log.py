"""Drain FFmpeg stderr into a sanitized log file.

Passing a raw file descriptor to `stderr` would persist camera credentials if
FFmpeg prints its input URL. This pump reads the pipe line-by-line, redacts
credentials, and only then writes the line to disk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.logging_config import get_logger
from app.services.ffmpeg_utils import redact_text

logger = get_logger("ffmpeg-log")


async def pump_stderr(
    stream: asyncio.StreamReader,
    log_path: Path,
    source_url: str,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        handle = open(log_path, "ab", buffering=0)  # noqa: SIM115 - closed in finally
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            # A malformed line should not be able to balloon the log.
            safe = redact_text(text[:8192], source_url=source_url)
            handle.write(safe.encode("utf-8", errors="replace"))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("写入 FFmpeg 日志失败: %s", log_path.name)
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
