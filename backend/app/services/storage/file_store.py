from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.settings import Settings
from app.models.schemas import DesignRecord


class FileStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.designs_root.mkdir(parents=True, exist_ok=True)

    def design_dir(self, design_id: str) -> Path:
        path = self.settings.designs_root / design_id
        path.mkdir(parents=True, exist_ok=True)
        return path

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
        record.updated_at = datetime.now(timezone.utc)
        self.write_json(self.design_record_path(record.design_id), record.model_dump(mode="json"))

    def load_record(self, design_id: str) -> DesignRecord | None:
        path = self.design_record_path(design_id)
        if not path.exists():
            return None
        return DesignRecord.model_validate(self.read_json(path))

    @staticmethod
    def _default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Unsupported JSON value: {type(value)!r}")

