from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime

from scoresight.capture.base import FramePacket
from scoresight.core.models import (
    NormalizedRect,
    Point,
    RegionConfig,
    ResultBatch,
    ResultField,
    ResultState,
)
from scoresight.ocr.base import OcrEngine
from scoresight.ocr.preprocess import crop_region, preprocess, transform_frame
from scoresight.ocr.smoothing import CharacterSmoother


class RecognitionPipeline:
    def __init__(
        self,
        engine: OcrEngine,
        regions: list[RegionConfig],
        *,
        crop: NormalizedRect | None = None,
        perspective: list[Point] | None = None,
    ) -> None:
        self.engine = engine
        self.regions = regions
        self.crop = crop
        self.perspective = perspective
        self.stream_id = str(uuid.uuid4())
        self._sequence = 0
        self._last_values: dict[str, str] = {}
        self._changed_at: dict[str, datetime] = {}
        self._smoothers = {
            region.id: CharacterSmoother(region.smoothing_window)
            for region in regions
            if region.smoothing_window > 1
        }

    def process(self, frame: FramePacket) -> ResultBatch:
        started_ns = time.perf_counter_ns()
        fields: list[ResultField] = []
        image = transform_frame(
            frame.image,
            crop=self.crop,
            perspective=self.perspective,
        )
        frame_height, frame_width = image.shape[:2]
        enabled_regions = [region for region in self.regions if region.enabled]
        prepared = []
        for region in enabled_regions:
            patch = crop_region(image, region.rect.pixels(frame_width, frame_height))
            prepared.append(preprocess(patch, region.preprocess))

        recognize_many = getattr(self.engine, "recognize_many", None)
        if callable(recognize_many):
            recognitions = recognize_many(
                [(patch, region.id) for patch, region in zip(prepared, enabled_regions)]
            )
        else:
            recognitions = [
                self.engine.recognize(patch, region_id=region.id)
                for patch, region in zip(prepared, enabled_regions)
            ]

        for region, recognition in zip(enabled_regions, recognitions):
            value = recognition.text
            if region.remove_leading_zeros and value.isdigit():
                value = value.lstrip("0") or "0"
            smoother = self._smoothers.get(region.id)
            if smoother is not None:
                value = smoother.add(value)

            if not value:
                state = ResultState.EMPTY
            elif (
                recognition.confidence is not None
                and recognition.confidence < region.confidence_threshold
            ) or re.fullmatch(region.format_regex, value) is None:
                state = ResultState.REJECTED
            elif self._last_values.get(region.id) == value:
                state = ResultState.UNCHANGED
            else:
                state = ResultState.OK

            now = datetime.now(UTC)
            if state == ResultState.OK or region.id not in self._changed_at:
                self._changed_at[region.id] = now
            if state not in {ResultState.REJECTED, ResultState.EMPTY}:
                self._last_values[region.id] = value
            fields.append(
                ResultField(
                    id=region.id,
                    name=region.name,
                    value=value,
                    state=state,
                    confidence=recognition.confidence,
                    changed_at=self._changed_at[region.id],
                )
            )

        self._sequence += 1
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        return ResultBatch(
            stream_id=self.stream_id,
            sequence=self._sequence,
            captured_at=frame.captured_at,
            latency_ms=elapsed_ms,
            fields=fields,
        )

    def close(self) -> None:
        self.engine.close()
