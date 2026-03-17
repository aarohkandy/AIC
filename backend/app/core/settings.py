from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AI_CAD_")

    app_name: str = "AI CAD"
    app_env: str = "development"
    runtime_root: Path = Path(__file__).resolve().parents[2] / "runtime"
    executor_mode: Literal["local", "containerized"] = "local"
    allow_hosted_models: bool = False
    gemini_api_key: str | None = None
    gemini_flash_model: str = "gemini-2.5-flash"
    gemini_pro_model: str = "gemini-2.5-pro"
    default_flash_calls_per_day: int = 20
    default_pro_calls_per_day: int = 3
    max_hosted_calls_per_design: int = 3
    max_pro_calls_per_design: int = 1
    default_executor_timeout_seconds: int = 30
    compiler_version: str = "v1"
    frontend_origin: str = "http://localhost:5173"

    @property
    def designs_root(self) -> Path:
        return self.runtime_root / "designs"

    @property
    def cache_root(self) -> Path:
        return self.runtime_root / "cache"

    @property
    def quota_file(self) -> Path:
        return self.runtime_root / "quota-ledger.json"

    @property
    def health_file(self) -> Path:
        return self.runtime_root / "executor-health.json"

    @property
    def python_warning(self) -> str:
        return (
            "Supported geometry runtime is Python 3.11 via Miniforge/conda + mamba. "
            "Local builds may fail outside that environment."
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.designs_root.mkdir(parents=True, exist_ok=True)
    settings.cache_root.mkdir(parents=True, exist_ok=True)
    settings.runtime_root.mkdir(parents=True, exist_ok=True)
    return settings

