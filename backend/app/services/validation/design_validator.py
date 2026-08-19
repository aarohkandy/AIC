from __future__ import annotations

from typing import Any

from app.models.schemas import DesignBrief, SemanticBuildPlan


class DesignValidator:
    """Heuristics for planning risk and plan quality.

    Scores how risky a brief is to plan, and reports warnings about the plan
    that comes back: a step missing its workplane, notes or sketch
    constraints, a placeholder anywhere in a step, and missing numeric
    parameters on the three mug macros.
    """

    def planning_risk_score(self, brief: DesignBrief) -> float:
        prompt = brief.prompt.lower()
        risk = 0.0
        if not any(value is not None for value in brief.target_dims.model_dump().values()):
            risk += 0.15
        if any(word in prompt for word in ("assembly", "multiple parts", "hinge", "joint", "fit")):
            risk += 0.3
        if any(word in prompt for word in ("maybe", "roughly", "something like")):
            risk += 0.15
        if "and" in prompt and any(
            word in prompt for word in ("mug", "bracket", "box", "cap", "stand")
        ):
            risk += 0.1
        return round(min(risk, 1.0), 2)

    def plan_quality_warnings(self, plan: SemanticBuildPlan) -> list[str]:
        warnings: list[str] = []
        for step in plan.steps:
            if not step.workplane:
                warnings.append(f"Step {step.id} is missing a workplane.")
            if not step.location_notes:
                warnings.append(f"Step {step.id} is missing location notes.")
            if not step.size_notes:
                warnings.append(f"Step {step.id} is missing size notes.")
            if not step.sketch_constraints:
                warnings.append(f"Step {step.id} is missing sketch constraints.")

            if self._contains_placeholder(step.parameters) or self._contains_placeholder(
                step.location_notes
                + step.size_notes
                + step.sketch_constraints
                + step.manual_instructions
            ):
                warnings.append(f"Step {step.id} contains placeholders instead of concrete values.")

            # Only the three mug macros have a required-parameter list here. A
            # step naming any of the other nine gets the structural checks above
            # and no parameter checking at all.
            if step.primitive_or_macro == "create_mug_body":
                required = ("outer_diameter", "height")
                missing = [
                    key
                    for key in required
                    if not isinstance(step.parameters.get(key), (int, float))
                ]
                if missing:
                    warnings.append(
                        f"Step {step.id} is missing numeric mug body parameters: {', '.join(missing)}."
                    )
            if step.primitive_or_macro == "hollow_mug_body":
                if not isinstance(step.parameters.get("wall_thickness"), (int, float)):
                    warnings.append(f"Step {step.id} is missing numeric wall_thickness.")
            if step.primitive_or_macro == "add_mug_handle":
                required = ("handle_width", "handle_span", "handle_thickness", "offset", "z_center")
                missing = [
                    key
                    for key in required
                    if not isinstance(step.parameters.get(key), (int, float))
                ]
                if missing:
                    warnings.append(
                        f"Step {step.id} is missing numeric handle parameters: {', '.join(missing)}."
                    )
        return warnings

    @staticmethod
    def _contains_placeholder(value: Any) -> bool:
        if isinstance(value, dict):
            return any(DesignValidator._contains_placeholder(item) for item in value.values())
        if isinstance(value, list):
            return any(DesignValidator._contains_placeholder(item) for item in value)
        if isinstance(value, str):
            return any(token in value for token in ("{{", "}}", "<", ">", "TBD"))
        return False
