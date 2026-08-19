import json

import httpx
import pytest

from app.core.settings import Settings
from app.models.schemas import DesignBrief, TargetDimensions
from app.services.planners.ollama_planner import OllamaPlanner, OllamaPlannerError
from app.services.planners.rule_based_planner import RuleBasedPlanner


def test_mug_prompt_generates_staged_plan() -> None:
    planner = RuleBasedPlanner()
    brief = DesignBrief(
        prompt="Design a mug with 86 mm diameter and 96 mm height.",
        target_dims=TargetDimensions(diameter=86, height=96),
    )

    plan = planner.plan(brief)

    assert len(plan.steps) == 3
    assert plan.steps[0].primitive_or_macro == "create_mug_body"
    assert plan.steps[1].depends_on == ["create_outer_body"]
    assert "mug" in plan.summary.lower()
    assert plan.steps[0].workplane == "XY"
    assert any("origin" in note.lower() for note in plan.steps[0].location_notes)
    assert any("diameter" in note.lower() for note in plan.steps[0].size_notes)
    assert plan.steps[2].sketch_constraints


def test_target_dims_survive_the_prompt_scan() -> None:
    planner = RuleBasedPlanner()
    brief = DesignBrief(
        prompt="Design a mug with an 86 mm diameter, 96 mm height, 4 mm walls, and a sturdy handle.",
        target_dims=TargetDimensions(diameter=86, height=96),
    )

    plan = planner.plan(brief)

    assert plan.parameters["outer_diameter"] == 86
    assert plan.parameters["height"] == 96
    assert plan.parameters["wall_thickness"] == 4


def test_dimensions_are_read_with_the_number_after_the_keyword() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug, diameter 86 mm, height 96 mm"))

    assert plan.parameters["outer_diameter"] == 86
    assert plan.parameters["height"] == 96


def test_dimensions_are_read_with_the_number_before_the_keyword() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug 86 mm across and 96 mm tall"))

    assert plan.parameters["outer_diameter"] == 86
    assert plan.parameters["height"] == 96


def test_box_dimensions_reach_the_macro_parameters() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(
        DesignBrief(prompt="a project box 200 mm wide, 150 mm deep, 60 mm high, 3 mm walls")
    )

    assert plan.parameters["width"] == 200
    assert plan.parameters["depth"] == 150
    assert plan.parameters["height"] == 60
    assert plan.parameters["wall_thickness"] == 3


def test_unrecognized_object_is_announced_in_the_assumptions() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a teapot which can hold 1 gallon"))

    fallback = [note for note in plan.assumptions if "teapot" in note]
    assert fallback, plan.assumptions
    assert "bottle cap" in fallback[0]
    assert plan.steps, "the fallback recipe should still be usable"


def test_category_keywords_match_whole_words_only() -> None:
    planner = RuleBasedPlanner()

    assert planner.infer_kind("a capacitor mount") is None
    assert planner.infer_kind("a standard M3 washer") is None
    assert planner.infer_kind("boxes for resistors") == "project_box"
    assert planner.infer_kind("two mugs") == "mug"


def test_recognized_object_says_nothing_about_a_fallback() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug for camping"))

    assert not any("no macro family" in note.lower() for note in plan.assumptions)


def test_a_keyword_does_not_steal_the_next_clause_number() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug with a thick wall 96 mm tall"))

    assert plan.parameters["height"] == 96
    # 96 belongs to the height, so the wall falls back to the category default.
    assert plan.parameters["wall_thickness"] == 4


def test_the_idiomatic_in_diameter_phrasing_is_read() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug 86 mm in diameter and 96 mm in height"))

    assert plan.parameters["outer_diameter"] == 86
    assert plan.parameters["height"] == 96


def test_inches_are_converted_to_millimetres() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug 4 in tall"))

    assert plan.parameters["height"] == 101.6
    assert any("converted to millimetres" in note for note in plan.assumptions)


def test_a_wall_too_thick_for_the_body_is_clamped_and_announced() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a project box 20 mm wide with 15 mm walls"))

    wall = plan.parameters["wall_thickness"]
    assert wall < plan.parameters["width"] / 2, "the cavity would be inside out"
    clamp_notes = [note for note in plan.assumptions if "reduced to" in note]
    assert any("Wall thickness of 15 mm" in note for note in clamp_notes), plan.assumptions


def test_bracket_thickness_shrinks_to_fit_short_arms() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a bracket 10 mm wide and 10 mm high"))

    assert plan.parameters["thickness"] < plan.parameters["arm_width"]
    assert plan.parameters["thickness"] < plan.parameters["arm_height"]


def test_a_dimension_the_recipe_cannot_use_is_called_out() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug 120 mm wide and 96 mm tall"))

    assert plan.parameters["outer_diameter"] == 86
    assert any("no width parameter" in note for note in plan.assumptions), plan.assumptions


