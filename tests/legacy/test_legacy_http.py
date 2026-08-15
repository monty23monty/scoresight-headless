from __future__ import annotations

from fastapi.testclient import TestClient

import http_server
from text_detection_target import TextDetectionTarget, TextDetectionTargetWithResult


def result() -> TextDetectionTargetWithResult:
    target = TextDetectionTarget(1, 2, 3, 4, "Home Score", {"templatefield": False})
    return TextDetectionTargetWithResult(
        target, "12", TextDetectionTargetWithResult.ResultState.Success
    )


def test_legacy_http_snapshot_formats_and_has_no_shutdown_route() -> None:
    http_server.update_http_server([result()])
    with TestClient(http_server.app) as client:
        json_response = client.get("/json")
        csv_response = client.get("/csv")
        shutdown_response = client.get("/shutdown")

    assert json_response.json()[0]["text"] == "12"
    assert "Home Score,12,1,2,3,4" in csv_response.text
    assert shutdown_response.status_code == 404
