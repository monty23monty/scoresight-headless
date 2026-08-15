from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from scoresight.capture.base import CaptureDevice, CaptureMode, FramePacket


class DeckLinkUnavailable(RuntimeError):
    pass


def _load_native() -> Any:
    try:
        return importlib.import_module("scoresight_decklink")
    except ImportError as exc:
        raise DeckLinkUnavailable(
            "DeckLink support requires the optional scoresight_decklink extension and "
            "Blackmagic Desktop Video runtime"
        ) from exc


class DeckLinkCapture:
    """Thin Python boundary around the optional Blackmagic SDK extension.

    The extension owns a two-frame ring and exposes only the newest complete frame,
    preventing the Python/OCR side from accumulating capture latency.
    """

    def __init__(self, device_id: str, mode_id: str) -> None:
        self.device_id = device_id
        self.mode_id = mode_id
        self._native: Any | None = None

    @classmethod
    def discover(cls) -> list[CaptureDevice]:
        native = _load_native()
        devices = []
        for device in native.discover():
            modes = tuple(
                CaptureMode(
                    id=mode["id"],
                    width=mode["width"],
                    height=mode["height"],
                    frames_per_second=mode["frames_per_second"],
                    pixel_format=mode["pixel_format"],
                )
                for mode in device.get("modes", [])
            )
            devices.append(CaptureDevice(id=device["id"], name=device["name"], modes=modes))
        return devices

    def open(self) -> None:
        native = _load_native()
        self._native = native.Capture(self.device_id, self.mode_id, ring_size=2)
        self._native.start()

    def read_latest(self, timeout: float = 1.0) -> FramePacket | None:
        if self._native is None:
            raise RuntimeError("capture is not open")
        frame = self._native.read_latest(timeout)
        if frame is None:
            return None
        return FramePacket(
            sequence=frame.sequence,
            image=frame.image,
            width=frame.width,
            height=frame.height,
            captured_at=frame.captured_at,
            monotonic_ns=frame.monotonic_ns,
            pixel_format=frame.pixel_format,
        )

    def close(self) -> None:
        if self._native is not None:
            self._native.stop()
            self._native = None


def native_contract() -> dict[str, Callable[..., Any] | str]:
    """Document the import-time contract for extension and test doubles."""

    return {
        "module": "scoresight_decklink",
        "discover": "discover() -> list[device dictionaries]",
        "capture": "Capture(device_id, mode_id, ring_size=2)",
    }
