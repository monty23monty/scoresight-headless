from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from scoresight.capture.decklink import DeckLinkCapture
from scoresight.capture.mock import MockCapture
from scoresight.capture.opencv import OpenCVCapture
from scoresight.core.config import ConfigStore
from scoresight.core.models import HealthComponent, HealthSnapshot, ServiceConfig
from scoresight.core.service import PreviewFrame, ScoreSightService
from scoresight.ocr.base import OcrEngine
from scoresight.ocr.pipeline import RecognitionPipeline
from scoresight.ocr.preprocess import transform_frame
from scoresight.ocr.tesseract_engine import PooledTesseractEngine, TesseractEngine

logger = logging.getLogger(__name__)


class RuntimeController:
    def __init__(self, service: ScoreSightService, store: ConfigStore) -> None:
        self.service = service
        self.store = store
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run_forever(), name="scoresight-runtime")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _run_forever(self) -> None:
        failure_count = 0
        last_error = ""
        while not self._stop.is_set():
            config = self.store.load()
            source = None
            pipeline = None
            try:
                source = self._build_source(config)
                pipeline = self._build_pipeline(config)
                await asyncio.to_thread(source.open)
                failure_count = 0
                last_error = ""
                await self.service.publish_health(
                    HealthSnapshot(capture=HealthComponent(message="capture connected"))
                )
                await self._capture_loop(config, source, pipeline)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_count += 1
                error = f"{type(exc).__name__}: {exc}"
                if error != last_error:
                    logger.error("runtime unavailable: %s", error)
                    last_error = error
                await self.service.publish_health(
                    HealthSnapshot(
                        status="degraded",
                        capture=HealthComponent(status="down", message=str(exc)),
                        ocr=HealthComponent(status="degraded", message="waiting for capture"),
                    )
                )
                with suppress(TimeoutError):
                    delay = min(
                        30.0,
                        config.source.reconnect_seconds * 2 ** min(failure_count - 1, 5),
                    )
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=delay
                    )
            finally:
                if source is not None:
                    await asyncio.to_thread(source.close)
                if pipeline is not None:
                    await asyncio.to_thread(pipeline.close)

    async def _capture_loop(
        self, config: ServiceConfig, source: Any, pipeline: RecognitionPipeline
    ) -> None:
        ocr_period = 1.0 / config.ocr.target_hz
        preview_period = 0.2
        next_ocr = 0.0
        next_preview = 0.0
        while not self._stop.is_set():
            frame = await asyncio.to_thread(source.read_latest, 1.0)
            if frame is None:
                raise RuntimeError("capture signal lost")
            self.service.metrics.capture_frames += 1
            now = time.perf_counter()
            if now >= next_preview:
                preview = await asyncio.to_thread(self._encode_preview, frame, config)
                await self.service.publish_preview(preview)
                self.service.metrics.preview_frames += 1
                next_preview = now + preview_period
            if now < next_ocr or not config.regions:
                continue
            result = await asyncio.to_thread(pipeline.process, frame)
            await self.service.publish_result(result)
            self.service.metrics.ocr_batches += 1
            self.service.metrics.last_ocr_latency_ms = result.latency_ms
            next_ocr = now + ocr_period

    @staticmethod
    def _build_source(config: ServiceConfig) -> Any:
        source = config.source
        if source.kind == "decklink":
            return DeckLinkCapture(source.device_id, source.mode)
        if source.kind == "mock":
            return MockCapture()
        if source.kind == "opencv":
            device: str | int = (
                int(source.device_id) if source.device_id.isdigit() else source.device_id
            )
            return OpenCVCapture(device)
        if source.kind in {"rtsp", "file"}:
            if not source.uri:
                raise ValueError(f"{source.kind} source requires a URI")
            return OpenCVCapture(source.uri)
        raise ValueError(f"unsupported source kind: {source.kind}")

    @staticmethod
    def _build_pipeline(config: ServiceConfig) -> RecognitionPipeline:
        env_path = os.getenv("SCORESIGHT_TESSDATA")
        repository_path = Path(__file__).resolve().parents[3] / "tesseract" / "tessdata"
        tessdata_path = Path(env_path) if env_path else repository_path
        engine: OcrEngine
        if config.ocr.workers > 1:
            engine = PooledTesseractEngine(
                config.ocr.model,
                workers=config.ocr.workers,
                tessdata_path=tessdata_path,
            )
        else:
            engine = TesseractEngine(config.ocr.model, tessdata_path=tessdata_path)
        return RecognitionPipeline(
            engine,
            config.regions,
            crop=config.crop,
            perspective=config.perspective,
        )

    @staticmethod
    def _encode_preview(frame: Any, config: ServiceConfig) -> PreviewFrame:
        import cv2

        image = transform_frame(
            frame.image,
            crop=config.crop,
            perspective=config.perspective,
        )
        height, width = image.shape[:2]
        target_width = min(960, width)
        target_height = max(1, round(height * target_width / width))
        if target_width != width:
            image = cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise RuntimeError("failed to encode preview frame")
        return PreviewFrame(
            jpeg=encoded.tobytes(),
            width=target_width,
            height=target_height,
            sequence=frame.sequence,
        )
