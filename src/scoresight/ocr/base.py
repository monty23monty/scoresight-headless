from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Recognition:
    text: str
    confidence: float | None = None


class OcrEngine(Protocol):
    def recognize(self, image: Any, *, region_id: str) -> Recognition: ...

    def close(self) -> None: ...
