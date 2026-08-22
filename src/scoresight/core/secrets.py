from __future__ import annotations

from pathlib import Path
from typing import Any

REDACTED = "__redacted__"
SECRET_KEYS = frozenset({"authorization", "password", "secret", "token", "uri"})


def read_secret_file(path: str | Path) -> str:
    secret_path = Path(path)
    value = secret_path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"secret file is empty: {secret_path}")
    if len(value) > 65536:
        raise ValueError(f"secret file is unexpectedly large: {secret_path}")
    return value


def redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if key.lower() in SECRET_KEYS else redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


def restore_redacted(candidate: Any, current: Any) -> Any:
    if isinstance(candidate, dict) and isinstance(current, dict):
        restored: dict[str, Any] = {}
        for key, value in candidate.items():
            if key.lower() in SECRET_KEYS and value == REDACTED and key in current:
                restored[key] = current[key]
            else:
                restored[key] = restore_redacted(value, current.get(key))
        return restored
    if isinstance(candidate, list) and isinstance(current, list):
        current_by_id = {
            item.get("id"): item
            for item in current
            if isinstance(item, dict) and item.get("id") is not None
        }
        return [
            (
                restore_redacted(item, current_by_id.get(item.get("id"), {}))
                if isinstance(item, dict)
                else item
            )
            for item in candidate
        ]
    return candidate
