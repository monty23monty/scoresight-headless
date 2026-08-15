from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from scoresight.capture.base import FramePacket
from scoresight.core.models import NormalizedRect, Point, RegionConfig, ResultState
from scoresight.ocr.base import Recognition
from scoresight.ocr.pipeline import RecognitionPipeline
from scoresight.ocr.preprocess import transform_frame


class FakeEngine:
    def __init__(self, recognitions: list[Recognition]) -> None:
        self.recognitions = iter(recognitions)
        self.closed = False

    def recognize(self, image, *, region_id: str) -> Recognition:
        assert image.size > 0
        return next(self.recognitions)

    def close(self) -> None:
        self.closed = True


class FakeBatchEngine(FakeEngine):
    def __init__(self, recognitions: list[Recognition]) -> None:
        super().__init__(recognitions)
        self.batch_called = False

    def recognize_many(self, requests) -> list[Recognition]:
        self.batch_called = True
        return [next(self.recognitions) for _ in requests]


def frame(sequence: int = 1) -> FramePacket:
    return FramePacket(
        sequence=sequence,
        image=np.zeros((100, 200), dtype=np.uint8),
        width=200,
        height=100,
        captured_at=datetime.now(UTC),
    )


def test_pipeline_validates_and_marks_unchanged_results() -> None:
    region = RegionConfig(
        id="clock",
        name="Clock",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        format_regex=r"\d{2}:\d{2}",
    )
    engine = FakeEngine([Recognition("12:34", 0.99), Recognition("12:34", 0.99)])
    pipeline = RecognitionPipeline(engine, [region])

    first = pipeline.process(frame(1))
    second = pipeline.process(frame(2))

    assert first.sequence == 1
    assert first.fields[0].state == ResultState.OK
    assert second.fields[0].state == ResultState.UNCHANGED
    assert second.fields[0].changed_at == first.fields[0].changed_at


def test_pipeline_rejects_low_confidence_and_invalid_format() -> None:
    regions = [
        RegionConfig(
            id="a",
            name="A",
            rect=NormalizedRect(x=0, y=0, width=0.4, height=0.4),
            confidence_threshold=0.8,
        ),
        RegionConfig(
            id="b",
            name="B",
            rect=NormalizedRect(x=0.5, y=0, width=0.4, height=0.4),
            format_regex=r"\d+",
        ),
    ]
    pipeline = RecognitionPipeline(
        FakeEngine([Recognition("1", 0.2), Recognition("BAD", 0.99)]), regions
    )
    result = pipeline.process(frame())
    assert [field.state for field in result.fields] == [ResultState.REJECTED, ResultState.REJECTED]


def test_frame_crop_uses_normalized_coordinates() -> None:
    image = np.arange(100 * 200, dtype=np.uint16).reshape((100, 200))
    cropped = transform_frame(
        image,
        crop=NormalizedRect(x=0.25, y=0.2, width=0.5, height=0.5),
    )
    assert cropped.shape == (50, 100)
    assert cropped[0, 0] == image[20, 50]


def test_perspective_transform_preserves_frame_size() -> None:
    image = np.zeros((100, 200), dtype=np.uint8)
    transformed = transform_frame(
        image,
        perspective=[
            Point(x=0.05, y=0.05),
            Point(x=0.95, y=0.05),
            Point(x=0.95, y=0.95),
            Point(x=0.05, y=0.95),
        ],
    )
    assert transformed.shape == image.shape


def test_pipeline_uses_batch_engine_when_available() -> None:
    engine = FakeBatchEngine([Recognition("8", 0.99)])
    region = RegionConfig(
        id="score",
        name="Score",
        rect=NormalizedRect(x=0, y=0, width=0.2, height=0.2),
    )
    pipeline = RecognitionPipeline(engine, [region])
    assert pipeline.process(frame()).fields[0].value == "8"
    assert engine.batch_called
