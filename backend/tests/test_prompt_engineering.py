from app.models.schemas import DesignBrief
from app.services.planners.prompt_engineering import build_local_planner_prompt


def test_local_prompt_requests_location_size_and_constraints() -> None:
    brief = DesignBrief(prompt="Make a mug with a handle.")

    prompt = build_local_planner_prompt(brief)

    assert "location_notes" in prompt
    assert "size_notes" in prompt
    assert "sketch_constraints" in prompt
    assert "fully define" in prompt.lower()
    assert "manual_feature" in prompt
