from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from scoresight.core.models import ResultBatch, ResultField, ResultState
from scoresight.outputs.file import FileOutput
from scoresight.outputs.http import VMixOutput


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
