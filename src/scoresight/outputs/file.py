from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from scoresight.core.models import ResultBatch
from scoresight.outputs.base import OutputAdapter


class FileOutput(OutputAdapter):
    def __init__(self, adapter_id: str, path: Path) -> None:
        super().__init__(adapter_id)
        self.path = path

    async def send(self, batch: ResultBatch) -> None:
        await asyncio.to_thread(self._write, batch)

    def _write(self, batch: ResultBatch) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(batch.model_dump(mode="json"), handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
