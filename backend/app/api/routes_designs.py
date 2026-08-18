from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.dependencies import get_design_service
from app.models.schemas import (
    BuildRequest,
    BuildResponse,
    CompileRequest,
    CompileResult,
    DesignBrief,
    PlanResponse,
    ReviseRequest,
    ReviseResponse,
)
from app.services.design_service import ARTIFACT_KINDS, DesignService

router = APIRouter(prefix="/designs", tags=["designs"])

ARTIFACT_MEDIA_TYPES = {
    "glb": "model/gltf-binary",
    "stl": "model/stl",
    "step_export": "application/step",
    "source": "text/x-python",
}


@router.post("/plan", response_model=PlanResponse)
def create_plan(
    brief: DesignBrief,
    service: DesignService = Depends(get_design_service),
) -> PlanResponse:
    return service.plan(brief)


@router.post("/compile", response_model=CompileResult)
def compile_plan(
    request: CompileRequest,
    service: DesignService = Depends(get_design_service),
) -> CompileResult:
    return service.compile(request)


@router.post("/build", response_model=BuildResponse)
def build_design(
    request: BuildRequest,
    service: DesignService = Depends(get_design_service),
) -> BuildResponse:
    return service.build(request)


@router.post("/revise", response_model=ReviseResponse)
def revise_design(
    request: ReviseRequest,
    service: DesignService = Depends(get_design_service),
) -> ReviseResponse:
    response = service.revise(request.design_id, request.instruction)
    if response is None:
        raise HTTPException(status_code=404, detail="Design not found")
    return response


@router.get("/{design_id}/artifacts/{kind}")
def get_artifact(
    design_id: str,
    kind: str,
    service: DesignService = Depends(get_design_service),
) -> FileResponse:
    if kind not in ARTIFACT_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown artifact kind {kind!r}. Expected one of: {', '.join(ARTIFACT_KINDS)}.",
        )
    path: Path | None = service.artifact_path(design_id, kind)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media_type = ARTIFACT_MEDIA_TYPES.get(kind, "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)
