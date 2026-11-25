"""Convenience launcher: `python run.py` (or use the uvicorn command)."""

from __future__ import annotations

import uvicorn

from app.config import get_settings

settings = get_settings()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=settings.access_log,
    )
