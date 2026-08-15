from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from websockets.asyncio.server import ServerConnection, serve

from scoresight.core.models import ResultBatch, ResultField, ResultState
from scoresight.outputs.fan_site import FanSiteProtocolError, FanSiteWebSocketOutput


def batch(clock: str = "16:03", home: str = "3") -> ResultBatch:
    return ResultBatch(
        stream_id="scoresight",
        sequence=1,
        captured_at=datetime.now(UTC),
        latency_ms=5,
        fields=[
            ResultField(
                id="clock-id",
                name="Clock",
                value=clock,
                state=ResultState.UNCHANGED,
            ),
            ResultField(
                id="home-id",
                name="Home Score",
                value=home,
                state=ResultState.OK,
            ),
            ResultField(
                id="empty-id",
                name="Away Score",
                value="",
                state=ResultState.EMPTY,
            ),
        ],
    )


async def test_fan_site_output_registers_and_sends_spec_payload() -> None:
    received: list[dict[str, object]] = []
    request_details: dict[str, str] = {}

    async def handler(connection: ServerConnection) -> None:
        request_details["path"] = connection.request.path
        request_details["authorization"] = connection.request.headers["Authorization"]
        registration = json.loads(await connection.recv())
        received.append(registration)
        await connection.send(
            json.dumps({"type": "registered", "stream": "stream1", "role": "ocr"})
        )
        update = json.loads(await connection.recv())
        received.append(update)
        await connection.send(
            json.dumps(
                {
                    "type": "ocr_update",
                    "saved": True,
                    "updated_fields": ["clock", "home_score"],
                    "conflict_fields": [],
                    "live_data_mode": "automatic",
                    "ignored_fields": [],
                    "game_id": 42,
                }
            )
        )
        await connection.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        output = FanSiteWebSocketOutput(
            "fan",
            f"ws://127.0.0.1:{port}/ws/ocr",
            "stream1",
            "ingest-secret",
            {"clock-id": "Clock.Text", "Home Score": "score_a"},
        )
        await output.send(batch())
        await output.send(batch())
        details = output.details()
        await output.close()

    assert request_details == {
        "path": "/ws/ocr/stream1/",
        "authorization": "Bearer ingest-secret",
    }
    assert received == [
        {"type": "register", "stream": "stream1", "role": "ocr"},
        {
            "type": "ocr",
            "values": {"Clock.Text": "16:03", "score_a": "3"},
        },
    ]
    assert details == {
        "stream_id": "stream1",
        "saved": True,
        "updated_fields": ["clock", "home_score"],
        "conflict_fields": [],
        "ignored_fields": [],
        "live_data_mode": "automatic",
        "game_id": 42,
    }


async def test_fan_site_output_surfaces_protocol_errors() -> None:
    async def handler(connection: ServerConnection) -> None:
        await connection.recv()
        await connection.send(
            json.dumps({"type": "registered", "stream": "stream1", "role": "ocr"})
        )
        await connection.recv()
        await connection.send(json.dumps({"type": "error", "error": "no_live_game"}))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        output = FanSiteWebSocketOutput(
            "fan",
            f"ws://127.0.0.1:{port}/ws/ocr",
            "stream1",
            "ingest-secret",
            {},
        )
        with pytest.raises(FanSiteProtocolError, match="no_live_game"):
            await output.send(batch())
        await output.close()
