from __future__ import annotations

import queue

from scoresight.capture.base import CaptureDevice, FramePacket


class MockCapture:
    def __init__(self) -> None:
        self.frames: queue.Queue[FramePacket] = queue.Queue(maxsize=2)
        self.is_open = False

    @classmethod
    def discover(cls) -> list[CaptureDevice]:
        return [CaptureDevice(id="mock", name="Synthetic test source")]

    def open(self) -> None:
        self.is_open = True

    def push(self, frame: FramePacket) -> None:
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(frame)

    def read_latest(self, timeout: float = 1.0) -> FramePacket | None:
        if not self.is_open:
            raise RuntimeError("capture is not open")
        try:
            frame = self.frames.get(timeout=timeout)
        except queue.Empty:
            return None
        while True:
            try:
                frame = self.frames.get_nowait()
            except queue.Empty:
                return frame

    def close(self) -> None:
        self.is_open = False
