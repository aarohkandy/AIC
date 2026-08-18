from __future__ import annotations

import re
from typing import Any

from app.models.schemas import DesignBrief, SemanticBuildPlan, SemanticStep
from app.services.cadquery_macros import (
    default_postcondition,
    macro_parameters_for_prompt,
)

_NUMBER = r"(\d+(?:\.\d+)?)"

# Words that start a dimension of their own. A number followed by one of them
# belongs to that clause, not to a keyword sitting further back in the sentence.
_DIMENSION_NOUN_WORDS = (
    "height",
    "width",
    "depth",
    "length",
    "thickness",
    "diameter",
    "dia",
    "radius",
)
_DIMENSION_WORDS = ("tall", "high", "wide", "deep", "long", "thick", "across")
_DIMENSION_WORDS += _DIMENSION_NOUN_WORDS
_DIMENSION_NOUNS = "|".join(_DIMENSION_NOUN_WORDS)

# "3.5 in tall" is inches, "86 in diameter" is the preposition. The give-away is
# what follows: the noun forms come after the preposition, never after a unit.
_UNITS = rf"inches|inch|mm|cm|in(?!\s+(?:{_DIMENSION_NOUNS}))"
_UNIT = rf"(?:\s*({_UNITS}))?"
_ANY_UNIT = rf"(?:\s*(?:{_UNITS}))?"
_CONNECTOR = r"(?:(?:in|of|thick)\s+)?"

_MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "in": 25.4, "inch": 25.4, "inches": 25.4}
_UNIT_NAMES = {
    "mm": "millimetres",
    "cm": "centimetres",
    "in": "inches",
    "inch": "inches",
    "inches": "inches",
}


def _dimension_patterns(*keywords: str, owns: tuple[str, ...] = ()) -> tuple[re.Pattern[str], ...]:
    """Compile the two word orders a dimension is written in.

    English puts the number first about as often as last ("86 mm diameter" vs
    "diameter 86 mm"), so both are matched. A unit and a short connector may sit
    in the gap, which is what makes "86 mm in diameter" work. What may not sit
    there is *another* dimension: in "a thick wall 96 mm tall" the 96 belongs to
    the height, so the wall keyword has to come away empty.

    `owns` is the vocabulary that still reads as this dimension when it trails
    the number, and it has to be per-dimension. "walls 4 mm thick" is the
    ordinary way to write a wall thickness; treating its own "thick" as somebody
    else's clause threw the 4 away.
    """
    alternation = "|".join(keywords)
    others = "|".join(word for word in _DIMENSION_WORDS if word not in owns)
    not_another = rf"(?![\d.])(?!{_ANY_UNIT}\s*(?:{others})\b)"
    return (
        re.compile(rf"{_NUMBER}{_UNIT}\s*{_CONNECTOR}\b(?:{alternation})\b"),
        re.compile(rf"\b(?:{alternation})\b(?:\s*(?:of|is|=|:))?\s*{_NUMBER}{not_another}{_UNIT}"),
    )


def _keyword_pattern(*words: str) -> re.Pattern[str]:
    """Match any of the words as a whole word, singular or plural.

    Whole words matter here: a substring test reads "capacitor" as a cap and
    "standard" as a stand, which is the silent mis-categorization this planner is
    supposed to stop doing.
    """
    return re.compile(r"\b(?:" + "|".join(words) + r")(?:e?s)?\b")


