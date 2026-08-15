from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from scoresight.core.config import ConfigStore
from scoresight.core.models import ResultBatch, ResultField, ResultState
from scoresight.web.app import create_app


def make_client(tmp_path):
    config_path = tmp_path / "config.json"
    profile_path = tmp_path / "profiles"
    store = ConfigStore(config_path)
    token = store.load().security.admin_token
    app = create_app(config_path, profile_path, start_runtime=False)
    return app, token


def test_api_auth_config_revision_and_profiles(tmp_path) -> None:
    app, token = make_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        assert client.get("/api/v1/config").status_code == 401
        current = client.get("/api/v1/config", headers=headers).json()
        current["source"]["mode"] = "1080p25"
        saved = client.put("/api/v1/config", headers=headers, json=current)
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert client.put("/api/v1/config", headers=headers, json=current).status_code == 409

        assert client.put("/api/v1/profiles/game", headers=headers).status_code == 200
        assert client.get("/api/v1/profiles", headers=headers).json() == ["game"]
        assert (
            client.get("/api/v1/profiles/game", headers=headers).json()["source"]["mode"]
            == "1080p25"
        )

        changed = saved.json()
        changed["source"]["mode"] = "1080p30"
        assert client.put("/api/v1/config", headers=headers, json=changed).status_code == 200
        activated = client.post("/api/v1/profiles/game/activate", headers=headers)
        assert activated.status_code == 200
        assert activated.json()["source"]["mode"] == "1080p25"

        invalid = activated.json()
        invalid["outputs"] = [{"kind": "webhook", "settings": {}}]
        assert client.put("/api/v1/config", headers=headers, json=invalid).status_code == 422


def test_login_cookie_csrf_and_read_token(tmp_path) -> None:
    app, token = make_client(tmp_path)
    with TestClient(app) as client:
        login = client.post("/login", data={"token": token}, follow_redirects=False)
        assert login.status_code == 303
        csrf = client.cookies.get("scoresight_csrf")
        created = client.post("/api/v1/read-tokens", headers={"X-CSRF-Token": csrf})
        assert created.status_code == 200
        read_token = created.json()["token"]
        client.cookies.clear()
        assert client.get("/api/v1/health", params={"token": read_token}).status_code == 200
        assert client.get("/api/v1/config", params={"token": read_token}).status_code == 401


def test_event_websocket_starts_with_latest_snapshot(tmp_path) -> None:
    app, token = make_client(tmp_path)
    app.state.service.latest_result = ResultBatch(
        stream_id="stream",
        sequence=7,
        captured_at=datetime.now(UTC),
        latency_ms=4,
        fields=[ResultField(id="clock", name="Clock", value="1:23", state=ResultState.OK)],
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(f"/api/v1/events?token={token}") as websocket,
    ):
        event = websocket.receive_json()
    assert event["sequence"] == 7
    assert event["fields"][0]["value"] == "1:23"
