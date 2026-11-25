from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_and_single_page_assets(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    assert "ffmpeg" in health.json()

    page = client.get("/")
    assert page.status_code == 200
    assert "Video Studio" in page.text
    assert "text/html" in page.headers["content-type"]

    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/vendor/hls.min.js").status_code == 200


def test_stream_crud(client: TestClient, stream_payload: dict[str, object]) -> None:
    created = client.post("/api/streams", json=stream_payload)
    assert created.status_code == 201
    data = created.json()
    assert data["name"] == stream_payload["name"]
    assert data["auto_reconnect"] is False
    stream_id = data["id"]

    listed = client.get("/api/streams")
    assert listed.status_code == 200
    assert any(item["id"] == stream_id for item in listed.json())

    updated = client.put(
        f"/api/streams/{stream_id}",
        json={"name": "更新后的测试流", "auto_reconnect": True},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "更新后的测试流"
    assert updated.json()["auto_reconnect"] is True

    deleted = client.delete(f"/api/streams/{stream_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/streams/{stream_id}").status_code == 404


def test_stream_validation(client: TestClient) -> None:
    response = client.post("/api/streams", json={"name": "bad", "url": "not-a-url"})
    assert response.status_code == 422
    assert "地址" in response.json()["detail"]

    response = client.post("/api/streams", json={"name": "   ", "url": "rtsp://host/live"})
    assert response.status_code == 422


def test_connectivity_check_marks_offline(
    client: TestClient, stream_payload: dict[str, object]
) -> None:
    created = client.post("/api/streams", json=stream_payload)
    stream_id = created.json()["id"]
    try:
        checked = client.post(f"/api/streams/{stream_id}/check")
        assert checked.status_code == 200
        assert checked.json()["status"] in {"offline", "unknown"}
        if checked.json()["status"] == "offline":
            assert checked.json()["status_message"]
    finally:
        client.delete(f"/api/streams/{stream_id}")


def test_preview_and_recording_errors(client: TestClient, stream_payload: dict[str, object]) -> None:
    created = client.post("/api/streams", json=stream_payload)
    stream_id = created.json()["id"]
    try:
        assert client.get(f"/api/streams/{stream_id}/preview/ready").json()["ready"] is False
        assert client.post("/api/streams/99999/preview/start").status_code == 404
        assert client.post(f"/api/streams/{stream_id}/recording/stop").status_code == 409
        assert client.get("/api/recordings/99999/file").status_code == 404
        assert client.delete("/api/recordings/99999").status_code == 404
        assert client.get("/media/hls/99999/index.m3u8").status_code == 404
    finally:
        client.delete(f"/api/streams/{stream_id}")


def test_system_info(client: TestClient) -> None:
    data = client.get("/api/system/info").json()
    assert data["stream_count"] >= 0
    assert data["recording_bitrate"]
    assert data["recordings_path"]
    assert data["version"]
