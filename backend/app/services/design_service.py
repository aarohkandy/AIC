from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from app.core.settings import Settings
from app.models.schemas import (
    BuildRequest,
    BuildResponse,
    BuildResult,
    CompileRequest,
    CompileResult,
    DesignBrief,
    DesignRecord,
    FailureReport,
    PlanPatch,
    PlanResponse,
    ReviseResponse,
)
from app.services.compilers.cadquery_compiler import CadQueryCompiler
from app.services.executors.cadquery_executor import CadQueryExecutor
from app.services.gateway.model_gateway import ModelGateway
from app.services.revision.revision_engine import RevisionEngine
from app.services.storage.file_store import FileStore
from app.services.validation.design_validator import DesignValidator


class DesignService:
    def __init__(
        self,
        settings: Settings,
        store: FileStore,
        gateway: ModelGateway,
        compiler: CadQueryCompiler,
        executor: CadQueryExecutor,
        validator: DesignValidator,
        revision_engine: RevisionEngine,
    ) -> None:
        self.settings = settings
        self.store = store
        self.gateway = gateway
        self.compiler = compiler
        self.executor = executor
        self.validator = validator
        self.revision_engine = revision_engine

    def plan(self, brief: DesignBrief) -> PlanResponse:
        design_id = uuid4().hex[:12]
        plan, risk, model_call, warnings = self.gateway.plan(brief)
        record = DesignRecord(design_id=design_id, brief=brief, plan=plan)
        self.store.save_record(record)
        return PlanResponse(
            design_id=design_id,
            brief=brief,
            plan=plan,
            planning_risk_score=risk,
            planner_path=model_call.path,
            model_call=model_call,
            warnings=warnings,
        )

    def compile(self, request: CompileRequest) -> CompileResult:
        return self.compiler.compile(request.plan)

    def build(self, request: BuildRequest) -> BuildResponse:
        design_id = uuid4().hex[:12]
        plan, risk, model_call, warnings = self.gateway.plan(request.brief)
        compile_result = self.compiler.compile(plan)
        artifacts_dir = self.store.artifacts_dir(design_id)
        self.store.write_text(self.store.compile_source_path(design_id), compile_result.source)
        if self._compile_has_blockers(compile_result):
            build_result = self._compile_failure_result(compile_result)
        else:
            build_result = self._attempt_build(
                design_id=design_id,
                brief=request.brief,
                plan=plan,
                compile_result=compile_result,
                artifacts_dir=artifacts_dir,
            )
        build_result.metrics.planning_risk_score = risk
        build_result.metrics.token_usage = {
            "input": model_call.input_tokens,
            "output": model_call.output_tokens,
        }
        record = DesignRecord(
            design_id=design_id,
            brief=request.brief,
            plan=plan,
            compile=compile_result,
            build=build_result,
        )
        self.store.save_record(record)
        return BuildResponse(
            design_id=design_id,
            brief=request.brief,
            plan=plan,
            compile=compile_result,
            build=build_result,
            model_call=model_call,
            warnings=warnings,
        )

    def revise(self, design_id: str, instruction: str) -> ReviseResponse | None:
        record = self.store.load_record(design_id)
        if record is None:
            return None
        intent, patch = self.revision_engine.interpret(instruction, record.plan)
        warnings: list[str] = []
        if intent.confidence_score < 0.6:
            return ReviseResponse(
                design_id=design_id,
                revision=intent,
                patch=patch,
                plan=record.plan,
                warnings=["Revision confidence below 0.60; clarification required."],
            )
        if intent.confidence_score < 0.85 or patch is None or intent.operation == "topology_change":
            return ReviseResponse(
                design_id=design_id,
                revision=intent,
                patch=patch,
                plan=record.plan,
                warnings=["Revision requires confirmation before rebuild."],
            )

        updated_plan = self.revision_engine.apply_patch(record.plan, patch)
        compile_result = self.compiler.compile(updated_plan)
        if self._compile_has_blockers(compile_result):
            build_result = self._compile_failure_result(compile_result)
        else:
            dirty_from_step = self._earliest_dirty_step(updated_plan, patch)
            build_result = self._attempt_build(
                design_id=design_id,
                brief=record.brief,
                plan=updated_plan,
                compile_result=compile_result,
                artifacts_dir=self.store.artifacts_dir(design_id),
                dirty_from_step=dirty_from_step,
            )
        record.plan = updated_plan
        record.compile = compile_result
        record.build = build_result
        record.revision = intent
        record.patch = patch
        self.store.save_record(record)
        return ReviseResponse(
            design_id=design_id,
            revision=intent,
            patch=patch,
            plan=updated_plan,
            compile=compile_result,
            build=build_result,
            warnings=warnings,
        )

    def artifact_path(self, design_id: str, kind: str) -> Path | None:
        record = self.store.load_record(design_id)
        if record is None or record.build is None:
            return None
        path = getattr(record.build.artifacts, f"{kind}_path", None)
        return Path(path) if path else None

    def _attempt_build(
        self,
        *,
        design_id: str,
        brief: DesignBrief,
        plan,
        compile_result: CompileResult,
        artifacts_dir: Path,
        dirty_from_step: str | None = None,
    ) -> BuildResult:
        current_plan = deepcopy(plan)
        current_compile = compile_result
        total_cache_hits = 0
        last_result: BuildResult | None = None
        for attempt in range(1, 4):
            last_result = self.executor.execute(
                design_id=design_id,
                brief=brief,
                plan=current_plan,
                compile_result=current_compile,
                artifacts_dir=artifacts_dir,
                dirty_from_step=dirty_from_step,
            )
            total_cache_hits += last_result.cache_hits
            last_result.attempts_used = attempt
            if last_result.status == "succeeded":
                last_result.cache_hits = total_cache_hits
                return last_result
            if not last_result.failure or last_result.failure.attribution_basis == "setup_unavailable":
                last_result.cache_hits = total_cache_hits
                return last_result
            patch = self._repair_patch(current_plan, last_result.failure)
            if patch is None:
                last_result.cache_hits = total_cache_hits
                return last_result
            current_plan = self.revision_engine.apply_patch(current_plan, patch)
            current_compile = self.compiler.compile(current_plan)
            dirty_from_step = self._earliest_dirty_step(current_plan, patch)

        assert last_result is not None
        last_result.cache_hits = total_cache_hits
        return last_result

    def _repair_patch(self, plan, failure: FailureReport) -> PlanPatch | None:
        failed_step_id = failure.failed_step_id
        if failed_step_id is None:
            return None
        for step in plan.steps:
            if step.id != failed_step_id:
                continue
            updates = {}
            for key, value in step.parameters.items():
                if isinstance(value, (int, float)) and any(token in key for token in ("thickness", "radius", "depth", "width", "offset")):
                    updates[key] = round(float(value) * 0.9, 2)
            if not updates:
                return None
            return PlanPatch(
                reason=f"Conservative repair for {failed_step_id} after execution failure.",
                target_step_ids=[failed_step_id],
                parameter_updates=updates,
                topology_change=False,
            )
        return None

    @staticmethod
    def _earliest_dirty_step(plan, patch: PlanPatch) -> str | None:
        if not patch.target_step_ids:
            return plan.steps[0].id if plan.steps else None
        step_order = {step.id: index for index, step in enumerate(plan.steps)}
        return min(patch.target_step_ids, key=lambda step_id: step_order.get(step_id, 10**6))

    @staticmethod
    def _compile_has_blockers(compile_result: CompileResult) -> bool:
        if any(diagnostic.level == "error" for diagnostic in compile_result.diagnostics):
            return True
        return any(finding.severity == "error" for finding in compile_result.whitelist_findings)

    @staticmethod
    def _compile_failure_result(compile_result: CompileResult) -> BuildResult:
        messages = [
            diagnostic.message
            for diagnostic in compile_result.diagnostics
            if diagnostic.level == "error"
        ] + [
            finding.message
            for finding in compile_result.whitelist_findings
            if finding.severity == "error"
        ]
        return BuildResult(
            status="failed",
            validation=DesignService._compile_failure_validation(),
            failure=FailureReport(
                failure_type="compile_failed",
                message="; ".join(messages) if messages else "Compiler reported blocking errors.",
                next_action="Use the planning output as a manual CAD recipe or revise the object toward supported macros.",
                attribution_basis="setup_unavailable",
            ),
            attempts_used=1,
        )

    @staticmethod
    def _compile_failure_validation():
        return {
            "status": "failed",
            "checks": {"compile_blocked": True},
        }
