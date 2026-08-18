from __future__ import annotations

import json
import os
from pathlib import Path

from app.services.storage.cache_store import CacheStore


def save_entry(store: CacheStore, step_id: str, *, artifact_bytes: int = 64) -> str:
    """Write one entry the way the runtime does: artifact first, entry.json last."""
    cache_key = store.make_cache_key("d1", step_id, "params", "root")
    store.entry_dir(cache_key).mkdir(parents=True, exist_ok=True)
    artifact = store.artifact_path(cache_key, step_id)
    artifact.write_bytes(b"ISO-10303-21;\n".ljust(artifact_bytes, b" "))
    store.save("d1", step_id, "params", "root", artifact)
    return cache_key


def test_a_saved_entry_reads_back(tmp_path: Path) -> None:
    store = CacheStore(tmp_path / "cache", "v1")

    cache_key = save_entry(store, "body")
    entry = store.get(cache_key, "body")

    assert entry is not None
    assert entry.step_id == "body"
    assert Path(entry.artifact_path).exists()


def test_an_entry_without_its_artifact_is_a_miss(tmp_path: Path) -> None:
    store = CacheStore(tmp_path / "cache", "v1")
    cache_key = save_entry(store, "body")
    store.artifact_path(cache_key, "body").unlink()

    assert store.get(cache_key, "body") is None


def test_a_truncated_entry_is_a_miss_rather_than_an_exception(tmp_path: Path) -> None:
    # This runs inside the executor subprocess, after the runtime has already
    # recorded which step it is working on. Raising here reported a build
    # failure against a step that was fine, and the repair loop then spent
    # three attempts shrinking that step's parameters.
    store = CacheStore(tmp_path / "cache", "v1")
    cache_key = save_entry(store, "body")
    store.entry_path(cache_key).write_text('{"cache_key": "abc", "desi', encoding="utf-8")

    assert store.get(cache_key, "body") is None


def test_an_entry_from_an_older_schema_is_a_miss(tmp_path: Path) -> None:
    store = CacheStore(tmp_path / "cache", "v1")
    cache_key = save_entry(store, "body")
    stale = json.loads(store.entry_path(cache_key).read_text(encoding="utf-8"))
    stale["metrics_path"] = str(store.entry_dir(cache_key) / "body-metrics.json")
    store.entry_path(cache_key).write_text(json.dumps(stale), encoding="utf-8")

    assert store.get(cache_key, "body") is None


def test_nothing_is_dropped_while_the_cache_is_inside_its_budget(tmp_path: Path) -> None:
    store = CacheStore(tmp_path / "cache", "v1", budget_bytes=20_000)

    steps = ("body", "hollow")
    keys = [save_entry(store, step_id, artifact_bytes=2_000) for step_id in steps]

    assert store.prune() == []
    assert all(store.get(key, step_id) is not None for key, step_id in zip(keys, steps))


def test_the_oldest_entries_go_first_when_the_budget_is_passed(tmp_path: Path) -> None:
    store = CacheStore(tmp_path / "cache", "v1", budget_bytes=20_000)

    keys = {}
    for age, step_id in enumerate(("body", "hollow", "handle")):
        keys[step_id] = save_entry(store, step_id, artifact_bytes=8_000)
        # Three saves inside one filesystem clock tick would leave the eviction
        # order up to the filesystem, so age each entry as it lands.
        os.utime(store.entry_dir(keys[step_id]), (1_000 + age, 1_000 + age))

    # Two entries fit and the third does not, so exactly the oldest one goes.
    assert not store.entry_dir(keys["body"]).exists()
    assert store.get(keys["body"], "body") is None
    assert store.get(keys["hollow"], "hollow") is not None
    assert store.get(keys["handle"], "handle") is not None


def test_prune_names_what_it_dropped(tmp_path: Path) -> None:
    store = CacheStore(tmp_path / "cache", "v1", budget_bytes=20_000)
    keys = {}
    for age, step_id in enumerate(("body", "hollow")):
        keys[step_id] = save_entry(store, step_id, artifact_bytes=9_000)
        os.utime(store.entry_dir(keys[step_id]), (1_000 + age, 1_000 + age))

    store.budget_bytes = 10_000

    assert store.prune() == [keys["body"]]


def test_an_entry_that_will_not_delete_does_not_count_towards_the_budget(tmp_path: Path) -> None:
    # An entry directory the process cannot empty. Counting its bytes as
    # reclaimed used to stop the loop one entry early and name it in the return
    # value, so the caller was told the cache was inside its budget when it was
    # not, on every save from then on.
    store = CacheStore(tmp_path / "cache", "v1", budget_bytes=30_000)
    keys = {}
    for age, step_id in enumerate(("body", "hollow", "handle")):
        keys[step_id] = save_entry(store, step_id, artifact_bytes=9_000)
        os.utime(store.entry_dir(keys[step_id]), (1_000 + age, 1_000 + age))

    stuck = store.entry_dir(keys["body"])
    stuck.chmod(0o500)
    # Room for two of the three entries, so exactly one eviction is needed and
    # the stuck one cannot be it.
    store.budget_bytes = 20_000
    try:
        dropped = store.prune()
    finally:
        stuck.chmod(0o700)

    assert dropped == [keys["hollow"]]
    assert stuck.exists()
    assert store.get(keys["handle"], "handle") is not None
