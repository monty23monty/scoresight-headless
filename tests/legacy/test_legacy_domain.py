from __future__ import annotations

from defaults import default_info_for_box_name, normalize_settings_dict
from template_fields import evaluate_template_field
from text_detection_target import TextDetectionTarget, TextDetectionTargetWithResult


def target(name: str, value: str) -> TextDetectionTargetWithResult:
    base = TextDetectionTarget(0, 0, 10, 10, name, {"templatefield_text": ""})
    return TextDetectionTargetWithResult(
        base, value, TextDetectionTargetWithResult.ResultState.Success
    )


def test_legacy_target_mutable_defaults_are_isolated() -> None:
    first = TextDetectionTarget(0, 0, 1, 1, "one")
    second = TextDetectionTarget(0, 0, 1, 1, "two")
    first.settings["changed"] = True
    first.mini_rects.append(object())
    assert second.settings == {}
    assert second.mini_rects == []


def test_legacy_default_settings_are_normalized() -> None:
    settings = normalize_settings_dict({}, default_info_for_box_name("Time"))
    assert settings["skip_empty"] is True
    assert settings["format_regex"]
    assert settings["type"] == 1


def test_legacy_template_fields_replace_named_values() -> None:
    home = target("Home", "12")
    away = target("Away", "9")
    template = target("Summary", "")
    template.settings["templatefield_text"] = "{{Home}}–{{Away}}"
    assert evaluate_template_field([home, away, template], template) == "12–9"
