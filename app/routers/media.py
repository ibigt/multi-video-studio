"""HLS playlist/segment delivery for live browser previews."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings
from app.dependencies import get_preview_manager
from app.services.ffmpeg_utils import resolve_within
from app.services.stream_manager import PreviewManager

router = APIRouter(tags=["media"])

_SEGMENT_RE = re.compile(r"^segment_\d{5,9}\.ts$")


@router.get("/media/hls/{stream_id}/{filename}")
async def serve_hls(
    stream_id: int,
    filename: str,
    preview: PreviewManager = Depends(get_preview_manager),
) -> FileResponse:
    # Any request for a preview asset proves the browser is still watching.
    preview.touch(stream_id)

    if filename != "index.m3u8" and not _SEGMENT_RE.match(filename):
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    if not preview.is_known(stream_id):
        raise HTTPException(status_code=404, detail="预览未启动或已停止")

    settings = get_settings()
    try:
        path = resolve_within(settings.hls_path / str(stream_id), filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法路径") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="媒体尚未就绪")

    media_type = (
        "application/vnd.apple.mpegurl" if filename == "index.m3u8" else "video/mp2t"
    )
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            # hls.js uses XHR/fetch; this also permits native Safari HLS.
            "Access-Control-Allow-Origin": "*",
        },
    )
