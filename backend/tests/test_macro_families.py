"""Every macro family, compiled and then actually executed.

``test_compiler.py`` runs two hand-written plans through the compiler, and
``test_executor.py`` drives the runtime from a source string the compiler never
produced. Neither of them ever ran the source the macro library emits, so nine
of the twelve macros had never been executed by anything.

These tests take the whole path - ``RuleBasedPlanner`` to ``CadQueryCompiler``
to ``exec`` to ``build_model()`` to ``export_artifacts()`` - for all five
families the planner knows, plus hand-built plans for the two macros it never
emits. The fake CadQuery in ``stub_cadquery`` refuses any non-positive length,
so a recipe that shrinks a wall past the solid it sits inside fails here rather
than on the one machine that has CadQuery installed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import stub_cadquery

from app.models.schemas import (
    CompileResult,
    DesignBrief,
    SemanticBuildPlan,
    SemanticStep,
)
from app.services.compilers.cadquery_compiler import CadQueryCompiler
from app.services.executors import runtime
from app.services.planners.rule_based_planner import RuleBasedPlanner
from app.services.validation.source_validator import SourceValidator


class Family(NamedTuple):
    """A macro family, and what its emitted source should be seen doing."""

    name: str
    prompt: str
    macros: tuple[str, ...]
    # The calls the family's first step makes, in order. They are the cheapest
    # proof that the source that ran is the recipe that was planned.
    opening_calls: tuple[str, ...]
    # Wordings of the same request, for the hostile-dimension sweep. Each one
    # has to land both numbers in the macro or the sweep is only testing
    # defaults, so every phrasing here was checked against the extractor.
    phrasings: tuple[str, ...]


FAMILIES = (
    Family(
        name="mug",
        prompt="a mug 86 mm across and 96 mm tall",
        macros=("create_mug_body", "hollow_mug_body", "add_mug_handle"),
        opening_calls=("Workplane", "circle", "extrude"),
        phrasings=(
            "a mug {a} mm across and {b} mm tall",
            "a coffee cup with a {a} mm diameter, {b} mm high",
            "mug: diameter {a} mm, height {b} mm",
        ),
    ),
    Family(
        name="l_bracket",
        prompt="an L bracket 60 mm wide and 40 mm high",
        macros=("create_l_bracket", "drill_mount_holes"),
        opening_calls=("Workplane", "polyline", "close", "extrude"),
        phrasings=(
            "an L bracket {a} mm wide and {b} mm high",
            "a shelf bracket {a} mm wide, {b} mm deep",
            "bracket: width {a} mm, height {b} mm",
        ),
    ),
    Family(
        name="project_box",
        prompt="a project box 200 mm wide 150 mm deep and 60 mm tall",
        macros=("create_project_box_shell", "add_standoffs"),
        opening_calls=("Workplane", "box", "Workplane", "box", "translate", "cut"),
        phrasings=(
            "a project box {a} mm wide and {b} mm deep",
            "an enclosure {a} mm wide with {b} mm walls",
            "box: width {a} mm, height {b} mm",
        ),
    ),
    Family(
        name="phone_stand",
        prompt="a phone stand 80 mm wide",
        macros=("create_phone_stand", "add_retention_lip"),
        opening_calls=("Workplane", "box", "Workplane", "box", "translate", "rotate"),
        phrasings=(
            "a phone stand {a} mm wide and {b} mm tall",
            "a phone stand {a} mm deep, {b} mm high",
            "stand: width {a} mm, depth {b} mm",
        ),
    ),
    Family(
        name="bottle_cap",
        prompt="a bottle cap 30 mm across and 12 mm tall",
        macros=("create_bottle_cap", "add_grip_cutouts"),
        opening_calls=("Workplane", "circle", "extrude"),
        phrasings=(
            "a bottle cap {a} mm across and {b} mm tall",
            "a cap with a {a} mm diameter and {b} mm walls",
            "bottle cap: diameter {a} mm, height {b} mm",
        ),
    ),
)

# Pairs a user could plausibly type and a recipe cannot plausibly honour: a
# tenth of a printer nozzle, a metre-scale mug, and the mismatched pairs where
# one dimension leaves the other no room. A wall that gets clamped past the
# solid it is cut from used to come out of here as a negative length.
HOSTILE_PAIRS = (
    ("0.001", "0.001"),
    ("0.001", "1000000"),
    ("0.1", "3.7"),
    ("3.7", "0.1"),
    ("200", "1"),
    ("1000000", "0.001"),
)


def compiler() -> CadQueryCompiler:
    return CadQueryCompiler(SourceValidator())


def errors(result: CompileResult) -> list[str]:
    return [diagnostic.code for diagnostic in result.diagnostics if diagnostic.level == "error"]


def run_source(source: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(compile(source, "<compiled>", "exec"), namespace)
    return namespace


def export_into(namespace: dict[str, Any], state: Any, directory: Path) -> list[Path]:
    paths = [directory / "model.step", directory / "model.stl", directory / "preview.glb"]
    namespace["export_artifacts"](state, *[str(path) for path in paths])
    return paths


def hostile_cases() -> list[Any]:
    cases = []
    for family in FAMILIES:
        for index, phrasing in enumerate(family.phrasings):
            for first, second in HOSTILE_PAIRS:
                cases.append(
                    pytest.param(
                        phrasing.format(a=first, b=second),
                        family.macros[0],
                        id=f"{family.name}-{index}-{first}x{second}",
                    )
                )
    return cases


@pytest.mark.parametrize("family", FAMILIES, ids=[family.name for family in FAMILIES])
def test_a_planned_family_compiles_to_source_that_runs(family: Family, tmp_path, monkeypatch):
    calls = stub_cadquery.install(monkeypatch)
    plan = RuleBasedPlanner().plan(DesignBrief(prompt=family.prompt))
    result = compiler().compile(plan)

    assert errors(result) == []
    assert [step.primitive_or_macro for step in plan.steps] == list(family.macros)
    assert len(result.editable_regions) == len(plan.steps)
    for step in plan.steps:
        assert f"def {step.id}(state):" in result.source
        assert f"    state = {step.id}(state)" in result.source

    namespace = run_source(result.source)
    state = namespace["build_model"]()
    artifacts = export_into(namespace, state, tmp_path)

    opening = stub_cadquery.call_names(calls)[: len(family.opening_calls)]
    assert opening == list(family.opening_calls)
    assert [path for path in artifacts if not path.exists()] == []


@pytest.mark.parametrize(("prompt", "first_macro"), hostile_cases())
def test_a_hostile_dimension_never_reaches_cadquery_as_a_length(
    prompt: str, first_macro: str, monkeypatch
):
    # The stub raises on any non-positive or non-finite length, so this asserts
    # by running. It is the regression net for the clamps in cadquery_macros.py:
    # before them, "a project box 0.1 mm wide" emitted box(-0.1, ...) with a
    # clean diagnostic list.
    stub_cadquery.install(monkeypatch)
    plan = RuleBasedPlanner().plan(DesignBrief(prompt=prompt))
    result = compiler().compile(plan)

    assert errors(result) == []
    assert plan.steps[0].primitive_or_macro == first_macro

    namespace = run_source(result.source)
    assert namespace["build_model"]() is not None


@pytest.mark.parametrize(
    ("expression", "refused"),
    [
        # The call "a project box 0.1 mm wide" emitted before the walls were
        # clamped against the box they are cut from.
        ('cq.Workplane("XY").box(0.1 - (0.1 * 2), 80, 48)', True),
        ('cq.Workplane("XY").circle((0.1 / 2) - 0.1).extrude(10)', True),
        ('cq.Workplane("XY").circle(5).extrude(0.1 - 0.1)', True),
        # Shelling inward is what the negative thickness means, but a wall
        # clamped to nothing is still a wall that is not there.
        ('cq.Workplane("XY").circle(5).extrude(10).faces(">Z").shell(-0)', True),
        ('cq.Workplane("XY").circle(5).extrude(10).faces(">Z").shell(-4)', False),
    ],
)
def test_the_stub_refuses_the_lengths_the_clamps_exist_to_prevent(
    expression: str, refused: bool, monkeypatch
):
    # The sweep above asserts by running, so it is worth nothing if the stub
    # waves a bad length through. This is where that is checked.
    stub_cadquery.install(monkeypatch)
    namespace = run_source(
        f"import cadquery as cq\n\n\ndef build_model():\n    return {expression}\n"
    )

    if refused:
        with pytest.raises(stub_cadquery.NonPositiveLength):
            namespace["build_model"]()
    else:
        assert namespace["build_model"]() is not None


def test_the_stub_refuses_a_call_cadquery_does_not_have(monkeypatch):
    # The other half of the same worry. A stub that answers every name agrees
    # with source CadQuery would reject, and a misspelled length call also
    # drops out of LENGTH_CALLS, so the sweep above would stop checking it
    # without anything going red.
    stub_cadquery.install(monkeypatch)
    namespace = run_source(
        "import cadquery as cq\n\n\n"
        'def build_model():\n    return cq.Workplane("XY").box(10, 10, 10).hoel(2)\n'
    )

    with pytest.raises(AttributeError, match="hoel"):
        namespace["build_model"]()


def fillet_plan() -> SemanticBuildPlan:
    """A mug body with its rim rounded.

    ``fillet_edges`` is in the macro library and the rule-based planner has no
    path that emits it, so nothing else here would ever run its source.
    """
    return SemanticBuildPlan(
        summary="Extrude a mug body and round its rim.",
        steps=[
            SemanticStep(
                id="create_body",
                intent="Extrude the mug body",
                primitive_or_macro="create_mug_body",
                parameters={"outer_diameter": 86, "height": 96},
                postcondition="Body exists.",
            ),
            SemanticStep(
                id="round_rim",
                intent="Round the rim",
                primitive_or_macro="fillet_edges",
                parameters={"selector": ">Z", "radius": 2},
                depends_on=["create_body"],
                postcondition="Rim is rounded.",
            ),
        ],
    )


def manual_first_plan() -> SemanticBuildPlan:
    """A hand-modelled step ahead of a real one.

    ``manual_feature`` is the planner's escape hatch for shapes the library
    does not cover, and the rule-based planner never reaches for it either.
    """
    return SemanticBuildPlan(
        summary="Carve the spout by hand, then extrude the mug body.",
        steps=[
            SemanticStep(
                id="carve_spout",
                intent="Carve the spout by hand",
                primitive_or_macro="manual_feature",
                parameters={},
                postcondition="Spout carved in CAD.",
            ),
            SemanticStep(
                id="create_body",
                intent="Extrude the mug body",
                primitive_or_macro="create_mug_body",
                parameters={"outer_diameter": 86, "height": 96},
                postcondition="Body exists.",
            ),
        ],
    )


def test_fillet_edges_emits_source_that_runs(tmp_path, monkeypatch):
    calls = stub_cadquery.install(monkeypatch)

    result = compiler().compile(fillet_plan())

    assert errors(result) == []
    namespace = run_source(result.source)
    state = namespace["build_model"]()
    artifacts = export_into(namespace, state, tmp_path)

    assert ("edges", (">Z",), {}) in calls
    assert ("fillet", (2,), {}) in calls
    assert [path for path in artifacts if not path.exists()] == []


def test_a_manual_step_ahead_of_a_real_one_emits_source_that_runs(tmp_path, monkeypatch):
    calls = stub_cadquery.install(monkeypatch)

    result = compiler().compile(manual_first_plan())

    assert errors(result) == []
    assert [d.code for d in result.diagnostics if d.level == "warning"] == ["manual_step"]

    namespace = run_source(result.source)
    # The pass-through gets no geometry to work with and must not invent any.
    assert namespace["carve_spout"](None) is None
    state = namespace["build_model"]()
    artifacts = export_into(namespace, state, tmp_path)

    assert stub_cadquery.call_names(calls)[:2] == ["Workplane", "circle"]
    assert [path for path in artifacts if not path.exists()] == []


def drive_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brief: DesignBrief,
    plan: SemanticBuildPlan,
    source: str,
) -> dict[str, Any]:
    """Run the executor entry point on compiled source, in this process.

    The payload is the one ``CadQueryExecutor.execute`` writes before it spawns
    the subprocess. Building it here skips the subprocess - which would import
    the real cadquery and find nothing - without pretending the shape is
    different from what the executor sends.
    """
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    source_path = artifacts_dir / "compiled.py"
    source_path.write_text(source, encoding="utf-8")
    result_path = artifacts_dir / "executor-result.json"
    payload_path = artifacts_dir / "executor-payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "design_id": "d1",
                "brief": brief.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "source_path": str(source_path),
                "artifacts_dir": str(artifacts_dir),
                "cache_root": str(tmp_path / "cache"),
                "compiler_version": "v1",
                "dirty_from_step": None,
                "result_path": str(result_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["runtime", str(payload_path)])
    runtime.main()
    return json.loads(result_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("family", FAMILIES, ids=[family.name for family in FAMILIES])
def test_the_runtime_builds_every_family_from_compiled_source(
    family: Family, tmp_path, monkeypatch
):
    stub_cadquery.install(monkeypatch)
    # No target dimensions, so the runtime's height tolerance check stays out of
    # it. A stub can say a file was written and a volume was read; it has no
    # business claiming a mug came out 96 mm tall.
    brief = DesignBrief(prompt=family.prompt)
    plan = RuleBasedPlanner().plan(brief)
    result = compiler().compile(plan)

    payload = drive_runtime(tmp_path, monkeypatch, brief, plan, result.source)

    assert payload["status"] == "succeeded", payload.get("failure")
    assert payload["cache_hits"] == 0
    artifacts = payload["artifacts"]
    for kind in ("step_export_path", "stl_path", "glb_path"):
        assert Path(artifacts[kind]).exists(), kind
    # One cache entry per step, so a revision can reuse the steps it did not touch.
    assert len(list((tmp_path / "cache").glob("*/entry.json"))) == len(plan.steps)
