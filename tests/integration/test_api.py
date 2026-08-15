from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from scoresight.core.config import ConfigStore
from scoresight.core.deployment import DeploymentSettings
from scoresight.core.models import OutputConfig, ResultBatch, ResultField, ResultState
from scoresight.core.service import PreviewFrame
from scoresight.web.app import create_app
from scoresight.web.cloudflare import AccessAuthenticationError


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
    assert app.state.service.results.subscriber_count == 0


def test_preview_websocket_starts_with_latest_frame(tmp_path) -> None:
    app, token = make_client(tmp_path)
    app.state.service.latest_preview = PreviewFrame(
        jpeg=b"jpeg-data", width=640, height=360, sequence=12
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(f"/api/v1/preview?token={token}") as websocket,
    ):
        metadata = websocket.receive_json()
        jpeg = websocket.receive_bytes()
    assert metadata == {
        "type": "preview.meta",
        "width": 640,
        "height": 360,
        "sequence": 12,
    }
    assert jpeg == b"jpeg-data"
    assert app.state.service.preview_frames.subscriber_count == 0


def test_filtered_region_preview_requires_auth_and_returns_png(tmp_path) -> None:
    app, token = make_client(tmp_path)
    app.state.service.latest_region_previews["clock"] = b"\x89PNG\r\n\x1a\npreview"
    with TestClient(app) as client:
        assert client.get("/api/v1/regions/clock/filter-preview").status_code == 401
        preview = client.get(
            "/api/v1/regions/clock/filter-preview",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["cache-control"] == "no-store"
    assert preview.content.startswith(b"\x89PNG")


def test_dashboard_includes_last_accepted_frame_preview(tmp_path) -> None:
    app, token = make_client(tmp_path)
    with TestClient(app) as client:
        client.post("/login", data={"token": token})
        dashboard = client.get("/")
        script = client.get("/static/operator.js")
    assert dashboard.status_code == 200
    assert "Accepted frame" in dashboard.text
    assert "OCR filter preview" in dashboard.text
    assert "Current candidate" in dashboard.text
    assert "Confirm frames" in dashboard.text
    assert "Clock / time" in dashboard.text
    assert "captureAcceptedPreview" in script.text
    assert "Last accepted frame for" in script.text
    assert "updatePreviewGeometry" in script.text
    assert "live fan-site OCR WebSocket" in dashboard.text
    assert "refreshOutputStatus" in script.text


class FakeAccessVerifier:
    closed = False

    async def verify(self, token):
        if token != "valid-access-token":
            raise AccessAuthenticationError("invalid Cloudflare Access assertion")
        return {"sub": "operator", "email": "operator@example.com"}

    async def close(self):
        self.closed = True


def cloudflare_client(tmp_path):
    deployment = DeploymentSettings(
        auth_mode="cloudflare_access",
        cloudflare_team_domain="https://team.cloudflareaccess.com",
        cloudflare_audience="audience",
    )
    verifier = FakeAccessVerifier()
    app = create_app(
        tmp_path / "config.json",
        tmp_path / "profiles",
        start_runtime=False,
        deployment=deployment,
        access_verifier=verifier,
    )
    return app, verifier


def test_cloudflare_mode_authenticates_http_csrf_and_websocket(tmp_path) -> None:
    app, verifier = cloudflare_client(tmp_path)
    app.state.service.latest_result = ResultBatch(
        stream_id="stream",
        sequence=9,
        captured_at=datetime.now(UTC),
        latency_ms=2,
        fields=[ResultField(id="home", name="Home", value="7", state=ResultState.OK)],
    )
    access = {"Cf-Access-Jwt-Assertion": "valid-access-token"}
    with TestClient(app) as client:
        assert client.get("/login", headers=access).status_code == 404
        assert client.get("/api/v1/config").status_code == 401
        assert client.get("/api/v1/health", params={"token": "ignored"}).status_code == 401
        dashboard = client.get("/", headers=access)
        assert dashboard.status_code == 200
        csrf = client.cookies.get("scoresight_csrf")
        current = client.get("/api/v1/config", headers=access).json()
        assert client.put("/api/v1/config", headers=access, json=current).status_code == 403
        saved = client.put(
            "/api/v1/config",
            headers={**access, "X-CSRF-Token": csrf},
            json=current,
        )
        assert saved.status_code == 200
        with client.websocket_connect("/api/v1/events", headers=access) as websocket:
            assert websocket.receive_json()["sequence"] == 9
    assert verifier.closed


def test_liveness_readiness_and_prometheus_metrics(tmp_path) -> None:
    app, _ = make_client(tmp_path)
    with TestClient(app) as client:
        assert client.get("/livez").json() == {"status": "ok"}
        assert client.get("/readyz").status_code == 503
        app.state.service.metrics.last_frame_monotonic = time.monotonic()
        assert client.get("/readyz").status_code == 200
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "scoresight_capture_frames_total" in metrics.text
        assert "python_info" in metrics.text


def test_output_secrets_are_redacted_and_restored_on_save(tmp_path) -> None:
    app, token = make_client(tmp_path)
    store = app.state.config_store
    current = store.load()
    configured = current.model_copy(
        update={
            "outputs": [
                OutputConfig(
                    id="fan",
                    kind="fan_site",
                    enabled=False,
                    settings={
                        "endpoint": "wss://fan.example/ws/ocr",
                        "stream_id": "stream1",
                        "token": "top-secret",
                    },
                )
            ]
        }
    )
    store.replace(configured, current.revision)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        safe = client.get("/api/v1/config", headers=headers).json()
        assert safe["outputs"][0]["settings"]["token"] == "__redacted__"
        safe["source"]["mode"] = "720p60"
        assert client.put("/api/v1/config", headers=headers, json=safe).status_code == 200
    assert store.load().outputs[0].settings["token"] == "top-secret"
