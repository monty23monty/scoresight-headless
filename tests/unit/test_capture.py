from __future__ import annotations

import sys
import types
from datetime import UTC, datetime

from scoresight.capture.base import FramePacket
from scoresight.capture.decklink import DeckLinkCapture
from scoresight.capture.mock import MockCapture


def make_frame(sequence: int) -> FramePacket:
    return FramePacket(
        sequence=sequence, image=object(), width=10, height=10, captured_at=datetime.now(UTC)
    )


def test_mock_capture_returns_latest_frame() -> None:
    capture = MockCapture()
    capture.open()
    capture.push(make_frame(1))
    capture.push(make_frame(2))
    assert capture.read_latest().sequence == 2


def test_decklink_adapter_maps_native_contract(monkeypatch) -> None:
    native = types.ModuleType("scoresight_decklink")
    native.discover = lambda: [
        {
            "id": "card-1",
            "name": "DeckLink",
            "modes": [
                {
                    "id": "1080p30",
                    "width": 1920,
                    "height": 1080,
                    "frames_per_second": 30.0,
                    "pixel_format": "uyvy8",
                }
            ],
        }
    ]
    monkeypatch.setitem(sys.modules, "scoresight_decklink", native)
    devices = DeckLinkCapture.discover()
    assert devices[0].name == "DeckLink"
    assert devices[0].modes[0].frames_per_second == 30.0
