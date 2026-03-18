from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetDimensions(StrictModel):
    width: float | None = None
    depth: float | None = None
    height: float | None = None
    diameter: float | None = None


class DesignBrief(StrictModel):
    prompt: str
    units: Literal["mm", "cm", "in"] = "mm"
    target_dims: TargetDimensions = Field(default_factory=TargetDimensions)
    required_features: list[str] = Field(default_factory=list)
    style_notes: list[str] = Field(default_factory=list)
    tolerances: dict[str, float] | None = None


class SemanticStep(StrictModel):
    id: str
    intent: str
    primitive_or_macro: str
    workplane: str = ""
    location_notes: list[str] = Field(default_factory=list)
    size_notes: list[str] = Field(default_factory=list)
    sketch_constraints: list[str] = Field(default_factory=list)
    manual_instructions: list[str] = Field(default_factory=list)
    parameters: dict[str, float | int | str | bool] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    postcondition: str


class SemanticBuildPlan(StrictModel):
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    parameters: dict[str, float | int | str | bool] = Field(default_factory=dict)
    steps: list[SemanticStep]


class EditableRegion(StrictModel):
    step_id: str
    label: str
    start_line: int
    end_line: int


class CompileDiagnostic(StrictModel):
    level: Literal["info", "warning", "error"]
    code: str
    message: str


class WhitelistFinding(StrictModel):
    severity: Literal["info", "warning", "error"]
    message: str


class CompileResult(StrictModel):
    language: Literal["cadquery_py"] = "cadquery_py"
    source: str
    editable_regions: list[EditableRegion] = Field(default_factory=list)
    whitelist_findings: list[WhitelistFinding] = Field(default_factory=list)
    diagnostics: list[CompileDiagnostic] = Field(default_factory=list)


class ValidationReport(StrictModel):
    status: Literal["passed", "failed", "skipped"]
    checks: dict[str, bool | float | str] = Field(default_factory=dict)


class ArtifactPaths(StrictModel):
    step_path: str | None = None
    glb_path: str | None = None
    step_export_path: str | None = None
    source_path: str | None = None
    stl_path: str | None = None


class BuildMetrics(StrictModel):
    volume: float | None = None
    bounding_box: dict[str, float] = Field(default_factory=dict)
    attempt_latency_ms: int | None = None
    planning_risk_score: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)


class FailureReport(StrictModel):
    failure_type: str
    failed_step_id: str | None = None
    root_cause_step_id: str | None = None
    message: str
    next_action: str
    attribution_basis: Literal["failed_step", "replay_validation", "setup_unavailable"]


class BuildResult(StrictModel):
    status: Literal["succeeded", "failed", "needs_confirmation"]
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths)
    metrics: BuildMetrics = Field(default_factory=BuildMetrics)
    validation: ValidationReport = Field(
        default_factory=lambda: ValidationReport(status="skipped")
    )
    attempts_used: int = 0
    cache_hits: int = 0
    failure: FailureReport | None = None


class RevisionIntent(StrictModel):
    operation: Literal["update_parameter", "topology_change", "unknown"]
    targets: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    confidence_score: float = 0.0
    confidence_evidence: list[str] = Field(default_factory=list)


class PlanPatch(StrictModel):
    reason: str
    target_step_ids: list[str] = Field(default_factory=list)
    parameter_updates: dict[str, float | int | str | bool] = Field(default_factory=dict)
    topology_change: bool = False


class ModelCallRecord(StrictModel):
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    path: Literal["hosted", "local"]


class PlanResponse(StrictModel):
    design_id: str
    brief: DesignBrief
    plan: SemanticBuildPlan
    planning_risk_score: float
    planner_path: Literal["hosted", "local"]
    model_call: ModelCallRecord
    warnings: list[str] = Field(default_factory=list)


class BuildResponse(StrictModel):
    design_id: str
    brief: DesignBrief
    plan: SemanticBuildPlan
    compile: CompileResult
    build: BuildResult
    model_call: ModelCallRecord
    warnings: list[str] = Field(default_factory=list)


class ReviseRequest(StrictModel):
    design_id: str
    instruction: str


class ReviseResponse(StrictModel):
    design_id: str
    revision: RevisionIntent
    patch: PlanPatch | None = None
    plan: SemanticBuildPlan
    compile: CompileResult | None = None
    build: BuildResult | None = None
    warnings: list[str] = Field(default_factory=list)


class CompileRequest(StrictModel):
    plan: SemanticBuildPlan


class BuildRequest(StrictModel):
    brief: DesignBrief


class ExecutorHealth(StrictModel):
    executor_mode: Literal["local", "containerized"]
    healthy: bool
    details: dict[str, Any] = Field(default_factory=dict)


class CacheEntry(StrictModel):
    cache_key: str
    design_id: str
    step_id: str
    parent_artifact_hash: str
    parameter_hash: str
    compiler_version: str
    artifact_path: str
    metrics_path: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DesignRecord(StrictModel):
    design_id: str
    brief: DesignBrief
    plan: SemanticBuildPlan
    compile: CompileResult | None = None
    build: BuildResult | None = None
    revision: RevisionIntent | None = None
    patch: PlanPatch | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
