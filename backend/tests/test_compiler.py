import sys
import types
from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any

import pytest

from app.models.schemas import CompileResult, SemanticBuildPlan, SemanticStep
from app.services.compilers.cadquery_compiler import CadQueryCompiler
from app.services.validation.source_validator import SourceValidator


class RecordingShape:
    """Stands in for a cq.Workplane and records the chained calls made on it."""

    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self.calls = calls

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> "RecordingShape":
            self.calls.append((name, args))
            return self

        return record


@contextmanager
def stub_cadquery():
    """Install a fake ``cadquery`` module so generated source can be executed.

    CadQuery itself is a conda-only dependency, so these tests check that the
    emitted source runs and calls what it says it calls, not that the geometry
    is correct.
    """
    calls: list[tuple[str, tuple[Any, ...]]] = []
    module = types.ModuleType("cadquery")

    def workplane(plane: str) -> RecordingShape:
        calls.append(("Workplane", (plane,)))
        return RecordingShape(calls)

    module.Workplane = workplane  # type: ignore[attr-defined]
    previous = sys.modules.get("cadquery")
    sys.modules["cadquery"] = module
    try:
        yield calls
    finally:
        if previous is None:
            del sys.modules["cadquery"]
        else:
            sys.modules["cadquery"] = previous


def make_step(
    step_id: str,
    macro: str,
    parameters: dict[str, Any],
    depends_on: Sequence[str] = (),
) -> SemanticStep:
    return SemanticStep(
        id=step_id,
        intent=f"Step {step_id}",
        primitive_or_macro=macro,
        parameters=parameters,
        depends_on=list(depends_on),
        postcondition=f"{step_id} done",
    )


def make_plan(*steps: SemanticStep) -> SemanticBuildPlan:
    return SemanticBuildPlan(summary="Test plan.", steps=list(steps))


@pytest.fixture
def compiler() -> CadQueryCompiler:
    return CadQueryCompiler(SourceValidator())


def errors(result: CompileResult) -> list[str]:
    return [diagnostic.code for diagnostic in result.diagnostics if diagnostic.level == "error"]


def warnings(result: CompileResult) -> list[str]:
    return [diagnostic.code for diagnostic in result.diagnostics if diagnostic.level == "warning"]


def test_compiler_returns_step_functions_and_regions(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 80, "height": 90})
    )

    result = compiler.compile(plan)

    assert "def create_outer_body" in result.source
    compile(result.source, "<generated>", "exec")
    assert result.editable_regions[0].step_id == "create_outer_body"
    assert result.whitelist_findings


def test_manual_feature_compiles_to_a_pass_through(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 86, "height": 96}),
        make_step("carve_spout", "manual_feature", {}),
    )

    result = compiler.compile(plan)

    assert errors(result) == []
    assert "    state = create_outer_body(state)" in result.source
    assert "    state = carve_spout(state)" in result.source
    manual = [d for d in result.diagnostics if d.code == "manual_step"]
    assert len(manual) == 1
    assert manual[0].level == "warning"
    assert "carve_spout" in manual[0].message
    assert {region.step_id for region in result.editable_regions} == {
        "create_outer_body",
        "carve_spout",
    }

    with stub_cadquery() as calls:
        namespace: dict[str, Any] = {}
        exec(compile(result.source, "<generated>", "exec"), namespace)
        state = namespace["build_model"]()

    assert namespace["carve_spout"]("unchanged") == "unchanged"
    assert isinstance(state, RecordingShape)
    assert ("circle", (43.0,)) in calls
    assert ("extrude", (96,)) in calls


def test_unknown_macro_is_an_error_and_emits_no_call(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(make_step("make_teapot", "create_teapot", {"volume": 3785}))

    result = compiler.compile(plan)

    assert errors(result) == ["unsupported_macro", "no_compiled_steps"]
    assert "make_teapot(state)" not in result.source
    compile(result.source, "<generated>", "exec")


def test_build_model_only_calls_emitted_functions(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 86, "height": 96}),
        make_step("engrave_logo", "emboss_text", {"depth": 1}),
    )

    result = compiler.compile(plan)

    assert "engrave_logo" not in result.source
    with stub_cadquery():
        namespace: dict[str, Any] = {}
        exec(compile(result.source, "<generated>", "exec"), namespace)
        assert namespace["build_model"]() is not None


