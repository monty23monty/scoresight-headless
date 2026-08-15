from __future__ import annotations

import json

import pytest

from scoresight.core.config import ConfigStore, RevisionConflict
from scoresight.core.profiles import ProfileStore


def test_config_store_creates_and_atomically_replaces_config(tmp_path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    original = store.load()
    assert path.exists()
    assert original.revision == 0

    changed = original.model_copy(
        update={"source": original.source.model_copy(update={"mode": "1080p25"})}
    )
    saved = store.replace(changed, expected_revision=0)

    assert saved.revision == 1
    assert ConfigStore(path).load().source.mode == "1080p25"
    assert json.loads(path.read_text(encoding="utf-8"))["revision"] == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_config_store_rejects_stale_revision(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    config = store.load()
    store.replace(config, 0)
    with pytest.raises(RevisionConflict):
        store.replace(config, 0)


def test_profile_store_rejects_path_traversal(tmp_path) -> None:
    profiles = ProfileStore(tmp_path / "profiles")
    with pytest.raises(ValueError):
        profiles.save("../outside", ConfigStore(tmp_path / "config.json").load())
