from __future__ import annotations

import asyncio
from typing import Any

import httpx

from scoresight.core.models import ResultBatch, ResultState
from scoresight.outputs.base import OutputAdapter


class HttpOutput(OutputAdapter):
    def __init__(
        self,
        adapter_id: str,
        url: str,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        timeout: float = 1.0,
    ) -> None:
        super().__init__(adapter_id)
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 0.5))
        )

    async def send(self, batch: ResultBatch) -> None:
        response = await self.client.request(
            self.method,
            self.url,
            headers=self.headers,
            json=batch.model_dump(mode="json"),
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self.client.aclose()
        await super().close()


class VMixOutput(OutputAdapter):
    def __init__(
        self,
        adapter_id: str,
        host: str,
        port: int,
        input_number: str,
        field_mapping: dict[str, str],
        *,
        send_unchanged: bool = False,
    ) -> None:
        super().__init__(adapter_id)
        self.base_url = f"http://{host}:{port}/api/"
        self.input_number = input_number
        self.field_mapping = field_mapping
        self.send_unchanged = send_unchanged
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(1.0, connect=0.5))

    async def send(self, batch: ResultBatch) -> None:
        accepted = {ResultState.OK}
        if self.send_unchanged:
            accepted.add(ResultState.UNCHANGED)
        calls = []
        for field in batch.fields:
            selected_name = self.field_mapping.get(field.id) or self.field_mapping.get(
                field.name
            )
            if selected_name is None or field.state not in accepted:
                continue
            calls.append(
                self.client.post(
                    self.base_url,
                    params={
                        "Function": "SetText",
                        "Input": self.input_number,
                        "SelectedName": selected_name,
                        "Value": field.value,
                    },
                )
            )
        if not calls:
            return
        responses = await asyncio.gather(*calls)
        for response in responses:
            response.raise_for_status()

    async def close(self) -> None:
        await self.client.aclose()
        await super().close()


class UnoOutput(OutputAdapter):
    def __init__(
        self,
        adapter_id: str,
        endpoint: str,
        field_mapping: dict[str, str],
        *,
        overlay_id: str | None = None,
        send_unchanged: bool = False,
    ) -> None:
        super().__init__(adapter_id)
        self.endpoint = endpoint
        self.field_mapping = field_mapping
        self.overlay_id = overlay_id
        self.send_unchanged = send_unchanged
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(1.0, connect=0.5))

    async def send(self, batch: ResultBatch) -> None:
        accepted = {ResultState.OK}
        if self.send_unchanged:
            accepted.add(ResultState.UNCHANGED)
        for field in batch.fields:
            command = self.field_mapping.get(field.id) or self.field_mapping.get(
                field.name
            )
            if command is None or field.state not in accepted:
                continue
            payload: dict[str, Any]
            if self.overlay_id:
                payload = {
                    "command": "SetOverlayContentField",
                    "fieldId": command,
                    "id": self.overlay_id,
                    "value": field.value,
                }
            else:
                payload = {"command": command, "value": field.value}
            response = await self.client.put(self.endpoint, json=payload)
            response.raise_for_status()

    async def close(self) -> None:
        await self.client.aclose()
        await super().close()
