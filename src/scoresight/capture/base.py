from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CaptureMode:
    id: str
    width: int
    height: int
    frames_per_second: float
    pixel_format: str


@dataclass(frozen=True, slots=True)
class CaptureDevice:
    id: str
    name: str
    modes: tuple[CaptureMode, ...] = ()


@dataclass(slots=True)
class FramePacket:
    sequence: int
    image: Any
    width: int
    height: int
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    monotonic_ns: int = 0
    pixel_format: str = "gray8"


class CaptureSource(Protocol):
    @classmethod
    def discover(cls) -> list[CaptureDevice]: ...

    def open(self) -> None: ...

    def read_latest(self, timeout: float = 1.0) -> FramePacket | None: ...

    def close(self) -> None: ...
