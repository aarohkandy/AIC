from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.core.settings import Settings
from app.models.schemas import CacheEntry


class CacheStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.cache_root.mkdir(parents=True, exist_ok=True)

    def make_hash(self, payload: object) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def make_cache_key(
        self,
        design_id: str,
        step_id: str,
        parameter_hash: str,
        parent_artifact_hash: str,
    ) -> str:
        return self.make_hash(
            {
                "design_id": design_id,
                "step_id": step_id,
                "parameter_hash": parameter_hash,
                "parent_artifact_hash": parent_artifact_hash,
                "compiler_version": self.settings.compiler_version,
            }
        )

    def entry_dir(self, cache_key: str) -> Path:
        path = self.settings.cache_root / cache_key
        path.mkdir(parents=True, exist_ok=True)
        return path

    def entry_path(self, cache_key: str) -> Path:
        return self.entry_dir(cache_key) / "entry.json"

    def get(self, cache_key: str) -> CacheEntry | None:
        path = self.entry_path(cache_key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CacheEntry.model_validate(payload)

    def save(
        self,
        design_id: str,
        step_id: str,
        parameter_hash: str,
        parent_artifact_hash: str,
        artifact_path: Path,
        metrics_path: Path,
    ) -> CacheEntry:
        cache_key = self.make_cache_key(design_id, step_id, parameter_hash, parent_artifact_hash)
        entry = CacheEntry(
            cache_key=cache_key,
            design_id=design_id,
            step_id=step_id,
            parent_artifact_hash=parent_artifact_hash,
            parameter_hash=parameter_hash,
            compiler_version=self.settings.compiler_version,
            artifact_path=str(artifact_path),
            metrics_path=str(metrics_path),
        )
        self.entry_path(cache_key).write_text(
            json.dumps(entry.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return entry

