from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_designs import router as design_router
from app.core.dependencies import get_design_service
from app.core.settings import get_settings


settings = get_settings()
allowed_origins = {
    settings.frontend_origin,
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
}
app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_origin_regex=r"^https?://tauri\.localhost$|^tauri://localhost$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(design_router)


@app.get("/health")
def health() -> dict[str, object]:
    service = get_design_service()
    return {
        "status": "ok",
        "python_version": sys.version,
        "runtime_root": str(settings.runtime_root),
        "executor_health": service.gateway.executor_health().model_dump(mode="json"),
        "warning": settings.python_warning,
    }