def test_non_identifier_step_id_is_reported_by_name(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("1. make body", "create_mug_body", {"outer_diameter": 86, "height": 96})
    )

    result = compiler.compile(plan)

    invalid = [d for d in result.diagnostics if d.code == "invalid_step_id"]
    assert len(invalid) == 1
    assert "1. make body" in invalid[0].message
    compile(result.source, "<generated>", "exec")


def test_step_id_may_not_shadow_the_generated_driver(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step("build_model", "create_mug_body", {"outer_diameter": 86, "height": 96})
    )

    result = compiler.compile(plan)

    assert "reserved_step_id" in errors(result)
    with stub_cadquery():
        namespace: dict[str, Any] = {}
        exec(compile(result.source, "<generated>", "exec"), namespace)
        assert namespace["build_model"]() is None


def test_duplicate_step_id_is_rejected(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 86, "height": 96}),
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 40, "height": 20}),
    )

    result = compiler.compile(plan)

    assert errors(result) == ["duplicate_step_id"]
    assert result.source.count("def create_outer_body") == 1
    assert result.source.count("state = create_outer_body(state)") == 1


def test_quoted_dimension_is_coerced_to_a_number(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step(
            "create_outer_body",
            "create_mug_body",
            {"outer_diameter": "86 mm", "height": 96},
        )
    )

    result = compiler.compile(plan)

    assert errors(result) == []
    assert "outer_radius = 86.0 / 2" in result.source


def test_non_numeric_parameter_names_the_step_and_key(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step(
            "create_outer_body",
            "create_mug_body",
            {"outer_diameter": "wide", "height": 96},
        )
    )

    result = compiler.compile(plan)

    problems = [d for d in result.diagnostics if d.code == "non_numeric_parameter"]
    assert len(problems) == 1
    assert "create_outer_body" in problems[0].message
    assert "outer_diameter" in problems[0].message
    assert "def create_outer_body" not in result.source


def test_fillet_selector_stays_a_string(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 86, "height": 96}),
        make_step("round_rim", "fillet_edges", {"selector": ">Z", "radius": 2}),
    )

    result = compiler.compile(plan)

    assert errors(result) == []
    assert "state.edges('>Z').fillet(2)" in result.source


def test_missing_parameter_skips_the_step_entirely(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 86, "height": 96}),
        make_step("hollow_body", "hollow_mug_body", {}),
    )

    result = compiler.compile(plan)

    assert errors(result) == ["missing_parameter"]
    assert "hollow_body(state)" not in result.source
    with stub_cadquery():
        namespace: dict[str, Any] = {}
        exec(compile(result.source, "<generated>", "exec"), namespace)
        assert namespace["build_model"]() is not None


def test_misnamed_parameters_are_reported_together_with_what_the_macro_wants(
    compiler: CadQueryCompiler,
) -> None:
    # A planner that guesses "diameter" for "outer_diameter" should learn the
    # whole naming scheme from one compile, not one key per build attempt.
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"diameter": 86, "tall": 96})
    )

    result = compiler.compile(plan)

    missing = [d for d in result.diagnostics if d.code == "missing_parameter"]
    assert len(missing) == 1
    assert "create_outer_body" in missing[0].message
    assert "does not supply height, outer_diameter" in missing[0].message
    assert "needs height, outer_diameter" in missing[0].message


def test_fillet_selector_that_is_not_a_selector_is_reported(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": 86, "height": 96}),
        make_step("round_rim", "fillet_edges", {"selector": 2, "radius": 2}),
    )

    result = compiler.compile(plan)

    problems = [d for d in result.diagnostics if d.code == "non_text_parameter"]
    assert len(problems) == 1
    assert "round_rim" in problems[0].message
    assert "state.edges(2)" not in result.source


