from __future__ import annotations

import pytest
from pydantic import ValidationError

from scoresight.core.models import NormalizedRect, OutputConfig, RegionConfig, ServiceConfig


def test_normalized_rect_converts_to_pixel_bounds() -> None:
    rect = NormalizedRect(x=0.25, y=0.1, width=0.5, height=0.2)
    assert rect.pixels(1920, 1080) == (480, 108, 1440, 324)


@pytest.mark.parametrize(
    "payload",
    [
        {"x": 0.9, "y": 0.0, "width": 0.2, "height": 0.2},
        {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.2},
        {"x": -0.1, "y": 0.0, "width": 0.2, "height": 0.2},
    ],
)
def test_invalid_normalized_rect_is_rejected(payload: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        NormalizedRect.model_validate(payload)


def test_region_rejects_invalid_regular_expression() -> None:
    with pytest.raises(ValidationError):
        RegionConfig(
            name="Clock", rect=NormalizedRect(x=0, y=0, width=0.2, height=0.2), format_regex="["
        )


def test_region_defaults_to_two_confirmation_frames() -> None:
    region = RegionConfig(
        name="Clock", rect=NormalizedRect(x=0, y=0, width=0.2, height=0.2)
    )
    assert region.confirmation_frames == 2
    assert region.field_type == "text"


def test_service_config_requires_four_perspective_points() -> None:
    with pytest.raises(ValidationError):
        ServiceConfig(perspective=[{"x": 0, "y": 0}])


def test_output_config_requires_destination() -> None:
    with pytest.raises(ValidationError):
        OutputConfig(kind="webhook", settings={})


@pytest.mark.parametrize("missing", ["endpoint", "stream_id", "token"])
def test_fan_site_output_requires_connection_settings(missing: str) -> None:
    settings = {
        "endpoint": "wss://fan.example/ws/ocr",
        "stream_id": "stream1",
        "token": "secret",
    }
    settings.pop(missing)
    with pytest.raises(ValidationError):
        OutputConfig(kind="fan_site", settings=settings)


def test_fan_site_output_accepts_token_file() -> None:
    output = OutputConfig(
        kind="fan_site",
        settings={
            "endpoint": "wss://fan.example/ws/ocr",
            "stream_id": "stream1",
            "token_file": "/run/secrets/fan_site_token",
        },
    )
    assert output.settings["token_file"] == "/run/secrets/fan_site_token"
