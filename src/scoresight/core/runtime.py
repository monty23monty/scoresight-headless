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
from scoresight.core.secrets import read_secret_file
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
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=30.0)
            except TimeoutError:
                logger.warning("runtime did not stop within 30 seconds; cancelling")
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
                await asyncio.to_thread(source.open)
                if failure_count:
                    self.service.metrics.capture_reconnects.inc()
                ocr_error = ""
                if config.regions:
                    try:
                        pipeline = self._build_pipeline(config)
                    except Exception as exc:
                        ocr_error = f"{type(exc).__name__}: {exc}"
                        logger.error("OCR unavailable; preview will continue: %s", ocr_error)
                failure_count = 0
                last_error = ""
                await self.service.publish_health(
                    HealthSnapshot(
                        status="degraded" if ocr_error else "ok",
                        capture=HealthComponent(message="capture connected"),
                        ocr=HealthComponent(
                            status="degraded" if ocr_error else "ok",
                            message=ocr_error
                            or (
                                "OCR ready"
                                if config.regions
                                else "waiting for regions to be configured"
                            ),
                        ),
                    )
                )
                await self._capture_loop(config, source, pipeline)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure_count += 1
                self.service.metrics.capture_failures.inc()
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
        self,
        config: ServiceConfig,
        source: Any,
        pipeline: RecognitionPipeline | None,
    ) -> None:
        ocr_period = 1.0 / config.ocr.target_hz
        preview_period = 0.2
        next_ocr = 0.0
        next_preview = 0.0
        while not self._stop.is_set():
            frame = await asyncio.to_thread(source.read_latest, 1.0)
            if frame is None:
                raise RuntimeError("capture signal lost")
            self.service.metrics.record_capture()
            now = time.perf_counter()
            if now >= next_preview:
                preview = await asyncio.to_thread(self._encode_preview, frame, config)
                await self.service.publish_preview(preview)
                self.service.metrics.preview_frames.inc()
                next_preview = now + preview_period
            if now < next_ocr or not config.regions or pipeline is None:
                continue
            result = await asyncio.to_thread(pipeline.process, frame)
            self.service.publish_region_previews(pipeline.latest_previews)
            await self.service.publish_result(result)
            self.service.metrics.record_result(result)
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
            uri = read_secret_file(source.uri_file) if source.uri_file else source.uri
            if not uri:
                raise ValueError(f"{source.kind} source requires a URI")
            return OpenCVCapture(
                uri,
                pace=source.kind == "file",
                open_timeout=source.open_timeout_seconds,
                read_timeout=source.read_timeout_seconds,
            )
        raise ValueError(f"unsupported source kind: {source.kind}")

    @staticmethod
    def _build_pipeline(config: ServiceConfig) -> RecognitionPipeline:
        env_path = os.getenv("SCORESIGHT_TESSDATA")
        repository_path = Path(__file__).resolve().parents[3] / "tesseract" / "tessdata"
        tessdata_path = Path(env_path) if env_path else repository_path
        field_whitelists = {
            "number": "0123456789",
            "time": "0123456789:.",
        }
        character_whitelists = {
            region.id: field_whitelists[region.field_type]
            for region in config.regions
            if region.field_type in field_whitelists
        }
        engine: OcrEngine
        if config.ocr.workers > 1:
            engine = PooledTesseractEngine(
                config.ocr.model,
                workers=config.ocr.workers,
                tessdata_path=tessdata_path,
                character_whitelists=character_whitelists,
            )
        else:
            engine = TesseractEngine(
                config.ocr.model,
                tessdata_path=tessdata_path,
                character_whitelists=character_whitelists,
            )
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
