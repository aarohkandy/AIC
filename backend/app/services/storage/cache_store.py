from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.models.schemas import CacheEntry


class CacheStore:
    """Content-addressed store for per-step build cache entries.

    Used from the executor subprocess, which is handed a cache root and a
    compiler version in its JSON payload rather than a Settings object.
    """

    def __init__(self, cache_root: Path, compiler_version: str) -> None:
        self.cache_root = cache_root
        self.compiler_version = compiler_version
        self.cache_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_hash(payload: object) -> str:
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
                "compiler_version": self.compiler_version,
            }
        )

    def entry_dir(self, cache_key: str) -> Path:
        return self.cache_root / cache_key

    def entry_path(self, cache_key: str) -> Path:
        return self.entry_dir(cache_key) / "entry.json"

    def artifact_path(self, cache_key: str, step_id: str) -> Path:
        return self.entry_dir(cache_key) / f"{step_id}.step"

    def metrics_path(self, cache_key: str, step_id: str) -> Path:
        return self.entry_dir(cache_key) / f"{step_id}-metrics.json"

    def get(self, cache_key: str, step_id: str) -> CacheEntry | None:
        """Return the entry only when its artifact and metrics files are both on disk.

        ``entry.json`` is written last, but a half-populated directory from an
        interrupted export would otherwise read as a hit.
        """
        entry_path = self.entry_path(cache_key)
        if not (
            entry_path.exists()
            and self.artifact_path(cache_key, step_id).exists()
            and self.metrics_path(cache_key, step_id).exists()
        ):
            return None
        return CacheEntry.model_validate_json(entry_path.read_text(encoding="utf-8"))

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
            compiler_version=self.compiler_version,
            artifact_path=str(artifact_path),
            metrics_path=str(metrics_path),
        )
        self.entry_dir(cache_key).mkdir(parents=True, exist_ok=True)
        self.entry_path(cache_key).write_text(
            json.dumps(entry.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return entry
