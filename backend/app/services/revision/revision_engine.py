from __future__ import annotations

import re
from copy import deepcopy

from app.models.schemas import PlanPatch, RevisionIntent, SemanticBuildPlan


PARAMETER_ALIASES = {
    "handle thickness": "handle_thickness",
    "handle width": "handle_width",
    "wall thickness": "wall_thickness",
    "height": "height",
    "diameter": "outer_diameter",
    "width": "width",
    "depth": "depth",
}


class RevisionEngine:
    VALUE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)")

    def interpret(self, instruction: str, plan: SemanticBuildPlan) -> tuple[RevisionIntent, PlanPatch | None]:
        lowered = instruction.lower().strip()
        topology_change = any(word in lowered for word in ("add ", "remove ", "turn into", "convert"))
        matched_parameter = next((value for alias, value in PARAMETER_ALIASES.items() if alias in lowered), None)
        value_match = self.VALUE_PATTERN.search(lowered)
        evidence: list[str] = []
        score = 0.0

        if matched_parameter:
            score += 0.45
            evidence.append(f"Matched parameter alias to {matched_parameter}.")
        if value_match:
            score += 0.35
            evidence.append(f"Parsed numeric value {value_match.group('value')}.")
        if topology_change:
            evidence.append("Detected topology-changing language.")
            score = max(score - 0.2, 0.15)
        if not matched_parameter:
            for step in plan.steps:
                if any(token in lowered for token in step.id.split("_")):
                    score += 0.15
                    evidence.append(f"Matched revision text to step {step.id}.")
                    break

        operation = "topology_change" if topology_change else "update_parameter" if matched_parameter else "unknown"
        targets = [matched_parameter] if matched_parameter else []
        intent = RevisionIntent(
            operation=operation,
            targets=targets,
            constraints=[],
            confidence_score=round(min(score, 1.0), 2),
            confidence_evidence=evidence or ["No strong deterministic match found."],
        )
        if operation != "update_parameter" or not value_match or matched_parameter is None:
            return intent, None

        target_step_ids = [
            step.id
            for step in plan.steps
            if matched_parameter in step.parameters
        ]
        if not target_step_ids and matched_parameter in plan.parameters:
            target_step_ids = [plan.steps[0].id]
        patch = PlanPatch(
            reason=f"Update {matched_parameter} from revision instruction.",
            target_step_ids=target_step_ids,
            parameter_updates={matched_parameter: float(value_match.group("value"))},
            topology_change=False,
        )
        return intent, patch

    def apply_patch(self, plan: SemanticBuildPlan, patch: PlanPatch) -> SemanticBuildPlan:
        updated = deepcopy(plan)
        updated.parameters.update(patch.parameter_updates)
        for step in updated.steps:
            if step.id in patch.target_step_ids:
                for key, value in patch.parameter_updates.items():
                    if key in step.parameters:
                        step.parameters[key] = value
        return updated
