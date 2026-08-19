from __future__ import annotations

import re
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
    RevisionIntent,
    SemanticBuildPlan,
    ValidationReport,
)
from app.services.compilers.cadquery_compiler import CadQueryCompiler
from app.services.executors.cadquery_executor import CadQueryExecutor
from app.services.gateway.model_gateway import ModelGateway
from app.services.revision.revision_engine import RevisionEngine
from app.services.storage.file_store import FileStore

# Artifact kinds the API will serve. Each names an ArtifactPaths field, "<kind>_path".
ARTIFACT_KINDS = ("glb", "stl", "step_export", "source")

# The shape uuid4().hex[:12] produces. Anything else is not an id we ever issued,
# and the store turns it into a filesystem path, so it is checked before use.
DESIGN_ID = re.compile(r"[0-9a-f]{12}")


class DesignService:
    """Orchestrate the planner -> compiler -> executor pipeline for a design.

    Coordinates planning, compilation, execution (with bounded repair
    attempts), revision, and persistence for a single parametric part.
    """

    # One executor run, plus two chances to shrink and retry.
    MAX_BUILD_ATTEMPTS = 3

    # Fallback for when a failure message names no parameter we can act on.
    REPAIRABLE_PARAMETER_TOKENS = ("thickness", "radius", "depth", "width", "offset")

    def __init__(
        self,
        settings: Settings,
        store: FileStore,
        gateway: ModelGateway,
        compiler: CadQueryCompiler,
        executor: CadQueryExecutor,
        revision_engine: RevisionEngine,
    ) -> None:
        self.settings = settings
        self.store = store
        self.gateway = gateway
        self.compiler = compiler
        self.executor = executor
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
        if not DESIGN_ID.fullmatch(design_id):
            return None
        record = self.store.load_record(design_id)
        if record is None:
            return None
        intent, patch = self.revision_engine.interpret(instruction, record.plan)
        if intent.confidence_score < 0.6:
            return self._revision_not_applied(
                design_id,
                record.plan,
                intent,
                patch,
                "Revision confidence below 0.60; clarification required.",
            )
        if intent.confidence_score < 0.80 or patch is None or intent.operation == "topology_change":
            return self._revision_not_applied(
                design_id,
                record.plan,
                intent,
                patch,
                "Revision requires confirmation before rebuild.",
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
        )

    @staticmethod
    def _revision_not_applied(
        design_id: str,
        plan: SemanticBuildPlan,
        intent: RevisionIntent,
        patch: PlanPatch | None,
        reason: str,
    ) -> ReviseResponse:
        """Hand back the untouched plan with the reason it was left alone.

        The web app renders `warnings` and nothing else from this response, so
        the engine's evidence has to travel with them. Without it, "no step in
        this plan has a width parameter" reaches the screen as a bare refusal.
        """
        return ReviseResponse(
            design_id=design_id,
            revision=intent,
            patch=patch,
            plan=plan,
            warnings=[reason, *intent.confidence_evidence],
        )

    def artifact_path(self, design_id: str, kind: str) -> Path | None:
        if kind not in ARTIFACT_KINDS or not DESIGN_ID.fullmatch(design_id):
            return None
        record = self.store.load_record(design_id)
        if record is None or record.build is None:
            return None
        path = getattr(record.build.artifacts, f"{kind}_path", None)
        if not path:
            return None
        # Records are JSON on disk, so the stored path is only as trustworthy as
        # the file. Anything outside the runtime root is not ours to serve.
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.settings.runtime_root.resolve()):
            return None
        return resolved

    def _attempt_build(
        self,
        *,
        design_id: str,
        brief: DesignBrief,
        plan: SemanticBuildPlan,
        compile_result: CompileResult,
        artifacts_dir: Path,
        dirty_from_step: str | None = None,
    ) -> BuildResult:
        current_plan = deepcopy(plan)
        current_compile = compile_result
        total_cache_hits = 0
        repairs: list[str] = []
        last_result: BuildResult | None = None
        for attempt in range(1, self.MAX_BUILD_ATTEMPTS + 1):
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
            last_result.cache_hits = total_cache_hits
            if last_result.status == "succeeded":
                break
            if (
                not last_result.failure
                or last_result.failure.attribution_basis == "setup_unavailable"
            ):
                break
            if attempt == self.MAX_BUILD_ATTEMPTS:
                # No attempt left to run a repair on. Computing one here would put
                # a resize into _note_repairs that the artifacts never received.
                break
            patch = self._repair_patch(current_plan, last_result.failure)
            if patch is None:
                break
            repairs.append(patch.reason)
            current_plan = self.revision_engine.apply_patch(current_plan, patch)
            current_compile = self.compiler.compile(current_plan)
            if self._compile_has_blockers(current_compile):
                last_result = self._compile_failure_result(current_compile)
                last_result.attempts_used = attempt
                last_result.cache_hits = total_cache_hits
                break
            dirty_from_step = self._earliest_dirty_step(current_plan, patch)

        assert last_result is not None
        if repairs:
            self._note_repairs(last_result, repairs)
        return last_result

    def _repair_patch(self, plan: SemanticBuildPlan, failure: FailureReport) -> PlanPatch | None:
        failed_step_id = failure.failed_step_id
        if failed_step_id is None:
            return None
        step = next((step for step in plan.steps if step.id == failed_step_id), None)
        if step is None:
            return None
        numeric = {
            key: float(value)
            for key, value in step.parameters.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        # Shrink what the failure actually named, since CadQuery and OCC usually
        # echo the offending dimension. When the message names nothing there is
        # no better target than every repairable length in the step, and two
        # rounds of that leave the part 19% small, so _note_repairs writes the
        # change into the build result instead of letting it pass unmentioned.
        message = failure.message.lower()
        implicated = {key: value for key, value in numeric.items() if key.lower() in message}
        if not implicated:
            implicated = {
                key: value
                for key, value in numeric.items()
                if any(token in key for token in self.REPAIRABLE_PARAMETER_TOKENS)
            }
        if not implicated:
            return None
        # round() makes the 10% shrink a no-op below about 0.05 mm. Re-running the
        # executor on byte-identical parameters would burn the remaining attempts
        # and then claim a repair that never happened.
        updates: dict[str, float | int | str | bool] = {
            key: round(value * 0.9, 2)
            for key, value in implicated.items()
            if round(value * 0.9, 2) != value
        }
        if not updates:
            return None
        changes = ", ".join(
            f"{key} {implicated[key]:g} to {updates[key]:g}" for key in sorted(updates)
        )
        # A dimension usually belongs to more than one step: a project box
        # carries its width in create_shell and again in add_standoffs, because
        # the standoffs are positioned against the wall. Patching only the
        # failed step leaves a 200 mm shell with standoffs placed for a 180 mm
        # one, and _earliest_dirty_step would then serve the stale shell from
        # cache and call the result a success. Shrink every step that names the
        # same dimension so the plan stays one part.
        targets = [
            step.id
            for step in plan.steps
            if step.id == failed_step_id or any(key in step.parameters for key in updates)
        ]
        return PlanPatch(
            reason=f"Conservative repair for {failed_step_id} after execution failure: {changes}.",
            target_step_ids=targets,
            parameter_updates=updates,
            topology_change=False,
        )

    @staticmethod
    def _note_repairs(result: BuildResult, repairs: list[str]) -> None:
        """Surface automatic parameter repairs so a resized part is never a surprise."""
        summary = " ".join(repairs)
        result.validation.checks["auto_repairs"] = summary
        if result.failure is not None:
            result.failure.message = f"{result.failure.message} {summary}"

    @staticmethod
    def _earliest_dirty_step(plan: SemanticBuildPlan, patch: PlanPatch) -> str | None:
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
                message=("; ".join(messages) if messages else "Compiler reported blocking errors."),
                next_action="Use the planning output as a manual CAD recipe or revise the object toward supported macros.",
                attribution_basis="setup_unavailable",
            ),
            attempts_used=1,
        )

    @staticmethod
    def _compile_failure_validation() -> ValidationReport:
        return ValidationReport(status="failed", checks={"compile_blocked": True})
