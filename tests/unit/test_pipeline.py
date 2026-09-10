from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

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


@pytest.mark.parametrize("field_type,value", [("number", "27"), ("time", "1:49"), ("text", "A")])
@pytest.mark.parametrize("smoothing_window", [1, 5])
def test_confirmed_blank_clears_value_and_smoothing(
    field_type, value, smoothing_window, monkeypatch
):
    class Clock:
        tick = datetime(2026, 1, 1, tzinfo=UTC)

        @classmethod
        def now(cls, tz):
            cls.tick += timedelta(seconds=1)
            return cls.tick

    monkeypatch.setattr("scoresight.ocr.pipeline.datetime", Clock)
    region = RegionConfig(
        id="field",
        name="Field",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        field_type=field_type,
        format_regex=r".+",
        confirmation_frames=2,
        smoothing_window=smoothing_window,
    )
    pipeline = RecognitionPipeline(
        FakeEngine(
            [
                Recognition(value, 0.99),
                Recognition(value, 0.99),
                Recognition("", 0),
                Recognition(value, 0.99),
                Recognition(" ", 0),
                Recognition("", 0),
                Recognition("", 0),
                Recognition(value, 0.99),
                Recognition(value, 0.99),
            ]
        ),
        [region],
    )
    results = [pipeline.process(frame(i)).fields[0] for i in range(9)]
    assert results[1].value == value
    assert results[2].state == ResultState.PENDING
    assert results[2].candidate_value == ""
    assert results[2].value == value
    assert results[3].state == ResultState.UNCHANGED
    assert results[4].state == ResultState.PENDING
    assert results[5].state == ResultState.OK
    assert results[5].value == ""
    assert results[5].changed_at > results[1].changed_at
    assert results[6].state == ResultState.EMPTY
    assert results[6].value == ""
    assert results[6].changed_at == results[5].changed_at
    assert results[7].state == ResultState.PENDING
    assert results[7].value == ""
    assert results[8].value == value


def test_blank_initial_read_is_confirmed_without_turning_into_zero():
    region = RegionConfig(
        name="Score",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        field_type="number",
        remove_leading_zeros=True,
        confirmation_frames=1,
    )
    pipeline = RecognitionPipeline(
        FakeEngine(
            [
                Recognition("", 0),
                Recognition("0", 0.99),
                Recognition("", 0),
            ]
        ),
        [region],
    )
    results = [pipeline.process(frame(i)).fields[0] for i in range(3)]
    assert [result.value for result in results] == ["", "0", ""]
    assert all(result.state == ResultState.OK for result in results)


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
        confirmation_frames=1,
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
            confirmation_frames=1,
        ),
        RegionConfig(
            id="b",
            name="B",
            rect=NormalizedRect(x=0.5, y=0, width=0.4, height=0.4),
            format_regex=r"\d+",
            confirmation_frames=1,
        ),
    ]
    pipeline = RecognitionPipeline(
        FakeEngine([Recognition("1", 0.2), Recognition("BAD", 0.99)]), regions
    )
    result = pipeline.process(frame())
    assert [field.state for field in result.fields] == [ResultState.REJECTED, ResultState.REJECTED]


def test_rejected_candidate_does_not_replace_last_accepted_value() -> None:
    region = RegionConfig(
        id="score",
        name="Score",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        format_regex=r"\d+",
        confidence_threshold=0.8,
        confirmation_frames=1,
    )
    pipeline = RecognitionPipeline(
        FakeEngine([Recognition("42", 0.99), Recognition("noise", 0.2)]), [region]
    )

    accepted = pipeline.process(frame(1)).fields[0]
    rejected = pipeline.process(frame(2)).fields[0]

    assert accepted.value == "42"
    assert accepted.candidate_value == "42"
    assert rejected.state == ResultState.REJECTED
    assert rejected.value == "42"
    assert rejected.candidate_value == "noise"
    assert rejected.changed_at == accepted.changed_at


def test_pipeline_exposes_exact_filtered_ocr_input_as_png() -> None:
    region = RegionConfig(
        id="clock",
        name="Clock",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        confirmation_frames=1,
    )
    pipeline = RecognitionPipeline(FakeEngine([Recognition("1:23", 0.99)]), [region])
    pipeline.process(frame())
    assert pipeline.latest_previews["clock"].startswith(b"\x89PNG\r\n\x1a\n")


def test_frame_crop_uses_normalized_coordinates() -> None:
    image = np.arange(100 * 200, dtype=np.uint16).reshape((100, 200))
    cropped = transform_frame(
        image,
        crop=NormalizedRect(x=0.25, y=0.2, width=0.5, height=0.5),
    )
    assert cropped.shape == (50, 100)
    assert cropped[0, 0] == image[20, 50]


def test_autocrop_filter_removes_uniform_border() -> None:
    from scoresight.core.models import PreprocessConfig
    from scoresight.ocr.preprocess import preprocess

    image = np.zeros((20, 30), dtype=np.uint8)
    image[5:15, 8:22] = 255
    filtered = preprocess(
        image,
        PreprocessConfig(threshold_method="none", autocrop=True),
    )
    assert filtered.shape == (12, 16)


