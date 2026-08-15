from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]


class ResultState(StrEnum):
    OK = "ok"
    UNCHANGED = "unchanged"
    PENDING = "pending"
    REJECTED = "rejected"
    EMPTY = "empty"
    STALE = "stale"


class NormalizedRect(BaseModel):
    x: UnitFloat
    y: UnitFloat
    width: UnitFloat
    height: UnitFloat

    @model_validator(mode="after")
    def must_fit_frame(self) -> NormalizedRect:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width and height must be greater than zero")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("rectangle must fit within the normalized frame")
        return self

    def pixels(self, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        x1 = round(self.x * frame_width)
        y1 = round(self.y * frame_height)
        x2 = round((self.x + self.width) * frame_width)
        y2 = round((self.y + self.height) * frame_height)
        return x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)


class Point(BaseModel):
    x: UnitFloat
    y: UnitFloat


class PreprocessConfig(BaseModel):
    threshold_method: Literal["otsu", "adaptive", "none"] = "otsu"
    invert: bool = False
    dilate_iterations: int = Field(default=0, ge=0, le=10)
    vertical_scale: float = Field(default=1.0, ge=0.1, le=3.0)
    autocrop: bool = False
    skip_similar: bool = True
    similarity_threshold: float = Field(default=0.02, ge=0.0, le=1.0)


class RegionConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=100)
    rect: NormalizedRect
    enabled: bool = True
    field_type: Literal["number", "time", "text"] = "text"
    format_regex: str = r"^.*$"
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    confirmation_frames: int = Field(default=2, ge=1, le=15)
    smoothing_window: int = Field(default=1, ge=1, le=15)
    remove_leading_zeros: bool = False
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)

    @field_validator("format_regex")
    @classmethod
    def valid_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value


class SourceConfig(BaseModel):
    kind: Literal["decklink", "opencv", "rtsp", "file", "mock"] = "decklink"
    device_id: str = "0"
    mode: str = "1080p30"
    uri: str | None = None
    uri_file: str | None = None
    reconnect_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    open_timeout_seconds: float = Field(default=5.0, ge=0.5, le=60.0)
    read_timeout_seconds: float = Field(default=2.0, ge=0.25, le=30.0)
    stale_after_seconds: float = Field(default=5.0, ge=1.0, le=120.0)

    @model_validator(mode="after")
    def network_and_file_sources_have_a_location(self) -> SourceConfig:
        if self.kind in {"rtsp", "file"} and not (self.uri or self.uri_file):
            raise ValueError(f"{self.kind} source requires uri or uri_file")
        return self


class OcrConfig(BaseModel):
    engine: Literal["tesseract"] = "tesseract"
    model: str = "scoreboard_general"
    language: str = "eng"
    target_hz: float = Field(default=10.0, ge=1.0, le=30.0)
    workers: int = Field(default=2, ge=1, le=4)


class SecurityConfig(BaseModel):
    admin_token: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=16)
    read_tokens: list[str] = Field(default_factory=list)
    allow_lan: bool = False


class OutputConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: Literal["vmix", "uno", "webhook", "file", "fan_site"]
    enabled: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    send_unchanged: bool = False

    @model_validator(mode="after")
    def required_settings_for_kind(self) -> OutputConfig:
        required: dict[str, tuple[str, ...]] = {
            "uno": ("endpoint",),
            "webhook": ("url",),
            "file": ("path",),
            "fan_site": ("endpoint", "stream_id"),
        }
        for key in required.get(self.kind, ()):
            if not self.settings.get(key):
                raise ValueError(f"{self.kind} output requires settings.{key}")
        if self.kind == "fan_site" and not (
            self.settings.get("token") or self.settings.get("token_file")
        ):
            raise ValueError("fan_site output requires settings.token or settings.token_file")
        return self


class ServiceConfig(BaseModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    source: SourceConfig = Field(default_factory=SourceConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    regions: list[RegionConfig] = Field(default_factory=list)
    crop: NormalizedRect | None = None
    perspective: list[Point] | None = None
    outputs: list[OutputConfig] = Field(default_factory=list)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @field_validator("perspective")
    @classmethod
    def four_perspective_points(cls, value: list[Point] | None) -> list[Point] | None:
        if value is not None and len(value) != 4:
            raise ValueError("perspective must contain exactly four points")
        return value


class ResultField(BaseModel):
    id: str
    name: str
    # The stable value most recently accepted by validation and confidence filters.
    value: str
    # The value observed in the current frame, including rejected/empty candidates.
    candidate_value: str = ""
    state: ResultState
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResultBatch(BaseModel):
    type: Literal["result.batch"] = "result.batch"
    schema_version: Literal[1] = 1
    stream_id: str
    sequence: int = Field(ge=0)
    captured_at: datetime
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float = Field(ge=0.0)
    fields: list[ResultField]


class HealthComponent(BaseModel):
    status: Literal["ok", "degraded", "down"] = "ok"
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class HealthSnapshot(BaseModel):
    type: Literal["health"] = "health"
    status: Literal["ok", "degraded", "down"] = "ok"
    capture: HealthComponent = Field(default_factory=HealthComponent)
    ocr: HealthComponent = Field(default_factory=HealthComponent)
    outputs: dict[str, HealthComponent] = Field(default_factory=dict)