def test_parameter_the_macro_never_reads_is_dropped_not_rejected(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step(
            "create_outer_body",
            "create_mug_body",
            {"outer_diameter": 86, "height": 96, "material": "PLA"},
        )
    )

    result = compiler.compile(plan)

    assert errors(result) == []
    assert "outer_radius = 86 / 2" in result.source
    assert "PLA" not in result.source


def test_a_unit_that_is_not_millimetres_is_not_silently_dropped(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step(
            "create_outer_body",
            "create_mug_body",
            {"outer_diameter": "8.6 cm", "height": 96},
        )
    )

    result = compiler.compile(plan)

    assert "non_numeric_parameter" in errors(result)
    assert "8.6 cm" in "".join(d.message for d in result.diagnostics)


def test_syntax_errors_carry_the_line_number() -> None:
    findings = SourceValidator().validate("def body(state):\n    return cq.Workplane(\n")

    assert len(findings) == 1
    assert "line 2" in findings[0].message


def test_infinite_parameter_is_reported_not_emitted(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step(
            "create_outer_body",
            "create_mug_body",
            {"outer_diameter": float("inf"), "height": 96},
        )
    )

    result = compiler.compile(plan)

    assert "non_numeric_parameter" in errors(result)
    assert "inf" not in result.source
    message = next(
        diagnostic.message
        for diagnostic in result.diagnostics
        if diagnostic.code == "non_numeric_parameter"
    )
    assert "create_outer_body" in message and "outer_diameter" in message


def test_step_ids_that_normalize_to_the_same_name_are_a_duplicate(
    compiler: CadQueryCompiler,
) -> None:
    # Python NFKC-normalizes identifiers when it parses them, so "ﬁx" and "fix"
    # are two spellings of one function name.
    plan = make_plan(
        make_step("ﬁx", "create_mug_body", {"outer_diameter": 86, "height": 96}),
        make_step(
            "fix",
            "create_bottle_cap",
            {
                "outer_diameter": 34,
                "height": 20,
                "wall_thickness": 2.4,
                "top_thickness": 3,
            },
        ),
    )

    result = compiler.compile(plan)

    assert "duplicate_step_id" in errors(result)
    assert result.source.count("state = ") == 2


def test_a_syntax_error_names_the_line_it_is_on() -> None:
    findings = SourceValidator().validate("def f():\n    pass\nreturn 1\n")

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "line 3" in findings[0].message


# The rule-based planner emits its steps in dependency order, so the driver
# could get away with plan order. The default planner is the local model, and a
# model will happily list a hollow ahead of the body it hollows.

MUG_BODY = {"outer_diameter": 86, "height": 96}
MUG_HANDLE = {
    "outer_diameter": 86,
    "handle_width": 28,
    "handle_span": 46.08,
    "handle_thickness": 12,
    "offset": 24,
    "z_center": 49.92,
}


def driver_calls(result: CompileResult) -> list[str]:
    body = result.source.split("def build_model():\n", 1)[1]
    return [line.strip() for line in body.splitlines() if line.startswith("    state = ")]


def test_steps_are_called_in_dependency_order_not_plan_order(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step("hollow_body", "hollow_mug_body", {"wall_thickness": 4}, ["create_outer_body"]),
        make_step("create_outer_body", "create_mug_body", MUG_BODY),
    )

    result = compiler.compile(plan)

    assert errors(result) == []
    assert driver_calls(result) == [
        "state = None",
        "state = create_outer_body(state)",
        "state = hollow_body(state)",
    ]
    with stub_cadquery() as calls:
        namespace: dict[str, Any] = {}
        exec(compile(result.source, "<generated>", "exec"), namespace)
        state = namespace["build_model"]()

    assert isinstance(state, RecordingShape)
    # The body has to exist before anything shells it. Called the other way
    # round this raises AttributeError on None instead.
    assert [name for name, _ in calls] == ["Workplane", "circle", "extrude", "faces", "shell"]