def test_an_unrecognized_object_is_flagged_in_the_summary_too() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a teapot which can hold 1 gallon"))

    assert plan.summary.startswith("No macro family matches")


def test_in_as_a_preposition_is_not_read_as_inches() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug 86 in diameter"))

    assert plan.parameters["outer_diameter"] == 86
    assert not any("converted" in note for note in plan.assumptions)


def test_the_briefs_unit_selector_converts_the_form_dimensions() -> None:
    planner = RuleBasedPlanner()
    brief = DesignBrief(
        prompt="a mug",
        units="in",
        target_dims=TargetDimensions(diameter=3.5, height=4),
    )

    plan = planner.plan(brief)

    assert plan.parameters["outer_diameter"] == 88.9
    assert plan.parameters["height"] == 101.6
    assert any("inches" in note for note in plan.assumptions), plan.assumptions


def test_the_plan_says_which_unit_its_numbers_are_in() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug", units="cm"))

    assert plan.assumptions[0] == "Every dimension in this plan is in millimetres."


def test_an_unqualified_prompt_number_takes_the_briefs_unit() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug 8.6 across and 9.6 tall", units="cm"))

    assert plan.parameters["outer_diameter"] == 86
    assert plan.parameters["height"] == 96


def test_a_dimension_that_cannot_be_built_falls_back_to_the_default() -> None:
    planner = RuleBasedPlanner()
    brief = DesignBrief(prompt="a mug", target_dims=TargetDimensions(diameter=-5, height=96))

    plan = planner.plan(brief)

    assert plan.parameters["outer_diameter"] == 86
    assert plan.parameters["height"] == 96
    assert any("cannot be built" in note for note in plan.assumptions), plan.assumptions


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a_dimension_that_is_not_a_finite_number_falls_back_too(value: float) -> None:
    # JSON carries bare NaN and Infinity literals and pydantic accepts them, so
    # both reach the planner. NaN is the dangerous one: it compares False
    # against everything, so it used to pass the positivity check and then blow
    # up in the clamp's log10.
    planner = RuleBasedPlanner()
    brief = DesignBrief(prompt="a mug", target_dims=TargetDimensions(diameter=value, height=96))

    plan = planner.plan(brief)

    assert plan.parameters["outer_diameter"] == 86
    assert plan.parameters["height"] == 96
    assert any("cannot be built" in note for note in plan.assumptions), plan.assumptions


def test_a_trailing_thick_still_belongs_to_the_wall_it_follows() -> None:
    planner = RuleBasedPlanner()

    extracted, _ = planner._extract_parameters(
        DesignBrief(prompt="a project box with walls 3 mm thick and 200 mm wide")
    )

    assert extracted == {"width": 200.0, "wall_thickness": 3.0}


def test_a_trailing_tall_still_steals_the_number_back_from_the_wall() -> None:
    planner = RuleBasedPlanner()

    extracted, _ = planner._extract_parameters(DesignBrief(prompt="a thick wall 96 mm tall"))

    assert extracted == {"height": 96.0}


def test_a_wall_too_thick_for_the_box_is_clamped_under_the_ceiling() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a project box 20 mm wide with 15 mm walls"))

    shell = next(
        step for step in plan.steps if step.primitive_or_macro == "create_project_box_shell"
    )
    wall = shell.parameters["wall_thickness"]
    assert 0 < wall <= 0.4 * 20
    assert any("reduced to" in note for note in plan.assumptions), plan.assumptions


def test_a_hostile_dimension_never_produces_a_negative_feature() -> None:
    planner = RuleBasedPlanner()

    for prompt in (
        "a project box 0.1 mm wide",
        "a bottle cap 0.1 mm across and 0.1 mm tall",
        "a mug 0.2 mm across and 0.2 mm tall",
    ):
        plan = planner.plan(DesignBrief(prompt=prompt))
        sizes = [
            (step.id, key, value)
            for step in plan.steps
            for key, value in step.parameters.items()
            if isinstance(value, (int, float))
        ]
        assert sizes
        assert all(value > 0 for _, _, value in sizes), (prompt, sizes)


class _StubResponse:
    """An HTTP 200 that is not JSON, which is what a wrong port answers with."""

    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        raise json.JSONDecodeError("Expecting value", "<html>", 0)


def test_a_non_json_reply_from_the_ollama_port_falls_back_instead_of_500ing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # json.JSONDecodeError is not an httpx.HTTPError, so it used to escape the
    # gateway's except clause and surface as a bare 500 on /designs/plan.
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _StubResponse())
    planner = OllamaPlanner(Settings())

    with pytest.raises(OllamaPlannerError) as caught:
        planner.plan(DesignBrief(prompt="a mug"))

    assert "non-JSON" in str(caught.value)
