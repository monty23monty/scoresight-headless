from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

from scoresight.capture.base import CaptureDevice, FramePacket


class OpenCVCapture:
    def __init__(
        self,
        source: str | int,
        *,
        pace: bool = False,
        open_timeout: float = 5.0,
        read_timeout: float = 2.0,
        rtsp_transport: str = "tcp",
    ) -> None:
        self.source = source
        self.pace = pace
        self.open_timeout = open_timeout
        self.read_timeout = read_timeout
        self.rtsp_transport = rtsp_transport
        self._capture: Any | None = None
        self._sequence = 0
        self._frame_period = 0.0
        self._next_frame_at = 0.0

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

        is_network = isinstance(self.source, str) and self.source.lower().startswith("rtsp")
        if is_network:
            os.environ.setdefault(
                "OPENCV_FFMPEG_CAPTURE_OPTIONS", f"rtsp_transport;{self.rtsp_transport}"
            )
            params = [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                round(self.open_timeout * 1000),
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                round(self.read_timeout * 1000),
            ]
            self._capture = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, params)
            self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            self._capture = cv2.VideoCapture(self.source)
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            raise RuntimeError(f"unable to open capture source {self.source!r}")
        if self.pace:
            fps = float(self._capture.get(cv2.CAP_PROP_FPS))
            if 0.1 <= fps <= 240.0:
                self._frame_period = 1.0 / fps
                self._next_frame_at = time.perf_counter()

    def read_latest(self, timeout: float = 1.0) -> FramePacket | None:
        if self._capture is None:
            raise RuntimeError("capture is not open")
        if self._frame_period:
            now = time.perf_counter()
            if self._next_frame_at > now:
                time.sleep(min(self._next_frame_at - now, timeout))
        ok, image = self._capture.read()
        if not ok:
            return None
        if self._frame_period:
            now = time.perf_counter()
            self._next_frame_at = max(self._next_frame_at + self._frame_period, now)
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
        self._frame_period = 0.0
        self._next_frame_at = 0.0
