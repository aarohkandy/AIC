from app.models.schemas import DesignBrief, TargetDimensions
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
