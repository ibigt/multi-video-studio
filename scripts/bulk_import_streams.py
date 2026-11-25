#!/usr/bin/env python3
"""Batch-create RTSP streams through the Video Studio HTTP API.

用法::

    python scripts/bulk_import_streams.py --dry-run   # 只预览，不写入
    python scripts/bulk_import_streams.py             # 真正导入

地址文件每行一个 URL，空行和 ``#`` 注释会被忽略。脚本按
``协议 + 主机 + 端口 + 路径`` 去重，并跳过应用里已存在的流，因此可以
重复执行而不会产生重复数据。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_PORT = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443}


def mask_url(url: str) -> str:
    """隐藏密码，避免把凭据打到终端/日志里。

    贪婪匹配到 authority 的最后一个 ``@``，因此密码里含 ``@`` 也能整体遮住。
    """
    return re.sub(r"(?i)(rtsps?|https?)://[^/\s]+@", r"\1://***@", url)


def stream_key(url: str) -> tuple[str, str, int, str]:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port or DEFAULT_PORT.get(scheme, 0)
    except ValueError:
        port = DEFAULT_PORT.get(scheme, 0)
    return (scheme, (parts.hostname or "").lower(), port, parts.path.rstrip("/"))


def read_urls(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"找不到地址文件：{path}")
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def api(method: str, base_url: str, path: str, payload: object | None = None, timeout: int = 60):
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
        return response.status, (json.loads(body) if body else None)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="批量导入 RTSP 视频流")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(__file__).with_name("cameras.txt"),
        help="地址清单（默认 scripts/cameras.txt）",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dry-run", action="store_true", help="只显示导入计划")
    parser.add_argument("--delay", type=float, default=0.5, help="每路之间的间隔秒数")
    parser.add_argument("--prefix", default="", help="流名称前缀，默认直接用 IP")
    parser.add_argument(
        "--no-auto-reconnect",
        dest="auto_reconnect",
        action="store_false",
        help="关闭断流自动重连（默认开启）",
    )
    args = parser.parse_args(argv)

    urls = read_urls(args.file)
    if not urls:
        print(f"地址文件为空：{args.file}", file=sys.stderr)
        return 2

    try:
        _, existing = api("GET", args.base_url, "/api/streams")
    except (URLError, HTTPError, TimeoutError) as exc:
        print(f"无法连接后端 {args.base_url}：{exc}", file=sys.stderr)
        return 2
    existing_keys = {stream_key(item["url"]) for item in (existing or [])}

    unique: list[tuple[str, str]] = []  # (name, url)
    seen: dict[tuple[str, str, int, str], str] = {}
    duplicates: list[str] = []
    already: list[str] = []

    for url in urls:
        key = stream_key(url)
        if key in seen:
            duplicates.append(url)
            continue
        seen[key] = url
        if key in existing_keys:
            already.append(url)
            continue
        name = args.prefix + (key[1] or "stream")
        unique.append((name, url))

    print(f"清单 {len(urls)} 条 → 去重后 {len(seen)} 条 → 待新增 {len(unique)} 条")
    if duplicates:
        print(f"清单内重复（跳过 {len(duplicates)} 条）：")
        for url in duplicates:
            print(f"  - {mask_url(url)}")
    if already:
        print(f"应用中已存在（跳过 {len(already)} 条）：")
        for url in already:
            print(f"  - {mask_url(url)}")

    if args.dry_run:
        print("\n预演结果（未写入）：")
        for index, (name, url) in enumerate(unique, 1):
            print(f"  [{index:>2}/{len(unique)}] {name:<16} {mask_url(url)}")
        print("\n确认无误后去掉 --dry-run 再执行，即可真正导入。")
        return 0

    created: list[str] = []
    failed: list[tuple[str, str]] = []
    total = len(unique)
    for index, (name, url) in enumerate(unique, 1):
        try:
            _, body = api(
                "POST",
                args.base_url,
                "/api/streams",
                {"name": name, "url": url, "auto_reconnect": args.auto_reconnect},
            )
            created.append(name)
            print(f"[{index:>2}/{total}] 已新增 {name}（id={body.get('id')}）")
        except HTTPError as exc:
            detail = ""
            try:
                detail = (json.loads(exc.read()) or {}).get("detail", "")
            except Exception:
                pass
            failed.append((name, str(detail or exc)))
            print(f"[{index:>2}/{total}] 失败 {name}：{detail or exc}")
        except (URLError, TimeoutError) as exc:
            failed.append((name, str(exc)))
            print(f"[{index:>2}/{total}] 失败 {name}：{exc}")
        if index < total and args.delay:
            time.sleep(args.delay)

    print(
        f"\n完成：新增 {len(created)} 条，跳过重复 {len(duplicates)} 条，"
        f"跳过已存在 {len(already)} 条，失败 {len(failed)} 条。"
    )
    if failed:
        print("失败明细：", file=sys.stderr)
        for name, reason in failed:
            print(f"  - {name}: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
