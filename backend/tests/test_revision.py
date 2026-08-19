"""What a revision instruction does to a plan someone already has.

Most of these drive `DesignService.revise` rather than the engine alone,
because the confidence score only means something next to the gates that read
it. None of it needs CadQuery: a real build here ends `cadquery_unavailable`,
so the executor is a stand-in and every assertion is about the plan, the patch,
the compile result or the response.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.settings import Settings
from app.models.schemas import (
    BuildResult,
    CompileResult,
    DesignBrief,
    DesignRecord,
    PlanPatch,
    SemanticBuildPlan,
    SemanticStep,
)
from app.services.compilers.cadquery_compiler import CadQueryCompiler
from app.services.design_service import DesignService
from app.services.gateway.model_gateway import ModelGateway
from app.services.planners.ollama_planner import OllamaPlanner
from app.services.planners.rule_based_planner import RuleBasedPlanner
from app.services.revision.revision_engine import RevisionEngine
from app.services.storage.file_store import FileStore
from app.services.validation.design_validator import DesignValidator
from app.services.validation.source_validator import SourceValidator

BRIEF = DesignBrief(prompt="a mug 86 mm across and 96 mm tall")
DESIGN_ID = "a1b2c3d4e5f6"


class CountingCompiler:
    """The real compiler, plus a count of how often it ran."""

    def __init__(self) -> None:
        self.inner = CadQueryCompiler(SourceValidator())
        self.calls = 0

    def compile(self, plan: SemanticBuildPlan) -> CompileResult:
        self.calls += 1
        return self.inner.compile(plan)


class RecordingExecutor:
    """Answers every execution with the same result and remembers the call."""

    def __init__(self, result: BuildResult | None = None) -> None:
        self.result = result or BuildResult(status="succeeded")
        self.calls: list[dict] = []

    def execute(self, **kwargs: object) -> BuildResult:
        self.calls.append(kwargs)
        # _attempt_build writes attempt counts onto whatever it gets back, so
        # each call needs its own copy.
        return self.result.model_copy(deep=True)


def make_service(tmp_path: Path) -> DesignService:
    # _env_file=None so a developer's .env cannot decide how this behaves, and
    # the local planner is off because nothing here should touch the network.
    settings = Settings(
        _env_file=None,
        runtime_root=tmp_path / "runtime",
        prefer_local_model_planner=False,
    )
    validator = DesignValidator()
    planner = RuleBasedPlanner()
    return DesignService(
        settings=settings,
        store=FileStore(settings),
        gateway=ModelGateway(settings, planner, validator, ollama_planner=OllamaPlanner(settings)),
        compiler=CountingCompiler(),
        executor=RecordingExecutor(),
        revision_engine=RevisionEngine(),
    )


def store_mug(service: DesignService) -> SemanticBuildPlan:
    """Save a real rule-based mug plan under DESIGN_ID and return it."""
    plan = RuleBasedPlanner().plan(BRIEF)
    service.store.save_record(DesignRecord(design_id=DESIGN_ID, brief=BRIEF, plan=plan))
    return plan


def step(plan: SemanticBuildPlan, step_id: str) -> SemanticStep:
    return next(candidate for candidate in plan.steps if candidate.id == step_id)


def test_revision_engine_maps_handle_thickness_change() -> None:
    engine = RevisionEngine()
    plan = SemanticBuildPlan(
        summary="Create mug then add handle.",
        steps=[
            SemanticStep(
                id="add_handle",
                intent="Add handle",
                primitive_or_macro="add_mug_handle",
                parameters={"handle_thickness": 12, "handle_width": 30},
                depends_on=[],
                postcondition="Handle exists",
            )
        ],
    )

    revision, patch = engine.interpret("Make the handle thickness 10 mm.", plan)

    assert revision.operation == "update_parameter"
    assert revision.confidence_score >= 0.8
    assert patch is not None
    assert patch.parameter_updates["handle_thickness"] == 10.0


@pytest.mark.parametrize(
    "instruction",
    [
        "change the wall thickness from 3 mm to 5 mm",
        "change the wall thickness from 3 to 5",
        "take the wall thickness 3mm to 5mm",
        # An abbreviated unit gets a period about as often as it does not, and
        # the run between the two numbers has to be able to cross it.
        "change the wall thickness from 3 mm. to 5 mm.",
    ],
)
def test_a_stated_change_lands_on_the_destination_value(tmp_path, instruction):
    # The value the user is moving away from comes first in the sentence, so a
    # search for the first number sets the size they just asked to stop using.
    service = make_service(tmp_path)
    store_mug(service)

    response = service.revise(DESIGN_ID, instruction)

    assert response is not None
    assert response.patch is not None
    assert response.patch.parameter_updates == {"wall_thickness": 5.0}
    assert step(response.plan, "hollow_body").parameters["wall_thickness"] == 5.0


def test_a_grouped_number_is_one_number_and_not_two(tmp_path):
    # "1,200" scanned as 1 and then 200, which made it a two-number instruction
    # that proposed setting the height to 1 mm.
    service = make_service(tmp_path)
    store_mug(service)

    response = service.revise(DESIGN_ID, "make the height 1,200 mm")

    assert response is not None
    assert response.patch is not None
    assert response.patch.parameter_updates == {"height": 1200.0}


def test_a_confident_revision_reaches_the_plan_the_record_and_the_executor(tmp_path):
    service = make_service(tmp_path)
    store_mug(service)

    response = service.revise(DESIGN_ID, "set the wall thickness to 5 mm")

    assert response is not None
    assert response.warnings == []
    assert response.patch is not None
    assert response.patch.target_step_ids == ["hollow_body"]
    assert step(response.plan, "hollow_body").parameters["wall_thickness"] == 5.0
    assert response.plan.parameters["wall_thickness"] == 5.0
    assert response.compile is not None and response.build is not None
    assert response.build.status == "succeeded"
    # Only the hollowing changed, so the outer body may still come from cache.
    assert service.executor.calls[0]["dirty_from_step"] == "hollow_body"
    # And the next revision has to start from the plan this one produced.
    reloaded = service.store.load_record(DESIGN_ID)
    assert reloaded is not None
    assert step(reloaded.plan, "hollow_body").parameters["wall_thickness"] == 5.0
    assert reloaded.patch is not None and reloaded.revision is not None
    assert reloaded.revision.targets == ["wall_thickness"]


def test_a_parameter_no_step_owns_is_not_a_successful_revision(tmp_path):
    service = make_service(tmp_path)
    plan = store_mug(service)
    assert "width" not in plan.parameters, "a mug plan carries no width; that is the point"

    response = service.revise(DESIGN_ID, "make the width 120 mm")

    assert response is not None
    assert response.patch is None
    assert response.build is None and response.compile is None
    assert service.compiler.calls == 0
    assert service.executor.calls == []
    # The user has to be told why, and "width" must not be written into the
    # plan as a parameter nothing reads.
    assert any("no step in this plan has a width" in w.lower() for w in response.warnings)
    assert "width" not in response.plan.parameters
    reloaded = service.store.load_record(DESIGN_ID)
    assert reloaded is not None and reloaded.revision is None


def test_an_unreadable_instruction_asks_for_clarification_without_building(tmp_path):
    service = make_service(tmp_path)
    store_mug(service)

    response = service.revise(DESIGN_ID, "add a lid")

    assert response is not None
    assert response.revision.confidence_score < 0.6
    assert response.warnings[0] == "Revision confidence below 0.60; clarification required."
    assert response.compile is None and response.build is None
    assert service.compiler.calls == 0
    assert service.executor.calls == []


@pytest.mark.parametrize(
    "instruction",
    [
        # Topology, which no patch can express.
        "remove 5 mm from the height",
        # Two numbers and nothing saying which is the new value.
        "make the wall thickness 5 mm and the height 120 mm",
        # A stated change plus a second request. The stated change is readable,
        # but applying only that half and reporting success loses the height.
        "change the wall thickness from 3 mm to 5 mm and the height to 120 mm",
        # A step rather than a destination: 5 is not the height being asked for.
        "increase the height by 5 mm",
    ],
)
def test_a_revision_the_engine_had_to_guess_at_waits_for_confirmation(tmp_path, instruction):
    service = make_service(tmp_path)
    store_mug(service)

    response = service.revise(DESIGN_ID, instruction)

    assert response is not None
    assert 0.6 <= response.revision.confidence_score < 0.8
    assert response.warnings[0] == "Revision requires confirmation before rebuild."
    assert response.compile is None and response.build is None
    assert service.compiler.calls == 0
    assert service.executor.calls == []
    # The plan on screen is still the one that was built.
    assert step(response.plan, "create_outer_body").parameters["height"] == 96.0
    assert step(response.plan, "hollow_body").parameters["wall_thickness"] == 4.0


def test_a_declined_revision_says_why_it_was_declined(tmp_path):
    service = make_service(tmp_path)
    store_mug(service)

    response = service.revise(DESIGN_ID, "make the wall thickness 5 mm and the height 120 mm")

    assert response is not None
    # The web app renders warnings and nothing else, so the engine's reasoning
    # has to travel with them.
    assert response.warnings[1:] == response.revision.confidence_evidence
    assert any("2 numbers" in warning for warning in response.warnings)


def test_a_revision_the_compiler_refuses_is_a_failed_build_not_an_exception(tmp_path):
    service = make_service(tmp_path)
    plan = RuleBasedPlanner().plan(BRIEF)
    # A plan can arrive from a hosted planner naming a macro this compiler does
    # not have. Revising it still has to answer.
    step(plan, "hollow_body").primitive_or_macro = "cnc_pocket"
    service.store.save_record(DesignRecord(design_id=DESIGN_ID, brief=BRIEF, plan=plan))

    response = service.revise(DESIGN_ID, "set the wall thickness to 5 mm")

    assert response is not None
    assert response.build is not None
    assert response.build.status == "failed"
    assert response.build.failure is not None
    assert response.build.failure.failure_type == "compile_failed"
    assert "cnc_pocket" in response.build.failure.message
    assert response.build.validation.checks == {"compile_blocked": True}
    # No source to run, so the executor is never asked.
    assert service.executor.calls == []
    # The revision still applied to the plan, and the record says so.
    assert step(response.plan, "hollow_body").parameters["wall_thickness"] == 5.0
    reloaded = service.store.load_record(DESIGN_ID)
    assert reloaded is not None and reloaded.build is not None
    assert reloaded.build.status == "failed"


def test_a_revision_against_an_unknown_design_is_not_an_answer(tmp_path):
    service = make_service(tmp_path)

    assert service.revise(DESIGN_ID, "set the wall thickness to 5 mm") is None
    assert service.revise("../etc", "set the wall thickness to 5 mm") is None


def test_the_dirty_step_is_the_earliest_one_the_patch_touches():
    plan = RuleBasedPlanner().plan(BRIEF)
    patch = PlanPatch(
        reason="Shrink the handle and the body.",
        target_step_ids=["add_handle", "create_outer_body"],
        parameter_updates={"outer_diameter": 80.0},
    )

    # Every step from the dirty one on is rebuilt, so picking the later of the
    # two would serve a stale body out of the cache.
    assert DesignService._earliest_dirty_step(plan, patch) == "create_outer_body"