def test_a_plan_already_in_order_emits_the_source_it_always_did(
    compiler: CadQueryCompiler,
) -> None:
    steps = [
        ("create_outer_body", "create_mug_body", MUG_BODY, []),
        ("hollow_body", "hollow_mug_body", {"wall_thickness": 4}, ["create_outer_body"]),
        ("add_handle", "add_mug_handle", MUG_HANDLE, ["hollow_body"]),
    ]
    declared = make_plan(*(make_step(i, macro, params, deps) for i, macro, params, deps in steps))
    ignored = make_plan(*(make_step(i, macro, params) for i, macro, params, _ in steps))

    result = compiler.compile(declared)
    assert errors(result) == []
    assert len(driver_calls(result)) == 4
    assert result.source == compiler.compile(ignored).source


def test_a_dependency_the_plan_does_not_build_is_named_but_does_not_block(
    compiler: CadQueryCompiler,
) -> None:
    plan = make_plan(
        make_step("hollow_body", "hollow_mug_body", {"wall_thickness": 4}, ["create_outer_body"])
    )

    result = compiler.compile(plan)

    assert errors(result) == []
    assert warnings(result) == ["unknown_dependency"]
    unknown = next(d for d in result.diagnostics if d.code == "unknown_dependency")
    assert unknown.step_id == "hollow_body"
    assert "create_outer_body" in unknown.message


def test_a_dependency_written_as_a_label_still_builds_the_plan(
    compiler: CadQueryCompiler,
) -> None:
    # Nothing makes the local planner write ids in depends_on, and it is not
    # asked for a particular order either. A model that lists the steps
    # correctly and then describes the dependency instead of naming it used to
    # build; refusing that plan outright would be a worse trade than ignoring
    # the edge it could not resolve.
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", MUG_BODY),
        make_step("hollow_body", "hollow_mug_body", {"wall_thickness": 4}, ["create outer body"]),
    )

    result = compiler.compile(plan)

    assert errors(result) == []
    assert driver_calls(result) == [
        "state = None",
        "state = create_outer_body(state)",
        "state = hollow_body(state)",
    ]
    with stub_cadquery() as calls:
        namespace: dict[str, Any] = {}
        exec(compile(result.source, "<generated>", "exec"), namespace)
        namespace["build_model"]()

    assert [name for name, _ in calls] == ["Workplane", "circle", "extrude", "faces", "shell"]


def test_a_dependency_on_a_step_that_failed_to_compile_is_named_too(
    compiler: CadQueryCompiler,
) -> None:
    # Skipping the body is already an error, but the plan is now short a step
    # that another step was counting on, and saying so is what stops the
    # executor from blaming hollow_body for the missing geometry.
    plan = make_plan(
        make_step("create_outer_body", "create_teapot", MUG_BODY),
        make_step("hollow_body", "hollow_mug_body", {"wall_thickness": 4}, ["create_outer_body"]),
    )

    result = compiler.compile(plan)

    assert errors(result) == ["unsupported_macro"]
    assert warnings(result) == ["unknown_dependency"]


def test_a_dependency_cycle_is_refused_by_name(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", MUG_BODY, ["hollow_body"]),
        make_step("hollow_body", "hollow_mug_body", {"wall_thickness": 4}, ["create_outer_body"]),
    )

    result = compiler.compile(plan)

    assert errors(result) == ["dependency_cycle"]
    cycle = next(d for d in result.diagnostics if d.code == "dependency_cycle")
    assert cycle.step_id == "create_outer_body"
    assert "create_outer_body, hollow_body" in cycle.message
    # Both steps were emitted, so both stay in the driver. Nothing runs it: the
    # error blocks the build before the executor sees the module.
    compile(result.source, "<generated>", "exec")
    assert len(driver_calls(result)) == 3


def test_a_diagnostic_about_one_step_carries_its_id(compiler: CadQueryCompiler) -> None:
    plan = make_plan(
        make_step("create_outer_body", "create_mug_body", {"outer_diameter": "wide", "height": 96}),
        make_step("carve_spout", "manual_feature", {}),
    )

    result = compiler.compile(plan)

    step_ids = {diagnostic.code: diagnostic.step_id for diagnostic in result.diagnostics}
    assert step_ids["non_numeric_parameter"] == "create_outer_body"
    assert step_ids["manual_step"] == "carve_spout"
    # step_count is about the plan, not about any one step.
    assert step_ids["step_count"] is None
