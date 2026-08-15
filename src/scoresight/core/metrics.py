from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

from scoresight import __version__
from scoresight.core.models import ResultBatch


class ServiceMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        ProcessCollector(namespace="scoresight", registry=self.registry)
        PlatformCollector(registry=self.registry)
        self.build = Info(
            "scoresight_build", "ScoreSight build information", registry=self.registry
        )
        self.build.info({"version": __version__})
        self.capture_frames = Counter(
            "scoresight_capture_frames",
            "Frames received from the capture source",
            registry=self.registry,
        )
        self.capture_failures = Counter(
            "scoresight_capture_failures",
            "Capture loop failures",
            registry=self.registry,
        )
        self.capture_reconnects = Counter(
            "scoresight_capture_reconnects",
            "Successful capture reconnects",
            registry=self.registry,
        )
        self.preview_frames = Counter(
            "scoresight_preview_frames",
            "Preview frames encoded",
            registry=self.registry,
        )
        self.ocr_batches = Counter(
            "scoresight_ocr_batches",
            "OCR result batches",
            registry=self.registry,
        )
        self.ocr_latency = Histogram(
            "scoresight_ocr_latency_seconds",
            "End-to-end OCR batch latency",
            buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.0, 5.0),
            registry=self.registry,
        )
        self.result_states = Counter(
            "scoresight_result_fields",
            "OCR result fields by state",
            labelnames=("state",),
            registry=self.registry,
        )
        self.last_frame_timestamp = Gauge(
            "scoresight_capture_last_frame_timestamp_seconds",
            "Unix timestamp of the last captured frame",
            registry=self.registry,
        )
        self.last_frame_age = Gauge(
            "scoresight_capture_last_frame_age_seconds",
            "Age of the latest captured frame",
            registry=self.registry,
        )
        self.websocket_clients = Gauge(
            "scoresight_websocket_clients",
            "Connected browser WebSocket clients",
            labelnames=("channel",),
            registry=self.registry,
        )
        self.output_state = Gauge(
            "scoresight_output_state",
            "Output state (1 running, 0 stopped, -1 degraded)",
            labelnames=("output_id", "kind"),
            registry=self.registry,
        )
        self.output_sent = Gauge(
            "scoresight_output_sent_total",
            "Updates sent by an output adapter",
            labelnames=("output_id", "kind"),
            registry=self.registry,
        )
        self.output_failed = Gauge(
            "scoresight_output_failed_total",
            "Failed updates for an output adapter",
            labelnames=("output_id", "kind"),
            registry=self.registry,
        )
        self.output_last_ack_age = Gauge(
            "scoresight_output_last_ack_age_seconds",
            "Age of the latest acknowledged output update, or -1",
            labelnames=("output_id", "kind"),
            registry=self.registry,
        )
        self.output_reconnects = Gauge(
            "scoresight_output_reconnects_total",
            "Reconnects completed by a persistent output adapter",
            labelnames=("output_id", "kind"),
            registry=self.registry,
        )
        self.output_connected = Gauge(
            "scoresight_output_connected",
            "Whether a persistent output adapter currently has a connection",
            labelnames=("output_id", "kind"),
            registry=self.registry,
        )
        self.last_frame_monotonic: float | None = None
        self.last_ocr_monotonic: float | None = None

    def record_capture(self) -> None:
        self.capture_frames.inc()
        self.last_frame_monotonic = time.monotonic()
        self.last_frame_timestamp.set(time.time())

    def record_result(self, result: ResultBatch) -> None:
        self.ocr_batches.inc()
        self.ocr_latency.observe(result.latency_ms / 1000.0)
        self.last_ocr_monotonic = time.monotonic()
        for field in result.fields:
            self.result_states.labels(state=field.state.value).inc()

    def set_websocket(self, channel: str, delta: int) -> None:
        self.websocket_clients.labels(channel=channel).inc(delta)

    def render(self, adapters: Mapping[str, Any]) -> bytes:
        now = time.monotonic()
        age = -1.0 if self.last_frame_monotonic is None else now - self.last_frame_monotonic
        self.last_frame_age.set(age)
        state_values = {"running": 1, "stopped": 0, "degraded": -1}
        for adapter_id, adapter in adapters.items():
            kind = getattr(adapter, "kind", type(adapter).__name__)
            labels = {"output_id": adapter_id, "kind": kind}
            self.output_state.labels(**labels).set(state_values.get(adapter.status.state, -1))
            self.output_sent.labels(**labels).set(adapter.status.sent)
            self.output_failed.labels(**labels).set(adapter.status.failed)
            last_ack = getattr(adapter, "last_ack_monotonic", None)
            self.output_last_ack_age.labels(**labels).set(
                -1.0 if last_ack is None else max(0.0, now - last_ack)
            )
            connections = int(getattr(adapter, "connections", 0))
            self.output_reconnects.labels(**labels).set(max(0, connections - 1))
            connection = getattr(adapter, "_connection", None)
            self.output_connected.labels(**labels).set(
                1 if connection is not None and connection.close_code is None else 0
            )
        return generate_latest(self.registry)
