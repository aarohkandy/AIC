from __future__ import annotations

import sys
import time
from threading import Lock
from typing import Any

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

# Deliberately longer than READY_POLL_MS in frontend/src/App.tsx, which is 10 s.
# A TTL under the poll interval expires between every pair of polls and the
# cache never gets used once the app is up.
LOCAL_PLANNER_PROBE_TTL_SECONDS = 15.0
_probe_lock = Lock()
_probe_cache: tuple[float, dict[str, Any]] | None = None


def _local_planner_health() -> dict[str, Any]:
    """Probe Ollama at most once per TTL.

    The probe is an HTTP round trip that waits up to five seconds before giving
    up, and /health sits on a timer in both the web app and the Tauri shell. The
    TTL is set above that timer so a steady poll answers from memory instead of
    re-opening the socket, at the cost of noticing a restarted Ollama up to
    fifteen seconds late.
    """
    global _probe_cache
    with _probe_lock:
        now = time.monotonic()
        if _probe_cache is not None and now - _probe_cache[0] < LOCAL_PLANNER_PROBE_TTL_SECONDS:
            return _probe_cache[1]
        try:
            health = get_design_service().gateway.local_planner_health()
        except Exception as exc:
            # Something is answering on the Ollama port but it is not Ollama: a
            # proxy, a dev server, the wrong port in AI_CAD_OLLAMA_BASE_URL. The
            # planner is unavailable and the reason is worth reading, which is
            # what this endpoint is for. Raising instead made /health a 500, and
            # the browser gate reads any non-200 as "the backend is down".
            health = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        _probe_cache = (now, health)
        return health


@app.get("/health")
def health() -> dict[str, object]:
    service = get_design_service()
    return {
        "status": "ok",
        "python_version": sys.version,
        "runtime_root": str(settings.runtime_root),
        "executor_health": service.gateway.executor_health().model_dump(mode="json"),
        "local_planner_health": _local_planner_health(),
        "warning": settings.python_warning,
    }
