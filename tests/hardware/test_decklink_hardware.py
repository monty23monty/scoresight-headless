from __future__ import annotations

import os

import pytest

from scoresight.capture.decklink import DeckLinkCapture

pytestmark = pytest.mark.decklink_hardware


@pytest.mark.skipif(
    os.getenv("SCORESIGHT_DECKLINK_HARDWARE") != "1",
    reason="requires a DeckLink card and Blackmagic Desktop Video",
)
def test_decklink_discovers_device_and_receives_1080p_frame() -> None:
    devices = DeckLinkCapture.discover()
    assert devices, "no DeckLink device discovered"
    mode = next(
        mode
        for mode in devices[0].modes
        if (mode.width, mode.height, round(mode.frames_per_second)) == (1920, 1080, 30)
    )
    capture = DeckLinkCapture(devices[0].id, mode.id)
    capture.open()
    try:
        frame = capture.read_latest(timeout=2.0)
    finally:
        capture.close()
    assert frame is not None
    assert (frame.width, frame.height) == (1920, 1080)