class RuleBasedPlanner:
    """Deterministic fallback planner.

    Infers an object category from the prompt and emits a staged build
    plan from the known CadQuery macro library, extracting dimensions
    from the brief where available.

    The macro library only covers the families in ``MACRO_FAMILIES``. Anything
    else still gets the closest recipe, but the plan says so in its assumptions
    instead of quietly pretending the request was understood.
    """

    MACRO_FAMILIES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
        ("mug", "mug", _keyword_pattern("mug", "cup")),
        ("l_bracket", "L bracket", _keyword_pattern("bracket")),
        ("project_box", "project box", _keyword_pattern("box", "enclosure")),
        ("phone_stand", "phone stand", _keyword_pattern("stand")),
        ("bottle_cap", "bottle cap", _keyword_pattern("bottle", "cap")),
    )
    FALLBACK_KIND = "bottle_cap"

    DIMENSION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
        "diameter": _dimension_patterns(
            "diameter", "dia", "across", owns=("diameter", "dia", "across")
        ),
        "height": _dimension_patterns("height", "tall", "high", owns=("height", "tall", "high")),
        "width": _dimension_patterns("width", "wide", owns=("width", "wide")),
        "depth": _dimension_patterns("depth", "deep", owns=("depth", "deep")),
        "wall_thickness": _dimension_patterns(
            "wall thickness", "walls", "wall", owns=("thick", "thickness")
        ),
    }

    def plan(self, brief: DesignBrief) -> SemanticBuildPlan:
        inferred = self.infer_kind(brief.prompt)
        kind = inferred or self.FALLBACK_KIND
        extracted, converted_units = self._extract_parameters(brief)
        raw_steps, dimension_notes = macro_parameters_for_prompt(kind, extracted)
        steps = [
            SemanticStep(
                id=raw_step["id"],
                intent=raw_step["intent"],
                primitive_or_macro=raw_step["primitive_or_macro"],
                workplane=raw_step.get("workplane", ""),
                location_notes=raw_step.get("location_notes", []),
                size_notes=raw_step.get("size_notes", []),
                sketch_constraints=raw_step.get("sketch_constraints", []),
                manual_instructions=raw_step.get("manual_instructions", []),
                parameters=raw_step["parameters"],
                depends_on=raw_step.get("depends_on", []),
                postcondition=default_postcondition(raw_step["primitive_or_macro"]),
            )
            for raw_step in raw_steps
        ]
        assumptions = self._assumptions(
            kind,
            brief,
            dimension_notes,
            converted_units=converted_units,
            recognized=inferred is not None,
        )
        summary = self._summary(kind, recognized=inferred is not None)
        parameters = self._combine_parameters(steps)
        return SemanticBuildPlan(
            summary=summary,
            assumptions=assumptions,
            parameters=parameters,
            steps=steps,
        )

    def infer_kind(self, prompt: str) -> str | None:
        """Return the macro family the prompt names, or None when none of them fit.

        Public because the model gateway needs the same answer when it decides
        which fallback warning to raise, and a second keyword list drifted.
        """
        text = prompt.lower()
        for kind, _label, keywords in self.MACRO_FAMILIES:
            if keywords.search(text):
                return kind
        return None

    def _family_label(self, kind: str) -> str:
        for family, label, _keywords in self.MACRO_FAMILIES:
            if family == kind:
                return label
        return kind

    def _extract_parameters(self, brief: DesignBrief) -> tuple[dict[str, Any], list[str]]:
        # target_dims is what the user typed into the form, so it wins outright;
        # the prompt scan only fills in the fields the form left blank. The
        # brief's unit selector is the unit of those four numbers, and the macro
        # library only ever speaks millimetres, so convert on the way in.
        converted_units: list[str] = []
        form_scale = _MM_PER_UNIT[brief.units]
        extracted: dict[str, Any] = {}
        for field in ("height", "width", "depth", "diameter"):
            value = getattr(brief.target_dims, field)
            if value is not None:
                extracted[field] = round(value * form_scale, 4)
        if extracted and form_scale != 1.0:
            converted_units.append(_UNIT_NAMES[brief.units])

        prompt = brief.prompt.lower()
        for name, patterns in self.DIMENSION_PATTERNS.items():
            if name in extracted:
                continue
            for pattern in patterns:
                match = pattern.search(prompt)
                if match:
                    # A number the prompt leaves unqualified takes the brief's
                    # unit system; an explicit "cm" or "in" overrides it.
                    unit = match.group(2) or brief.units
                    scale = _MM_PER_UNIT[unit]
                    if scale != 1.0 and _UNIT_NAMES[unit] not in converted_units:
                        converted_units.append(_UNIT_NAMES[unit])
                    extracted[name] = round(float(match.group(1)) * scale, 4)
                    break
        return extracted, converted_units

    def _summary(self, kind: str, *, recognized: bool) -> str:
        recipe = {
            "mug": "create the outer body, hollow it, and attach a handle so the mug emerges in stages",
            "l_bracket": "create the bracket profile first, then add the mounting holes as a second step",
            "project_box": "create the enclosure shell first, then add internal standoffs for mounting",
            "phone_stand": "create the stand silhouette first, then add the retention lip as a refinement",
            "bottle_cap": "create the cap body first, then add perimeter grip cutouts as the finishing step",
        }[kind]
        if not recognized:
            # The summary is the first thing the CLI prints, so it cannot describe
            # the stand-in recipe as though it were the object that was asked for.
            return f"No macro family matches this request. As a stand-in, {recipe}."
        return recipe[0].upper() + recipe[1:] + "."

    def _assumptions(
        self,
        kind: str,
        brief: DesignBrief,
        dimension_notes: list[str],
        *,
        converted_units: list[str],
        recognized: bool,
    ) -> list[str]:
        # The macros emit millimetres whatever the brief's unit selector said, so
        # this names the unit the numbers below are actually in.
        assumptions = ["Every dimension in this plan is in millimetres."]
        if not recognized:
            families = ", ".join(label for _kind, label, _keywords in self.MACRO_FAMILIES[:-1])
            last = self.MACRO_FAMILIES[-1][1]
            assumptions.append(
                f'No macro family matches "{self._request_label(brief.prompt)}", so the steps '
                f"below build a {self._family_label(kind)} instead of the object described."
            )
            assumptions.append(f"The macro library covers {families} and {last}.")
        for unit in converted_units:
            assumptions.append(f"Dimensions given in {unit} were converted to millimetres.")
        assumptions.extend(dimension_notes)
        if kind == "project_box":
            assumptions.append(
                "Single-part enclosure body only; separate lids remain out of scope for v1."
            )
        if kind == "mug":
            assumptions.append(
                "Handle is blocky and revision-friendly rather than ergonomic in v1."
            )
        return assumptions

    @staticmethod
    def _request_label(prompt: str) -> str:
        text = " ".join(prompt.split())
        return text if len(text) <= 60 else text[:57].rstrip() + "..."

    def _combine_parameters(self, steps: list[SemanticStep]) -> dict[str, Any]:
        combined: dict[str, Any] = {}
        for step in steps:
            combined.update(step.parameters)
        return combined
