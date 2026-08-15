from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from scoresight.capture.base import CaptureDevice, FramePacket


class OpenCVCapture:
    def __init__(self, source: str | int) -> None:
        self.source = source
        self._capture: Any | None = None
        self._sequence = 0

    @classmethod
    def discover(cls, limit: int = 10) -> list[CaptureDevice]:
        import cv2

        devices = []
        for index in range(limit):
            capture = cv2.VideoCapture(index)
            try:
                if capture.isOpened():
                    devices.append(CaptureDevice(id=str(index), name=f"Camera {index}"))
            finally:
                capture.release()
        return devices

    def open(self) -> None:
        import cv2

        self._capture = cv2.VideoCapture(self.source)
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            raise RuntimeError(f"unable to open capture source {self.source!r}")

    def read_latest(self, timeout: float = 1.0) -> FramePacket | None:
        if self._capture is None:
            raise RuntimeError("capture is not open")
        ok, image = self._capture.read()
        if not ok:
            return None
        self._sequence += 1
        height, width = image.shape[:2]
        return FramePacket(
            sequence=self._sequence,
            image=image,
            width=width,
            height=height,
            captured_at=datetime.now(UTC),
            monotonic_ns=time.perf_counter_ns(),
            pixel_format="bgr24",
        )

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
