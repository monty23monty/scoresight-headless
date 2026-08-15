from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import numpy as np
import pytest

from scoresight.capture.base import FramePacket
from scoresight.core.config import ConfigStore
from scoresight.core.models import NormalizedRect, RegionConfig, ServiceConfig, SourceConfig
from scoresight.core.runtime import RuntimeController
from scoresight.core.service import ScoreSightService


class RepeatingCapture:
    def __init__(self) -> None:
        self.sequence = 0
        self.closed = False

    def open(self) -> None:
        pass

    def read_latest(self, timeout: float = 1.0) -> FramePacket:
        self.sequence += 1
        return FramePacket(
            sequence=self.sequence,
            image=np.zeros((24, 32, 3), dtype=np.uint8),
            width=32,
            height=24,
            captured_at=datetime.now(UTC),
        )

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_preview_continues_when_ocr_dependency_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    store = ConfigStore(tmp_path / "config.json")
    current = store.load()
    configured = current.model_copy(
        update={
            "source": SourceConfig(kind="mock"),
            "regions": [
                RegionConfig(
                    id="clock",
                    name="Clock",
                    rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
                )
            ],
        }
    )
    store.replace(configured, current.revision)
    service = ScoreSightService(store)
    controller = RuntimeController(service, store)
    capture = RepeatingCapture()
    monkeypatch.setattr(controller, "_build_source", lambda config: capture)

    def unavailable_ocr(config: ServiceConfig):
        raise ModuleNotFoundError("No module named 'tesserocr'")

    monkeypatch.setattr(controller, "_build_pipeline", unavailable_ocr)

    async with service.preview_frames.subscribe() as queue:
        await controller.start()
        try:
            preview = await asyncio.wait_for(queue.get(), timeout=1.0)
        finally:
            await controller.stop()

    assert preview.width == 32
    assert preview.height == 24
    assert service.latest_preview == preview
    assert service.health.capture.status == "ok"
    assert service.health.ocr.status == "degraded"
    assert "tesserocr" in service.health.ocr.message
    assert capture.closed


def test_file_source_enables_real_time_pacing() -> None:
    config = ServiceConfig(source=SourceConfig(kind="file", uri="C:/video.mp4"))
    source = RuntimeController._build_source(config)
    assert source.source == "C:/video.mp4"
    assert source.pace is True


def test_network_source_reads_uri_from_secret_file(tmp_path) -> None:
    uri_file = tmp_path / "rtsp-uri"
    uri_file.write_text("rtsp://media/scoreboard\n", encoding="utf-8")
    config = ServiceConfig(source=SourceConfig(kind="rtsp", uri_file=str(uri_file)))
    source = RuntimeController._build_source(config)
    assert source.source == "rtsp://media/scoreboard"
    assert source.open_timeout == 5.0
    assert source.read_timeout == 2.0


@pytest.mark.asyncio
async def test_runtime_reconnects_after_capture_open_failure(tmp_path, monkeypatch) -> None:
    store = ConfigStore(tmp_path / "config.json")
    current = store.load()
    store.replace(
        current.model_copy(update={"source": SourceConfig(kind="mock", reconnect_seconds=0.1)}),
        current.revision,
    )
    service = ScoreSightService(store)
    controller = RuntimeController(service, store)
    working = RepeatingCapture()

    class BrokenCapture:
        def open(self):
            raise RuntimeError("device unavailable")

        def close(self):
            pass

    captures = iter([BrokenCapture(), working])
    monkeypatch.setattr(controller, "_build_source", lambda config: next(captures))
    async with service.preview_frames.subscribe() as queue:
        await controller.start()
        try:
            await asyncio.wait_for(queue.get(), timeout=1)
        finally:
            await controller.stop()
    assert service.metrics.capture_failures._value.get() == 1
    assert service.metrics.capture_reconnects._value.get() == 1
