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


def test_service_config_requires_four_perspective_points() -> None:
    with pytest.raises(ValidationError):
        ServiceConfig(perspective=[{"x": 0, "y": 0}])


def test_output_config_requires_destination() -> None:
    with pytest.raises(ValidationError):
        OutputConfig(kind="webhook", settings={})
