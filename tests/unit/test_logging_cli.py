from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from scoresight import cli
from scoresight.core.deployment import DeploymentSettings
from scoresight.core.logging import (
    JsonFormatter,
    configure_logging,
    redact_text,
    reset_request_id,
    set_request_id,
)


def test_logging_redacts_secrets_and_includes_request_id() -> None:
    assert "secret-token" not in redact_text("Authorization: Bearer secret-token")
    assert "abc" not in redact_text("/preview?token=abc&layout=one")
    formatter = JsonFormatter()
    token = set_request_id("request-123")
    try:
        record = logging.LogRecord(
            "scoresight.test",
            logging.INFO,
            __file__,
            1,
            'output {"token":"secret-token"}',
            (),
            None,
        )
        payload = json.loads(formatter.format(record))
    finally:
        reset_request_id(token)
    assert payload["request_id"] == "request-123"
    assert "secret-token" not in payload["message"]


def test_configure_text_and_json_logging() -> None:
    configure_logging("warning", json_logs=False)
    assert logging.getLogger().level == logging.WARNING
    configure_logging("info", json_logs=True)
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


def test_cli_uses_single_worker_deployment_settings(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    deployment = DeploymentSettings(trusted_proxies="10.0.0.0/8", access_log=False)
    monkeypatch.setattr(cli.DeploymentSettings, "from_env", lambda: deployment)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "create_app", lambda *args, **kwargs: "app")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(
        sys,
        "argv",
        ["scoresight-service", "--data-dir", str(tmp_path), "--host", "0.0.0.0"],
    )
    cli.main()
    assert captured["workers"] == 1
    assert captured["forwarded_allow_ips"] == "10.0.0.0/8"
    assert captured["host"] == "0.0.0.0"
    assert captured["log_config"] is None
    assert Path(tmp_path) == tmp_path
