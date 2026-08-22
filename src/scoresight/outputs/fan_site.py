from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from scoresight.core.models import ResultBatch
from scoresight.outputs.base import OutputAdapter


class FanSiteProtocolError(RuntimeError):
    """The fan site rejected or did not understand an OCR ingest message."""


class FanSiteWebSocketOutput(OutputAdapter):
    def __init__(
        self,
        adapter_id: str,
        endpoint: str,
        stream_id: str,
        token: str,
        field_mapping: dict[str, str],
        *,
        origin: str | None = None,
        send_unchanged: bool = False,
        timeout: float = 5.0,
    ) -> None:
        super().__init__(adapter_id)
        self.stream_id = stream_id.strip()
        self.token = token
        self.field_mapping = field_mapping
        self.origin = Origin(origin) if origin is not None else None
        self.send_unchanged = send_unchanged
        self.timeout = timeout
        self.url = self._build_url(endpoint, self.stream_id)
        self._connection: ClientConnection | None = None
        self._last_sent_values: dict[str, str] | None = None
        self.last_ack: dict[str, Any] = {}
        self.last_ack_monotonic: float | None = None
        self.connections = 0
        self.kind = "fan_site"

    @staticmethod
    def _build_url(endpoint: str, stream_id: str) -> str:
        endpoint = endpoint.strip().rstrip("/")
        encoded_stream = quote(stream_id, safe="")
        if endpoint.endswith(f"/{encoded_stream}"):
            return f"{endpoint}/"
        return f"{endpoint}/{encoded_stream}/"

    def _values(self, batch: ResultBatch) -> dict[str, str]:
        values: dict[str, str] = {}
        for field in batch.fields:
            if not field.value:
                continue
            target_name = self.field_mapping.get(field.id)
            if target_name is None:
                target_name = self.field_mapping.get(field.name, field.name)
            target_name = target_name.strip()
            if target_name:
                values[target_name] = field.value.strip()
        return values

    async def _receive_object(self) -> dict[str, Any]:
        if self._connection is None:
            raise FanSiteProtocolError("fan-site WebSocket is not connected")
        raw = await asyncio.wait_for(self._connection.recv(), timeout=self.timeout)
        if isinstance(raw, bytes):
            raise FanSiteProtocolError(
                "fan site returned an unsupported binary message"
            )
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FanSiteProtocolError("fan site returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise FanSiteProtocolError("fan site returned an invalid message")
        if response.get("type") == "error":
            error = str(response.get("error", "unknown_error"))
            raise FanSiteProtocolError(f"fan site rejected the message: {error}")
        return response

    async def _connect(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        self._connection = await connect(
            self.url,
            additional_headers=headers,
            origin=self.origin,
            open_timeout=self.timeout,
            close_timeout=self.timeout,
            ping_interval=20,
            ping_timeout=10,
        )
        self.connections += 1
        try:
            await self._connection.send(
                json.dumps(
                    {"type": "register", "stream": self.stream_id, "role": "ocr"}
                )
            )
            response = await self._receive_object()
            if response.get("type") != "registered":
                raise FanSiteProtocolError(
                    "fan site did not acknowledge OCR registration"
                )
        except Exception:
            await self._discard_connection()
            raise
        self._last_sent_values = None

    async def _discard_connection(self) -> None:
        connection, self._connection = self._connection, None
        self._last_sent_values = None
        if connection is not None:
            with suppress(ConnectionClosed):
                await connection.close()

    async def send(self, batch: ResultBatch) -> None:
        values = self._values(batch)
        if not values:
            return
        if self._connection is None or self._connection.close_code is not None:
            await self._discard_connection()
            await self._connect()
        if not self.send_unchanged and values == self._last_sent_values:
            return
        assert self._connection is not None
        try:
            await self._connection.send(json.dumps({"type": "ocr", "values": values}))
            response = await self._receive_object()
            if response.get("type") != "ocr_update":
                raise FanSiteProtocolError(
                    "fan site did not acknowledge the OCR update"
                )
        except Exception:
            await self._discard_connection()
            raise
        self.last_ack = response
        self.last_ack_monotonic = time.monotonic()
        self._last_sent_values = values

    async def close(self) -> None:
        await self._discard_connection()
        await super().close()

    def details(self) -> dict[str, Any]:
        if not self.last_ack:
            return {"stream_id": self.stream_id}
        return {
            "stream_id": self.stream_id,
            "saved": bool(self.last_ack.get("saved", False)),
            "updated_fields": self.last_ack.get("updated_fields", []),
            "conflict_fields": self.last_ack.get("conflict_fields", []),
            "ignored_fields": self.last_ack.get("ignored_fields", []),
            "live_data_mode": self.last_ack.get("live_data_mode"),
            "game_id": self.last_ack.get("game_id"),
        }
