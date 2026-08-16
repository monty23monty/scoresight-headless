from __future__ import annotations

import asyncio
import os
from pathlib import Path

from scoresight.core.events import LatestValueBus
from scoresight.core.models import OutputConfig, ResultBatch
from scoresight.core.secrets import read_secret_file
from scoresight.outputs.base import OutputAdapter
from scoresight.outputs.fan_site import FanSiteWebSocketOutput
from scoresight.outputs.file import FileOutput
from scoresight.outputs.http import HttpOutput, UnoOutput, VMixOutput


class OutputManager:
    def __init__(self, bus: LatestValueBus[ResultBatch]) -> None:
        self.bus = bus
        self.adapters: dict[str, OutputAdapter] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def configure(self, configs: list[OutputConfig]) -> None:
        if self.tasks:
            raise RuntimeError("stop output manager before reconfiguring")
        self.adapters = {}
        for config in configs:
            if not config.enabled:
                continue
            adapter = self._build(config)
            adapter.kind = config.kind
            self.adapters[config.id] = adapter

    async def start(self) -> None:
        for adapter_id, adapter in self.adapters.items():
            self.tasks[adapter_id] = asyncio.create_task(
                adapter.run(self.bus), name=f"output:{adapter_id}"
            )

    async def stop(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for adapter in self.adapters.values():
            await adapter.close()
        self.tasks.clear()

    @staticmethod
    def _build(config: OutputConfig) -> OutputAdapter:
        settings = config.settings
        if config.kind == "vmix":
            return VMixOutput(
                config.id,
                host=str(settings.get("host", "localhost")),
                port=int(settings.get("port", 8099)),
                input_number=str(settings.get("input", "1")),
                field_mapping=config.field_mapping,
                send_unchanged=config.send_unchanged,
            )
        if config.kind == "uno":
            return UnoOutput(
                config.id,
                endpoint=str(settings["endpoint"]),
                field_mapping=config.field_mapping,
                overlay_id=settings.get("overlay_id"),
                send_unchanged=config.send_unchanged,
            )
        if config.kind == "webhook":
            return HttpOutput(
                config.id,
                url=str(settings["url"]),
                method=str(settings.get("method", "POST")),
                headers=dict(settings.get("headers", {})),
            )
        if config.kind == "file":
            return FileOutput(config.id, Path(str(settings["path"])))
        if config.kind == "fan_site":
            if settings.get("token_file"):
                token = read_secret_file(str(settings["token_file"]))
            elif settings.get("token"):
                token = str(settings["token"])
            else:
                environment_name = str(settings["token_env"])
                token = os.getenv(environment_name, "").strip()
                if not token:
                    raise ValueError(
                        f"fan_site token environment variable is empty: {environment_name}"
                    )
            return FanSiteWebSocketOutput(
                config.id,
                endpoint=str(settings["endpoint"]),
                stream_id=str(settings["stream_id"]),
                token=token,
                field_mapping=config.field_mapping,
                origin=str(settings["origin"]) if settings.get("origin") else None,
                send_unchanged=config.send_unchanged,
                timeout=float(settings.get("timeout", 5.0)),
            )
        raise ValueError(f"unsupported output kind: {config.kind}")
