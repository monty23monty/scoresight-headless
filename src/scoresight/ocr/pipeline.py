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
        self.latest_previews: dict[str, bytes] = {}
        self._pending_values: dict[str, tuple[str, int]] = {}
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

        self.latest_previews = {}
        if prepared:
            import cv2

            for region, patch in zip(enabled_regions, prepared, strict=True):
                encoded_ok, encoded = cv2.imencode(".png", patch)
                if encoded_ok:
                    self.latest_previews[region.id] = encoded.tobytes()

        recognize_many = getattr(self.engine, "recognize_many", None)
        if callable(recognize_many):
            recognitions = recognize_many(
                [
                    (patch, region.id)
                    for patch, region in zip(prepared, enabled_regions, strict=True)
                ]
            )
        else:
            recognitions = [
                self.engine.recognize(patch, region_id=region.id)
                for patch, region in zip(prepared, enabled_regions, strict=True)
            ]

        for region, recognition in zip(enabled_regions, recognitions, strict=True):
            value = self._normalize_candidate(recognition.text, region.field_type)
            if region.remove_leading_zeros and value.isdigit():
                value = value.lstrip("0") or "0"
            smoother = self._smoothers.get(region.id)
            if smoother is not None:
                value = smoother.add(value)

            candidate_value = value
            if not candidate_value:
                state = ResultState.EMPTY
            elif (
                (
                    recognition.confidence is not None
                    and recognition.confidence < region.confidence_threshold
                )
                or not self._valid_field_type(candidate_value, region.field_type)
                or re.fullmatch(region.format_regex, candidate_value) is None
            ):
                state = ResultState.REJECTED
            elif self._last_values.get(region.id) == candidate_value:
                state = ResultState.UNCHANGED
            else:
                pending_value, pending_count = self._pending_values.get(
                    region.id, ("", 0)
                )
                pending_count = (
                    pending_count + 1 if pending_value == candidate_value else 1
                )
                self._pending_values[region.id] = (candidate_value, pending_count)
                state = (
                    ResultState.OK
                    if pending_count >= region.confirmation_frames
                    else ResultState.PENDING
                )

            if state in {ResultState.EMPTY, ResultState.REJECTED}:
                self._pending_values.pop(region.id, None)

            now = datetime.now(UTC)
            if state == ResultState.OK:
                self._changed_at[region.id] = now
                self._pending_values.pop(region.id, None)
                self._last_values[region.id] = candidate_value
            elif state == ResultState.UNCHANGED:
                self._pending_values.pop(region.id, None)
            accepted_value = self._last_values.get(region.id, "")
            changed_at = self._changed_at.setdefault(region.id, now)
            fields.append(
                ResultField(
                    id=region.id,
                    name=region.name,
                    value=accepted_value,
                    candidate_value=candidate_value,
                    state=state,
                    confidence=recognition.confidence,
                    changed_at=changed_at,
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

    @staticmethod
    def _normalize_candidate(value: str, field_type: str) -> str:
        value = value.strip()
        if field_type == "time":
            value = re.sub(r"\s+", "", value).replace(".", ":").replace(",", ":")
            if value.isdigit() and 3 <= len(value) <= 4:
                value = f"{value[:-2]}:{value[-2:]}"
        elif field_type == "number":
            value = re.sub(r"\s+", "", value)
        return value

    @staticmethod
    def _valid_field_type(value: str, field_type: str) -> bool:
        if field_type == "number":
            return re.fullmatch(r"\d+", value) is not None
        if field_type == "time":
            return re.fullmatch(r"\d{1,3}:[0-5]\d", value) is not None
        return True
