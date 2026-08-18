from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.core.settings import Settings
from app.models.schemas import DesignRecord


class FileStore:
    """Filesystem-backed persistence for design records and their artifacts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.designs_root.mkdir(parents=True, exist_ok=True)

    def design_dir(self, design_id: str) -> Path:
        # No mkdir here: reads go through this too, and creating a directory for
        # every id someone asks about lets a GET litter the runtime root.
        return self.settings.designs_root / design_id

    def design_record_path(self, design_id: str) -> Path:
        return self.design_dir(design_id) / "record.json"

    def artifacts_dir(self, design_id: str) -> Path:
        path = self.design_dir(design_id) / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def compile_source_path(self, design_id: str) -> Path:
        return self.artifacts_dir(design_id) / "compiled.py"

    def write_text(self, path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=self._default), encoding="utf-8")

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def save_record(self, record: DesignRecord) -> None:
        record.updated_at = datetime.now(UTC)
        self.write_json(self.design_record_path(record.design_id), record.model_dump(mode="json"))

    def load_record(self, design_id: str) -> DesignRecord | None:
        """Load a design record, or None if there isn't a readable one.

        runtime/ outlives the code that wrote it. A record left by an older
        build carries fields this schema no longer has, and StrictModel forbids
        extras, so validating it raises; a record from an interrupted write is
        truncated JSON. Both used to escape as a bare 500 on endpoints whose
        honest answer is 404, so both are treated as "no record here".
        """
        path = self.design_record_path(design_id)
        if not path.exists():
            return None
        try:
            return DesignRecord.model_validate(self.read_json(path))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return None

    @staticmethod
    def _default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Unsupported JSON value: {type(value)!r}")
