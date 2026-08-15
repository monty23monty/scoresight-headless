from __future__ import annotations

from scoresight.ocr.smoothing import CharacterSmoother


def test_character_smoother_uses_per_position_majority() -> None:
    smoother = CharacterSmoother(3)
    assert smoother.add("12:34") == "12:34"
    smoother.add("12:39")
    assert smoother.add("12:34") == "12:34"


def test_character_smoother_prefers_latest_value_on_tie() -> None:
    smoother = CharacterSmoother(2)
    smoother.add("1")
    assert smoother.add("2") == "2"
