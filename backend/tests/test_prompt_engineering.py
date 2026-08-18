from app.models.schemas import DesignBrief
from app.services.cadquery_macros import SUPPORTED_MACROS, macro_parameter_names
from app.services.planners.prompt_engineering import build_local_planner_prompt


def test_local_prompt_requests_location_size_and_constraints() -> None:
    brief = DesignBrief(prompt="Make a mug with a handle.")

    prompt = build_local_planner_prompt(brief)

    assert "location_notes" in prompt
    assert "size_notes" in prompt
    assert "sketch_constraints" in prompt
    assert "fully define" in prompt.lower()
    assert "manual_feature" in prompt


def test_local_prompt_offers_every_macro_the_compiler_implements() -> None:
    # The prompt list used to be hand-maintained and drifted out of sync with the
    # macro library, so the planner was told about macros that did not compile
    # and never told about fillet_edges.
    prompt = build_local_planner_prompt(DesignBrief(prompt="Make a mug with a handle."))

    missing = [macro for macro in sorted(SUPPORTED_MACROS) if f"- {macro}:" not in prompt]
    assert missing == []


def test_local_prompt_names_the_parameters_each_macro_reads() -> None:
    # Naming a macro the planner cannot parameterize is the same build failure as
    # naming a macro that does not exist: the compiler reports missing_parameter
    # and drops the step.
    prompt = build_local_planner_prompt(DesignBrief(prompt="Make a phone stand."))

    for macro in sorted(SUPPORTED_MACROS):
        line = next(row for row in prompt.splitlines() if row.startswith(f"- {macro}:"))
        for parameter in sorted(macro_parameter_names(macro)):
            assert parameter in line, f"{macro} prompt line omits {parameter}"


def test_a_non_millimetre_brief_still_asks_for_millimetre_parameters() -> None:
    # The compiler and the macros read every parameter as millimetres, so a
    # prompt that told the model to work in the brief's own unit turned a
    # centimetre brief into a silent 10x error.
    prompt = build_local_planner_prompt(DesignBrief(prompt="a mug", units="cm"))

    assert "Every parameter value is in millimetres" in prompt
    assert "convert those figures to millimetres" in prompt
