from __future__ import annotations

import sys
import types
from datetime import UTC, datetime

import numpy as np

from scoresight.capture.base import FramePacket
from scoresight.capture.decklink import DeckLinkCapture
from scoresight.capture.mock import MockCapture
from scoresight.capture.opencv import OpenCVCapture


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


def test_rtsp_capture_uses_timeouts_tcp_and_single_frame_buffer(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeVideoCapture:
        def __init__(self, *args):
            calls.append(args)

        def isOpened(self):
            return True

        def set(self, key, value):
            calls.append(("set", key, value))
            return True

        def read(self):
            return True, np.zeros((8, 12, 3), dtype=np.uint8)

        def release(self):
            calls.append(("release",))

    fake_cv2 = types.SimpleNamespace(
        VideoCapture=FakeVideoCapture,
        CAP_FFMPEG=1900,
        CAP_PROP_OPEN_TIMEOUT_MSEC=53,
        CAP_PROP_READ_TIMEOUT_MSEC=54,
        CAP_PROP_BUFFERSIZE=38,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    capture = OpenCVCapture("rtsp://media/scoreboard", open_timeout=4, read_timeout=1.5)
    capture.open()
    frame = capture.read_latest()
    capture.close()

    assert calls[0] == ("rtsp://media/scoreboard", 1900, [53, 4000, 54, 1500])
    assert ("set", 38, 1) in calls
    assert frame is not None and frame.width == 12
    assert capture._capture is None
