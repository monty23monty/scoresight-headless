from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from scoresight.core.events import LatestValueBus
from scoresight.core.models import OutputConfig, ResultBatch, ResultField, ResultState
from scoresight.outputs.base import OutputAdapter
from scoresight.outputs.file import FileOutput
from scoresight.outputs.http import VMixOutput
from scoresight.outputs.manager import OutputManager


def batch() -> ResultBatch:
    return ResultBatch(
        stream_id="test",
        sequence=1,
        captured_at=datetime.now(UTC),
        latency_ms=5,
        fields=[ResultField(id="home", name="Home Score", value="12", state=ResultState.OK)],
    )


async def test_vmix_output_sends_mapped_field() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    output = VMixOutput("vmix", "localhost", 8099, "1", {"home": "Home.Text"})
    await output.client.aclose()
    output.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await output.send(batch())
    await output.close()

    assert len(requests) == 1
    assert "Function=SetText" in str(requests[0].url)
    assert "SelectedName=Home.Text" in str(requests[0].url)
    assert "Value=12" in str(requests[0].url)


async def test_file_output_atomically_writes_latest_batch(tmp_path) -> None:
    path = tmp_path / "nested" / "results.json"
    output = FileOutput("file", path)
    await output.send(batch())
    assert '"sequence": 1' in path.read_text(encoding="utf-8")
    assert not list(path.parent.glob(".*"))


async def test_vmix_does_not_send_rejected_fields() -> None:
    value = batch()
    value.fields[0].state = ResultState.REJECTED
    output = VMixOutput("vmix", "localhost", 8099, "1", {"home": "Home.Text"})
    await output.client.aclose()
    output.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"unexpected request: {request.url}")
        )
    )
    await output.send(value)
    await output.close()


class FlakyOutput(OutputAdapter):
    def __init__(self, adapter_id: str) -> None:
        super().__init__(adapter_id)
        self.calls = 0
        self.sent_event = asyncio.Event()

    async def send(self, value: ResultBatch) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        self.sent_event.set()


@pytest.mark.asyncio
async def test_output_adapter_recovers_and_manager_stops(monkeypatch) -> None:
    bus = LatestValueBus[ResultBatch]()
    manager = OutputManager(bus)
    output = FlakyOutput("flaky")
    monkeypatch.setattr(manager, "_build", lambda config: output)
    manager.configure([OutputConfig(id="flaky", kind="file", enabled=True, settings={"path": "x"})])
    await manager.start()
    await bus.publish(batch())
    for _ in range(20):
        if output.status.message:
            break
        await asyncio.sleep(0.01)
    await bus.publish(batch())
    await asyncio.sleep(0.3)
    await bus.publish(batch())
    await asyncio.wait_for(output.sent_event.wait(), timeout=1)
    await manager.stop()
    assert output.status.failed == 1
    assert output.status.sent == 1
    assert output.status.state == "stopped"


def test_fan_site_token_is_loaded_from_secret_file(tmp_path) -> None:
    token_file = tmp_path / "fan-token"
    token_file.write_text("secret-from-file", encoding="utf-8")
    output = OutputManager._build(
        OutputConfig(
            id="fan",
            kind="fan_site",
            enabled=True,
            settings={
                "endpoint": "wss://fan.example/ws/ocr",
                "stream_id": "stream",
                "token_file": str(token_file),
            },
        )
    )
    assert output.token == "secret-from-file"


def test_fan_site_token_is_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("SCORESIGHT_FAN_SITE_TOKEN", "secret-from-environment")
    output = OutputManager._build(
        OutputConfig(
            id="fan",
            kind="fan_site",
            enabled=True,
            settings={
                "endpoint": "wss://fan.example/ws/ocr",
                "stream_id": "stream",
                "token_env": "SCORESIGHT_FAN_SITE_TOKEN",
            },
        )
    )
    assert output.token == "secret-from-environment"


def test_fan_site_environment_token_must_not_be_empty(monkeypatch) -> None:
    monkeypatch.delenv("SCORESIGHT_FAN_SITE_TOKEN", raising=False)
    config = OutputConfig(
        id="fan",
        kind="fan_site",
        enabled=True,
        settings={
            "endpoint": "wss://fan.example/ws/ocr",
            "stream_id": "stream",
            "token_env": "SCORESIGHT_FAN_SITE_TOKEN",
        },
    )
    with pytest.raises(ValueError, match="environment variable is empty"):
        OutputManager._build(config)
