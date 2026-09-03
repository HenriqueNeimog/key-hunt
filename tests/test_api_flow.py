from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.fakes import FakeAnalyzer, FakeMediaClient


def import_round(client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    response = client.post(
        "/api/playlists/import",
        json={"url": "https://www.youtube.com/playlist?list=PLabcdefghij"},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return response.json()


def wait_ready(client: TestClient, round_id: str) -> dict[str, object]:
    for _ in range(100):
        response = client.get(f"/api/rounds/{round_id}/status")
        assert response.status_code == 200
        payload = response.json()
        assert "detected_key" not in response.text
        assert "detected_scale" not in response.text
        assert "confidence" not in response.text
        if payload["status"] == "ready":
            return payload
        time.sleep(0.01)
    raise AssertionError("round did not become ready")


def test_complete_flow_and_caches(
    client: TestClient,
    csrf_headers: dict[str, str],
    fake_media: FakeMediaClient,
    fake_analyzer: FakeAnalyzer,
) -> None:
    created = import_round(client, csrf_headers)
    round_id = str(created["round_id"])
    ready = wait_ready(client, round_id)
    assert ready["audio_url"] == f"/api/rounds/{round_id}/audio"
    assert ready["can_reveal"] is True

    audio = client.get(f"/api/rounds/{round_id}/audio", headers={"Range": "bytes=0-9"})
    assert audio.status_code == 206
    assert len(audio.content) == 10
    assert audio.headers["accept-ranges"] == "bytes"

    reveal = client.post(f"/api/rounds/{round_id}/reveal", headers=csrf_headers)
    assert reveal.status_code == 200
    assert reveal.json() == {
        "key": "F#",
        "scale": "minor",
        "confidence": 0.82,
        "low_confidence": False,
    }
    result = client.post(
        f"/api/rounds/{round_id}/result",
        json={"result": "correct"},
        headers=csrf_headers,
    )
    assert result.status_code == 200
    assert (
        client.post(
            f"/api/rounds/{round_id}/result",
            json={"result": "incorrect"},
            headers=csrf_headers,
        ).status_code
        == 409
    )

    stats = client.get("/api/stats").json()
    assert stats["total"] == 1
    assert stats["correct"] == 1
    assert stats["accuracy"] == 100.0

    second = import_round(client, csrf_headers)
    wait_ready(client, str(second["round_id"]))
    third = import_round(client, csrf_headers)
    wait_ready(client, str(third["round_id"]))
    assert fake_media.metadata_calls == 1
    assert fake_media.download_calls == 2
    assert fake_analyzer.calls == 2


def test_reveal_and_answer_are_ordered(client: TestClient, csrf_headers: dict[str, str]) -> None:
    created = import_round(client, csrf_headers)
    round_id = str(created["round_id"])
    early_reveal = client.post(f"/api/rounds/{round_id}/reveal", headers=csrf_headers)
    assert early_reveal.status_code in {200, 409}
    if early_reveal.status_code == 409:
        assert (
            client.post(
                f"/api/rounds/{round_id}/result",
                json={"result": "correct"},
                headers=csrf_headers,
            ).status_code
            == 409
        )


def test_csrf_required(client: TestClient) -> None:
    response = client.post(
        "/api/playlists/import",
        json={"url": "https://www.youtube.com/playlist?list=PLabcdefghij"},
    )
    assert response.status_code == 403


def test_round_isolation(client: TestClient, csrf_headers: dict[str, str]) -> None:
    created = import_round(client, csrf_headers)
    round_id = str(created["round_id"])
    wait_ready(client, round_id)
    original_player = client.cookies.get("key_hunt_player")
    client.cookies.set("key_hunt_player", "00000000-0000-0000-0000-000000000000.invalid")
    assert client.get(f"/api/rounds/{round_id}/status").status_code == 401
    assert original_player is not None
    client.cookies.set("key_hunt_player", original_player)
