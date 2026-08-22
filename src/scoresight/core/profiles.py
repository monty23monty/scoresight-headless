from __future__ import annotations

import re
from pathlib import Path

from platformdirs import user_config_path

from scoresight.core.config import ConfigStore
from scoresight.core.models import ServiceConfig

PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$")


class ProfileStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or user_config_path("scoresight") / "profiles"

    def list(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def load(self, name: str) -> ServiceConfig:
        return ServiceConfig.model_validate_json(
            self._path(name).read_text(encoding="utf-8")
        )

    def save(self, name: str, config: ServiceConfig) -> None:
        path = self._path(name)
        ConfigStore(path)._write(config)

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=False)

    def _path(self, name: str) -> Path:
        if PROFILE_NAME.fullmatch(name) is None:
            raise ValueError("invalid profile name")
        return self.directory / f"{name}.json"