def test_perspective_transform_preserves_selected_quadrilateral_aspect() -> None:
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
    assert transformed.shape == (90, 180)


def test_crop_after_perspective_uses_rectified_dimensions() -> None:
    image = np.zeros((100, 200), dtype=np.uint8)
    transformed = transform_frame(
        image,
        perspective=[
            Point(x=0.05, y=0.05),
            Point(x=0.95, y=0.05),
            Point(x=0.95, y=0.95),
            Point(x=0.05, y=0.95),
        ],
        crop=NormalizedRect(x=0.25, y=0.25, width=0.5, height=0.5),
    )
    assert transformed.shape == (46, 90)


def test_pipeline_requires_consecutive_confirmations() -> None:
    region = RegionConfig(
        id="clock",
        name="Clock",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        field_type="time",
        confirmation_frames=2,
    )
    engine = FakeEngine(
        [
            Recognition("12.34", 0.99),
            Recognition("junk", 0.99),
            Recognition("1234", 0.99),
            Recognition("12:34", 0.99),
        ]
    )
    pipeline = RecognitionPipeline(engine, [region])

    first = pipeline.process(frame(1)).fields[0]
    rejected = pipeline.process(frame(2)).fields[0]
    restarted = pipeline.process(frame(3)).fields[0]
    accepted = pipeline.process(frame(4)).fields[0]

    assert first.state == ResultState.PENDING
    assert first.candidate_value == "12:34"
    assert first.value == ""
    assert rejected.state == ResultState.REJECTED
    assert restarted.state == ResultState.PENDING
    assert accepted.state == ResultState.OK
    assert accepted.value == "12:34"


def test_time_field_rejects_impossible_seconds() -> None:
    region = RegionConfig(
        id="clock",
        name="Clock",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        field_type="time",
        confirmation_frames=1,
    )
    pipeline = RecognitionPipeline(FakeEngine([Recognition("12:79", 0.99)]), [region])

    result = pipeline.process(frame()).fields[0]

    assert result.state == ResultState.REJECTED
    assert result.value == ""


def test_pipeline_uses_batch_engine_when_available() -> None:
    engine = FakeBatchEngine([Recognition("8", 0.99)])
    region = RegionConfig(
        id="score",
        name="Score",
        rect=NormalizedRect(x=0, y=0, width=0.2, height=0.2),
        confirmation_frames=1,
    )
    pipeline = RecognitionPipeline(engine, [region])
    assert pipeline.process(frame()).fields[0].value == "8"
    assert engine.batch_called


@pytest.mark.parametrize("smoothing_window", [1, 3])
def test_clock_switches_between_minutes_and_tenths(smoothing_window: int) -> None:
    region = RegionConfig(
        id="clock",
        name="Clock",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        field_type="time",
        confirmation_frames=2,
        smoothing_window=smoothing_window,
    )
    values = ["01:00", "59.9", "09.9", "9.8", "0.0", "10:00"]
    pipeline = RecognitionPipeline(
        FakeEngine([Recognition(value, 0.99) for value in values for _ in range(4)]),
        [region],
    )

    for value in values:
        results = [pipeline.process(frame()).fields[0] for _ in range(4)]
        assert all(result.state != ResultState.REJECTED for result in results)
        assert results[-1].value == value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("59.9", "59.9"),
        (" 5 9 . 8 ", "59.8"),
        ("09.9", "09.9"),
        ("9.8", "9.8"),
        ("00.0", "00.0"),
        ("0.0", "0.0"),
        ("59,9", "59.9"),
        ("12.34", "12:34"),
        ("12,34", "12:34"),
        ("1234", "12:34"),
    ],
)
def test_clock_preserves_tenths_and_normalizes_minutes(text: str, expected: str) -> None:
    region = RegionConfig(
        name="Clock",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        field_type="time",
        confirmation_frames=1,
    )
    pipeline = RecognitionPipeline(FakeEngine([Recognition(text, 0.99)]), [region])
    result = pipeline.process(frame()).fields[0]
    assert result.state == ResultState.OK
    assert result.value == expected


@pytest.mark.parametrize("text", ["60.0", "99.9", "123.4", "59.", ".9", "59:9", "-1.0"])
def test_clock_rejects_invalid_tenths_without_replacing_accepted_value(text: str) -> None:
    region = RegionConfig(
        name="Clock",
        rect=NormalizedRect(x=0, y=0, width=0.5, height=0.5),
        field_type="time",
        confirmation_frames=1,
    )
    pipeline = RecognitionPipeline(
        FakeEngine([Recognition("01:00", 0.99), Recognition(text, 0.99)]), [region]
    )
    pipeline.process(frame())
    result = pipeline.process(frame()).fields[0]
    assert result.state == ResultState.REJECTED
    assert result.value == "01:00"
