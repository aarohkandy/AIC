from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from pydantic import ValidationError

from app.models.schemas import CacheEntry

# Ceiling on the whole cache root, enforced oldest-first after every save.
# Nothing in here is precious: an entry is one STEP export of one step at one
# set of parameters, and evicting it costs the CadQuery time to rebuild that
# step on the next build that wants it. Without a ceiling the directory grows
# by one export per step per parameter set and nothing ever reclaims it.
CACHE_BUDGET_BYTES = 256 * 1024 * 1024


class CacheStore:
    """Content-addressed store for per-step build cache entries.

    Used from the executor subprocess, which is handed a cache root and a
    compiler version in its JSON payload rather than a Settings object.
    """

    def __init__(
        self,
        cache_root: Path,
        compiler_version: str,
        budget_bytes: int = CACHE_BUDGET_BYTES,
    ) -> None:
        self.cache_root = cache_root
        self.compiler_version = compiler_version
        self.budget_bytes = budget_bytes
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

    def get(self, cache_key: str, step_id: str) -> CacheEntry | None:
        """Return the entry, or None when there is not a readable one.

        ``entry.json`` is written last, but a half-populated directory from an
        interrupted export would otherwise read as a hit, so the artifact has to
        be on disk too.

        An entry that will not parse is a miss rather than an exception. It used
        to raise, from inside the executor subprocess, at a point where the
        runtime had already named the step it was working on - so a truncated
        file blamed a step that was fine and sent the repair loop off to shrink
        its parameters. Nothing is deleted here: the next build to reach this
        step rewrites entry.json in this same directory, so a poisoned entry
        heals itself. A record left by an older compiler carries fields this
        schema no longer has, which StrictModel refuses, and that is a miss for
        the same reason.
        """
        entry_path = self.entry_path(cache_key)
        if not (entry_path.exists() and self.artifact_path(cache_key, step_id).exists()):
            return None
        try:
            return CacheEntry.model_validate_json(entry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValidationError):
            return None

    def save(
        self,
        design_id: str,
        step_id: str,
        parameter_hash: str,
        parent_artifact_hash: str,
        artifact_path: Path,
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
        )
        self.entry_dir(cache_key).mkdir(parents=True, exist_ok=True)
        self.entry_path(cache_key).write_text(
            json.dumps(entry.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        self.prune()
        return entry

    def prune(self) -> list[str]:
        """Delete the oldest entries until the root is inside the budget.

        Oldest by directory mtime, which is the last write into the entry, so a
        step that keeps being rebuilt keeps its place. The budget covers the
        whole root rather than one design, because the disk does too. Returns
        the keys that were dropped.

        This walks every entry on every save. That is a directory listing and a
        stat per file, against a step export that just spent seconds inside
        OpenCascade, so it is not worth being cleverer about.
        """
        sized: list[tuple[float, Path, int]] = []
        for entry_dir in self.cache_root.iterdir():
            if not entry_dir.is_dir():
                continue
            try:
                size = sum(path.stat().st_size for path in entry_dir.iterdir())
                sized.append((entry_dir.stat().st_mtime, entry_dir, size))
            except OSError:
                # Two builds can prune at once, and this entry lost the race.
                # It is on its way out either way, so leave it out of the sum.
                continue

        total = sum(size for _, _, size in sized)
        if total <= self.budget_bytes:
            return []

        dropped: list[str] = []
        for _, entry_dir, size in sorted(sized, key=lambda item: item[0]):
            if total <= self.budget_bytes:
                break
            try:
                shutil.rmtree(entry_dir)
            except OSError:
                # A read-only directory, or a file another process still holds
                # open. Charging its bytes against the budget anyway would stop
                # the loop early and report an eviction that did not happen, so
                # skip it and keep evicting; the next entry is as good.
                continue
            total -= size
            dropped.append(entry_dir.name)
        return dropped
