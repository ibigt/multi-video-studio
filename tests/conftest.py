"""Pytest fixtures.

Environment variables are set before the application is imported so every
test run uses a throwaway data directory instead of the project's real data.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[object]:
    data_dir = tmp_path_factory.mktemp("rtsp-studio-test-data")
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["STREAM_TIMEOUT_SECONDS"] = "2"
    os.environ["STATUS_CHECK_INTERVAL_SECONDS"] = "300"
    os.environ["LOG_LEVEL"] = "WARNING"

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def stream_payload() -> dict[str, object]:
    return {
        "name": "测试流",
        "url": "rtsp://127.0.0.1:1/test",
        "auto_reconnect": False,
    }


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(os.environ["DATA_DIR"])
