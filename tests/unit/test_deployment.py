from __future__ import annotations

import pytest

from scoresight.core.deployment import DeploymentSettings
from scoresight.core.secrets import REDACTED, read_secret_file, redact_mapping, restore_redacted


def test_deployment_settings_derive_paths_and_origin(tmp_path) -> None:
    settings = DeploymentSettings(
        data_dir=tmp_path,
        public_url="https://scoresight.example.com/",
    )
    assert settings.config_path(None) == tmp_path / "config-v1.json"
    assert settings.profile_path(None) == tmp_path / "profiles"
    assert settings.effective_allowed_hosts == ("scoresight.example.com", "127.0.0.1", "localhost")
    assert settings.effective_allowed_origins == ("https://scoresight.example.com",)
    assert settings.secure_cookies


def test_cloudflare_settings_require_issuer_and_audience() -> None:
    with pytest.raises(ValueError, match="requires"):
        DeploymentSettings(auth_mode="cloudflare_access").validate()


def test_secret_file_and_redacted_round_trip(tmp_path) -> None:
    secret_file = tmp_path / "token"
    secret_file.write_text("  fan-secret\n", encoding="utf-8")
    assert read_secret_file(secret_file) == "fan-secret"
    current = {"id": "one", "settings": {"token": "fan-secret", "endpoint": "wss://fan"}}
    safe = redact_mapping(current)
    assert safe["settings"]["token"] == REDACTED
    assert restore_redacted(safe, current) == current


def test_empty_secret_file_is_rejected(tmp_path) -> None:
    secret_file = tmp_path / "empty"
    secret_file.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        read_secret_file(secret_file)
