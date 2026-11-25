"""Shared FFmpeg/process helpers.

Keeping these in one place means all three subsystems (probe, preview and
recorder) use exactly the same timeout, redaction and Windows process flags.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

_CREDENTIALS_RE = re.compile(
    # Greedy up to the last "@" of the authority so passwords containing "@"
    # are redacted completely, e.g. rtsp://admin:SAIda@0551@host/...
    r"(?P<scheme>(?:rtsps?|https?)://)(?P<userinfo>[^/\s]+)@",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:rtsps?|https?)://[^\s\"']+", re.IGNORECASE)
_ILLEGAL_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def is_rtsp_url(url: str) -> bool:
    return urlparse(url).scheme.lower() in {"rtsp", "rtsps"}


def process_creation_kwargs() -> dict[str, object]:
    """Hide console windows on Windows; create a clean group on POSIX."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def input_args(url: str, timeout_seconds: int) -> list[str]:
    """Options that must be placed before ``-i`` for a media input.

    RTSP uses TCP plus the RTSP demuxer's own socket timeout, which is the
    only spelling FFmpeg accepts for RTSP inputs:

    * ``-rw_timeout`` is an AVIOContext/URLContext option. FFmpeg 7+ rejects
      it as an input option and aborts with "Option rw_timeout not found"
      before it ever connects, so previews never produce a playlist.
    * ``-stimeout`` was removed in FFmpeg 7 and is now unrecognised.
    * ``-timeout`` on the RTSP demuxer is the socket I/O timeout in
      microseconds and works on FFmpeg 7/8 (on 5.x/6.x it only sets the
      listen timeout, which a client connection never uses).
    """
    args: list[str] = []
    if is_rtsp_url(url):
        # TCP avoids the packet loss/reordering artefacts common with UDP.
        args += ["-rtsp_transport", "tcp"]
        # Socket read/write timeout (microseconds) protects against silent hangs.
        args += ["-timeout", str(max(1, timeout_seconds) * 1_000_000)]
    return args


def redact_url(url: str) -> str:
    """Hide credentials when a URL appears in logs or API error messages."""
    return _CREDENTIALS_RE.sub(lambda m: f"{m.group('scheme')}***@", url)


def redact_text(text: str, source_url: str = "") -> str:
    """Redact credentials from arbitrary FFmpeg output before it is persisted."""
    cleaned = text.replace(source_url, redact_url(source_url)) if source_url else text
    cleaned = _CREDENTIALS_RE.sub(lambda m: f"{m.group('scheme')}***@", cleaned)
    cleaned = _URL_RE.sub(lambda m: redact_url(m.group(0)), cleaned)
    return cleaned


def sanitize_ffmpeg_message(text: str, source_url: str = "") -> str:
    cleaned = redact_text(text, source_url)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return "FFmpeg 未返回详细错误"
    message = lines[-1]
    return message[:350]


def safe_filename_stem(name: str, max_length: int = 48) -> str:
    """Turn a user-supplied stream name into a safe cross-platform stem."""
    stem = _ILLEGAL_FILENAME_RE.sub("_", name)
    stem = re.sub(r"\s+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip(" ._")
    if not stem:
        stem = "stream"
    if stem.upper() in _RESERVED_WINDOWS_NAMES:
        stem = f"stream_{stem}"
    return stem[:max_length].rstrip(" ._") or "stream"


def resolve_within(base: Path, relative: str | Path) -> Path:
    """Resolve a child path and reject traversal outside *base*."""
    base_resolved = base.resolve()
    target = (base_resolved / relative).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise ValueError("非法文件路径")
    return target


async def _wait_or_kill(process: asyncio.subprocess.Process, timeout: float) -> int | None:
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        await stop_process(process, timeout=2)
        return process.returncode


async def stop_process(process: asyncio.subprocess.Process, timeout: float = 5) -> None:
    """Terminate a process and, if necessary, kill it."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            pass


async def ffmpeg_available(binary: str) -> tuple[bool, str]:
    """Check the binary once at startup and return a short version string."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **process_creation_kwargs(),
        )
    except (FileNotFoundError, OSError) as exc:
        return False, str(exc)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=8)
    except asyncio.TimeoutError:
        await stop_process(process, timeout=2)
        return False, "ffmpeg -version 超时"
    if process.returncode != 0:
        return False, f"ffmpeg 退出码 {process.returncode}"
    first_line = stdout.decode("utf-8", errors="replace").splitlines()[0] if stdout else ""
    return True, first_line.strip() or "available"
