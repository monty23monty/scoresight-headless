from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from scoresight.core.events import LatestValueBus
from scoresight.core.models import ResultBatch

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OutputStatus:
    state: Literal["stopped", "running", "degraded"] = "stopped"
    message: str = ""
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    consecutive_failures: int = 0


class OutputAdapter(ABC):
    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id
        self.kind = type(self).__name__
        self.status = OutputStatus()
        self._retry_after = 0.0

    async def run(self, bus: LatestValueBus[ResultBatch]) -> None:
        self.status.state = "running"
        async with bus.subscribe() as queue:
            while True:
                batch = await queue.get()
                if self._retry_after > time.monotonic():
                    self.status.skipped += 1
                    continue
                try:
                    await self.send(batch)
                    self.status.sent += 1
                    self.status.consecutive_failures = 0
                    self._retry_after = 0.0
                    self.status.state = "running"
                    self.status.message = ""
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.status.failed += 1
                    self.status.consecutive_failures += 1
                    delay = min(30.0, 0.25 * 2 ** (self.status.consecutive_failures - 1))
                    self._retry_after = time.monotonic() + delay
                    self.status.state = "degraded"
                    self.status.message = str(exc)
                    logger.exception("output adapter %s failed", self.adapter_id)

    @abstractmethod
    async def send(self, batch: ResultBatch) -> None: ...

    async def close(self) -> None:
        self.status.state = "stopped"

    def details(self) -> dict[str, Any]:
        return {}
