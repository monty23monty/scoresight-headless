from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from scoresight.core.config import ConfigStore
from scoresight.core.events import LatestValueBus
from scoresight.core.metrics import ServiceMetrics
from scoresight.core.models import HealthSnapshot, ResultBatch


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    jpeg: bytes
    width: int
    height: int
    sequence: int


class ScoreSightService:
    def __init__(self, config_store: ConfigStore) -> None:
        self.config_store = config_store
        self.results = LatestValueBus[ResultBatch]()
        self.health_events = LatestValueBus[HealthSnapshot]()
        self.preview_frames = LatestValueBus[PreviewFrame]()
        self.latest_result: ResultBatch | None = None
        self.latest_preview: PreviewFrame | None = None
        self.latest_region_previews: dict[str, bytes] = {}
        self.health = HealthSnapshot()
        self.metrics = ServiceMetrics()
        self.started_at = datetime.now(UTC)
        self._output_tasks: list[asyncio.Task[None]] = []

    async def publish_result(self, result: ResultBatch) -> None:
        self.latest_result = result
        await self.results.publish(result)

    async def publish_health(self, health: HealthSnapshot) -> None:
        self.health = health
        await self.health_events.publish(health)

    async def publish_preview(self, preview: PreviewFrame) -> None:
        self.latest_preview = preview
        await self.preview_frames.publish(preview)

    def publish_region_previews(self, previews: dict[str, bytes]) -> None:
        self.latest_region_previews = dict(previews)

    async def stop(self) -> None:
        for task in self._output_tasks:
            task.cancel()
        if self._output_tasks:
            await asyncio.gather(*self._output_tasks, return_exceptions=True)
        self._output_tasks.clear()
