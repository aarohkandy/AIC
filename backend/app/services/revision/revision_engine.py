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
    """Interpret and apply natural-language plan revisions.

    Maps an instruction to a RevisionIntent with a confidence score and,
    when a parameter update is confidently identified, a PlanPatch that
    can be applied to a plan.
    """

    VALUE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)")
    # "from 3 mm to 5 mm" names the size being replaced before the size being
    # asked for, so the first number in the sentence is the wrong one to take.
    # The unit run allows a period so that "from 3 in. to 5 in." still reads as
    # a stated change rather than as two loose numbers.
    RANGE_PATTERN = re.compile(
        r"(?P<origin>\d+(?:\.\d+)?)\s*[a-z\"'.]*\s*\bto\s+(?P<target>\d+(?:\.\d+)?)"
    )
    # "1,200 mm" is one number, and VALUE_PATTERN would otherwise read it as 1
    # and then 200. Only a comma sitting inside a group of three digits goes;
    # the comma in "5 mm, and the height" is punctuation and stays.
    THOUSANDS_PATTERN = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
    # "increase the height by 5 mm" names an amount to move, not a size to land
    # on, and a patch only ever sets a parameter outright.
    RELATIVE_PATTERN = re.compile(r"\bby\s+\d")
    # Under design_service's 0.80 rebuild gate on purpose: a number this engine
    # had to guess at is something to confirm, not something to rebuild on.
    GUESSED_VALUE_CEILING = 0.75

    def interpret(
        self, instruction: str, plan: SemanticBuildPlan
    ) -> tuple[RevisionIntent, PlanPatch | None]:
        lowered = self.THOUSANDS_PATTERN.sub("", instruction.lower().strip())
        topology_change = any(
            word in lowered for word in ("add ", "remove ", "turn into", "convert")
        )
        matched_parameter = next(
            (value for alias, value in PARAMETER_ALIASES.items() if alias in lowered), None
        )
        # Only step parameters reach the compiler; plan.parameters is a summary
        # of them. A dimension no step carries cannot be revised, however
        # plainly the instruction names it.
        owning_steps = (
            [step.id for step in plan.steps if matched_parameter in step.parameters]
            if matched_parameter
            else []
        )
        numbers = self.VALUE_PATTERN.findall(lowered)
        change = self.RANGE_PATTERN.search(lowered) if len(numbers) > 1 else None
        relative = self.RELATIVE_PATTERN.search(lowered) is not None
        value_text = change.group("target") if change else (numbers[0] if numbers else None)
        # A stated change spends exactly two of the numbers. Anything past that
        # is a number the instruction never tied to anything - most often a
        # second request, as in "from 3 mm to 5 mm and the height to 120 mm",
        # which this engine only ever answers half of.
        guessed_value = relative or len(numbers) > (2 if change else 1)
        evidence: list[str] = []
        score = 0.0

        if owning_steps:
            score += 0.45
            evidence.append(f"Matched parameter alias to {matched_parameter}.")
        elif matched_parameter:
            evidence.append(
                f"No step in this plan has a {matched_parameter} parameter, "
                "so there is nothing to update."
            )
        if value_text is not None:
            score += 0.35
            evidence.append(f"Parsed numeric value {value_text}.")
        if change:
            evidence.append(
                f"Read the instruction as a change to {value_text}, "
                f"not to {change.group('origin')}."
            )
            if len(numbers) > 2:
                evidence.append(
                    f"Instruction names {len(numbers)} numbers, and only the change to "
                    f"{value_text} was read. A revision sets one parameter at a time."
                )
        elif len(numbers) > 1:
            evidence.append(
                f"Instruction names {len(numbers)} numbers and no change from one to "
                f"another, so {value_text} was read as the new value."
            )
        if relative:
            evidence.append("Instruction asks for a change by an amount, not a new value.")
        if topology_change:
            evidence.append("Detected topology-changing language.")
            score = max(score - 0.2, 0.15)
        if not matched_parameter:
            for step in plan.steps:
                if any(token in lowered for token in step.id.split("_")):
                    score += 0.15
                    evidence.append(f"Matched revision text to step {step.id}.")
                    break
        if guessed_value:
            score = min(score, self.GUESSED_VALUE_CEILING)

        operation = (
            "topology_change"
            if topology_change
            else "update_parameter" if matched_parameter else "unknown"
        )
        targets = [matched_parameter] if matched_parameter else []
        intent = RevisionIntent(
            operation=operation,
            targets=targets,
            constraints=[],
            confidence_score=round(min(score, 1.0), 2),
            confidence_evidence=evidence or ["No strong deterministic match found."],
        )
        if operation != "update_parameter" or value_text is None or not owning_steps:
            return intent, None

        patch = PlanPatch(
            reason=f"Update {matched_parameter} from revision instruction.",
            target_step_ids=owning_steps,
            parameter_updates={matched_parameter: float(value_text)},
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
