from __future__ import annotations

import re
from typing import Any

from app.models.schemas import DesignBrief, SemanticBuildPlan, SemanticStep
from app.services.cadquery_macros import default_postcondition, macro_parameters_for_prompt


class RuleBasedPlanner:
    DIMENSION_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|in|inch|inches)?")

    def plan(self, brief: DesignBrief) -> SemanticBuildPlan:
        kind = self._infer_kind(brief.prompt)
        extracted = self._extract_parameters(brief)
        raw_steps = macro_parameters_for_prompt(kind, extracted)
        steps = [
            SemanticStep(
                id=raw_step["id"],
                intent=raw_step["intent"],
                primitive_or_macro=raw_step["primitive_or_macro"],
                parameters=raw_step["parameters"],
                depends_on=raw_step.get("depends_on", []),
                postcondition=default_postcondition(raw_step["primitive_or_macro"]),
            )
            for raw_step in raw_steps
        ]
        assumptions = self._assumptions(kind, brief, extracted)
        summary = self._summary(kind, steps)
        parameters = self._combine_parameters(steps)
        return SemanticBuildPlan(
            summary=summary,
            assumptions=assumptions,
            parameters=parameters,
            steps=steps,
        )

    def _infer_kind(self, prompt: str) -> str:
        text = prompt.lower()
        if "mug" in text or "cup" in text:
            return "mug"
        if "bracket" in text:
            return "l_bracket"
        if "box" in text or "enclosure" in text:
            return "project_box"
        if "stand" in text:
            return "phone_stand"
        return "bottle_cap"

    def _extract_parameters(self, brief: DesignBrief) -> dict[str, Any]:
        extracted: dict[str, Any] = {}
        if brief.target_dims.height is not None:
            extracted["height"] = brief.target_dims.height
        if brief.target_dims.width is not None:
            extracted["width"] = brief.target_dims.width
        if brief.target_dims.depth is not None:
            extracted["depth"] = brief.target_dims.depth
        if brief.target_dims.diameter is not None:
            extracted["diameter"] = brief.target_dims.diameter

        prompt = brief.prompt.lower()
        if "wall" in prompt:
            wall_match = self.DIMENSION_PATTERN.search(prompt[prompt.find("wall") :])
            if wall_match:
                extracted["wall_thickness"] = float(wall_match.group("value"))
        if "diameter" in prompt:
            diameter_match = self.DIMENSION_PATTERN.search(prompt[prompt.find("diameter") :])
            if diameter_match:
                extracted["diameter"] = float(diameter_match.group("value"))
        if "height" in prompt:
            height_match = self.DIMENSION_PATTERN.search(prompt[prompt.find("height") :])
            if height_match:
                extracted["height"] = float(height_match.group("value"))
        return extracted

    def _summary(self, kind: str, steps: list[SemanticStep]) -> str:
        if kind == "mug":
            return "Create the outer body, hollow it, and attach a handle so the user sees the mug emerge in stages."
        if kind == "l_bracket":
            return "Create the bracket profile first, then add the mounting holes as a second step."
        if kind == "project_box":
            return "Create the enclosure shell first, then add internal standoffs for mounting."
        if kind == "phone_stand":
            return "Create the stand silhouette first, then add the retention lip as a refinement."
        return "Create the cap body first, then add perimeter grip cutouts as the finishing step."

    def _assumptions(self, kind: str, brief: DesignBrief, extracted: dict[str, Any]) -> list[str]:
        assumptions = [f"Units default to {brief.units}."]
        if not extracted:
            assumptions.append("Prompt omitted some dimensions, so category defaults were applied.")
        if kind == "project_box":
            assumptions.append("Single-part enclosure body only; separate lids remain out of scope for v1.")
        if kind == "mug":
            assumptions.append("Handle is blocky and revision-friendly rather than ergonomic in v1.")
        return assumptions

    def _combine_parameters(self, steps: list[SemanticStep]) -> dict[str, Any]:
        combined: dict[str, Any] = {}
        for step in steps:
            combined.update(step.parameters)
        return combined
