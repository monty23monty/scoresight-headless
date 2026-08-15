from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import RLock

from platformdirs import user_config_path

from scoresight.core.models import ServiceConfig


class RevisionConflict(ValueError):
    """Raised when a client attempts to replace stale configuration."""


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_path("scoresight") / "config-v1.json"
        self._lock = RLock()
        self._config: ServiceConfig | None = None

    def load(self) -> ServiceConfig:
        with self._lock:
            if self._config is not None:
                return self._config.model_copy(deep=True)
            if not self.path.exists():
                self._config = ServiceConfig()
                self._write(self._config)
            else:
                self._config = ServiceConfig.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
            return self._config.model_copy(deep=True)

    def replace(self, config: ServiceConfig, expected_revision: int) -> ServiceConfig:
        with self._lock:
            current = self.load()
            if current.revision != expected_revision:
                raise RevisionConflict(
                    f"configuration revision is {current.revision}, not {expected_revision}"
                )
            updated = config.model_copy(update={"revision": current.revision + 1}, deep=True)
            self._write(updated)
            self._config = updated
            return updated.model_copy(deep=True)

    def _write(self, config: ServiceConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = config.model_dump_json(indent=2)
        fd, temp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
