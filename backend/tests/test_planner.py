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


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("a mug 86 mm across and 96 mm tall", {"diameter": 86.0, "height": 96.0}),
        ("a mug with a 86mm diameter", {"diameter": 86.0}),
        ("a mug 8.6 cm across", {"diameter": 86.0}),
        ("a mug 3 inches across", {"diameter": 76.2}),
        ("a phone stand 80 mm wide, 60 mm deep", {"width": 80.0, "depth": 60.0}),
        ("a project box 200mm wide", {"width": 200.0}),
    ],
)
def test_the_phrasings_that_already_worked_still_work(
    prompt: str, expected: dict[str, float]
) -> None:
    # Written-out units and the NxNxN chain widened these patterns, and a scan
    # that learns a new phrasing by dropping an old one is not an improvement.
    planner = RuleBasedPlanner()

    extracted, _ = planner._extract_parameters(DesignBrief(prompt=prompt))

    assert extracted == expected


def test_a_chain_of_three_numbers_becomes_the_three_box_axes() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a project box 200x150x60 mm with 3 mm walls"))

    assert plan.parameters["width"] == 200
    assert plan.parameters["depth"] == 150
    assert plan.parameters["height"] == 60
    assert plan.parameters["wall_thickness"] == 3


def test_the_axis_order_of_a_chain_is_stated_as_the_guess_it_is() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a project box 200x150x60 mm"))

    assert 'Read "200x150x60" as width x depth x height.' in plan.assumptions


@pytest.mark.parametrize(
    "prompt",
    [
        "a project box 200x150x60 mm",
        "an enclosure 200 x 150 x 60 mm",
        "a project box 200X150X60mm",
        "an enclosure of 200x150x60",
    ],
)
def test_a_chain_is_read_through_spacing_case_and_a_missing_unit(prompt: str) -> None:
    planner = RuleBasedPlanner()

    extracted, _ = planner._extract_parameters(DesignBrief(prompt=prompt))

    assert extracted == {"width": 200.0, "depth": 150.0, "height": 60.0}


def test_a_two_number_chain_is_read_as_a_footprint() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a project box 200x150 mm"))

    assert plan.parameters["width"] == 200
    assert plan.parameters["depth"] == 150
    assert 'Read "200x150" as width x depth.' in plan.assumptions
    # Nothing said how tall it is, and the plan admits that rather than
    # borrowing a number from the footprint.
    assert any("did not state" in note and "height" in note for note in plan.assumptions)


def test_a_chains_unit_applies_to_every_number_in_it() -> None:
    planner = RuleBasedPlanner()

    extracted, notes = planner._extract_parameters(DesignBrief(prompt="a project box 20x15x6 cm"))

    assert extracted == {"width": 200.0, "depth": 150.0, "height": 60.0}
    assert "Dimensions given in centimetres were converted to millimetres." in notes


def test_a_chain_of_four_numbers_is_refused_rather_than_guessed_at() -> None:
    # Three numbers are a box. Four are something this scan does not understand,
    # and reading the first three of them would be an invention.
    planner = RuleBasedPlanner()

    extracted, notes = planner._extract_parameters(
        DesignBrief(prompt="a project box 200x150x60x40 mm")
    )

    assert extracted == {}
    assert notes == []


def test_a_part_number_is_not_a_size() -> None:
    planner = RuleBasedPlanner()

    extracted, _ = planner._extract_parameters(
        DesignBrief(prompt="a project box with m3x10 screws")
    )

    assert extracted == {}


def test_a_named_axis_beats_the_chains_positional_guess() -> None:
    planner = RuleBasedPlanner()

    extracted, notes = planner._extract_parameters(
        DesignBrief(prompt="a project box 200x150 mm, 300 mm wide")
    )

    assert extracted == {"width": 300.0, "depth": 150.0}
    # The 200 was thrown away, so the note may not say it was read as the width.
    assert notes == ['Read 150 as depth out of "200x150"; width came from the rest of the request.']


def test_the_chain_note_names_only_the_axes_the_chain_filled() -> None:
    # The note exists to own up to a positional guess. Claiming a guess that did
    # not happen is worse than not printing the line at all.
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a project box 200x150x60 mm, 80 mm tall"))

    assert plan.parameters["height"] == 80
    assert (
        'Read 200 as width and 150 as depth out of "200x150x60"; '
        "height came from the rest of the request." in plan.assumptions
    )
    assert not any("as width x depth x height" in note for note in plan.assumptions)


@pytest.mark.parametrize(
    "prompt",
    [
        "a bracket with 2x4 mounting holes",
        "a project box with a 3x3 grid of standoffs",
    ],
)
def test_two_numbers_around_an_x_are_only_a_size_when_a_unit_says_so(prompt: str) -> None:
    # "2x4 mounting holes" is a count. Reading it as millimetres planned a 2 mm
    # bracket, which the repair pass then shrank three more times trying to make
    # it printable.
    planner = RuleBasedPlanner()

    extracted, notes = planner._extract_parameters(DesignBrief(prompt=prompt))

    assert extracted == {}
    assert notes == []


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("a mug 86 millimeters across", {"diameter": 86.0}),
        ("a mug 86 millimetres across", {"diameter": 86.0}),
        ("a mug 8.6 centimeters across", {"diameter": 86.0}),
        ("a mug 8.6 centimetre across", {"diameter": 86.0}),
        (
            "a mug 86 millimeters across and 96 millimetres tall",
            {"diameter": 86.0, "height": 96.0},
        ),
    ],
)
def test_a_unit_spelled_out_reads_the_same_as_its_abbreviation(
    prompt: str, expected: dict[str, float]
) -> None:
    planner = RuleBasedPlanner()

    extracted, _ = planner._extract_parameters(DesignBrief(prompt=prompt))

    assert extracted == expected


def test_a_spelled_out_unit_is_named_in_the_conversion_note() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a mug 8.6 centimeters across"))

    assert plan.parameters["outer_diameter"] == 86
    assert "Dimensions given in centimetres were converted to millimetres." in plan.assumptions


@pytest.mark.parametrize(
    "prompt",
    ["a bottle cap with 2.5 mm thickness", "a bottle cap, thickness 2.5 mm"],
)
def test_a_bare_thickness_is_the_wall_thickness(prompt: str) -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt=prompt))

    assert plan.parameters["wall_thickness"] == 2.5


def test_a_radius_is_doubled_into_the_diameter_the_recipes_read() -> None:
    planner = RuleBasedPlanner()

    plan = planner.plan(DesignBrief(prompt="a bottle cap with a 43 mm radius"))

    assert plan.parameters["outer_diameter"] == 86
    assert "Read a radius of 43 mm as a diameter of 86 mm." in plan.assumptions


def test_a_stated_diameter_leaves_the_radius_alone() -> None:
    planner = RuleBasedPlanner()

    extracted, notes = planner._extract_parameters(
        DesignBrief(prompt="a bottle cap 30 mm across, radius 15 mm")
    )

    assert extracted == {"diameter": 30.0}
    assert notes == []


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
