from __future__ import annotations

from typing import Any

import httpx

from app.core.settings import Settings
from app.models.schemas import (
    DesignBrief,
    ModelCallRecord,
    SemanticBuildPlan,
    SemanticStep,
)
from app.services.planners.prompt_engineering import (
    LOCAL_PLANNER_SYSTEM_PROMPT,
    build_local_planner_prompt,
)

OLLAMA_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "parameters": {
            "type": "object",
            "additionalProperties": {
                "anyOf": [
                    {"type": "number"},
                    {"type": "integer"},
                    {"type": "string"},
                    {"type": "boolean"},
                ]
            },
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "intent": {"type": "string"},
                    "primitive_or_macro": {"type": "string"},
                    "workplane": {"type": "string"},
                    "location_notes": {"type": "array", "items": {"type": "string"}},
                    "size_notes": {"type": "array", "items": {"type": "string"}},
                    "sketch_constraints": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "manual_instructions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "parameters": {
                        "type": "object",
                        "additionalProperties": {
                            "anyOf": [
                                {"type": "number"},
                                {"type": "integer"},
                                {"type": "string"},
                                {"type": "boolean"},
                            ]
                        },
                    },
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "postcondition": {"type": "string"},
                },
                "required": [
                    "id",
                    "intent",
                    "primitive_or_macro",
                    "workplane",
                    "location_notes",
                    "size_notes",
                    "sketch_constraints",
                    "manual_instructions",
                    "parameters",
                    "depends_on",
                    "postcondition",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "assumptions", "parameters", "steps"],
    "additionalProperties": False,
}


class OllamaPlannerError(RuntimeError):
    """Raised when the local Ollama planner is unavailable or returns unusable output."""


class OllamaPlanner:
    """Local LLM planner.

    Requests a schema-constrained SemanticBuildPlan from an Ollama model
    and normalizes it so every step has a workplane, notes, and
    constraints suitable for manual CAD verification.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def plan(self, brief: DesignBrief) -> tuple[SemanticBuildPlan, ModelCallRecord, list[str]]:
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "format": OLLAMA_PLAN_SCHEMA,
            "options": {"temperature": 0, "num_predict": 1200},
            "messages": [
                {"role": "system", "content": LOCAL_PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": build_local_planner_prompt(brief)},
            ],
        }
        try:
            response = httpx.post(
                f"{self.settings.ollama_base_url}/api/chat",
                json=payload,
                timeout=self.settings.ollama_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise OllamaPlannerError(f"Ollama planner request failed: {exc}") from exc
        except ValueError as exc:
            # Anything else listening on the Ollama port answers 200 with HTML,
            # and json.JSONDecodeError is not an httpx.HTTPError, so without
            # this the rule-based fallback never runs and /designs/plan 500s.
            raise OllamaPlannerError(f"Ollama returned a non-JSON response: {exc}") from exc

        if not isinstance(body, dict):
            raise OllamaPlannerError("Ollama returned JSON that is not a chat response object.")
        message = body.get("message", {})
        content = message.get("content", "")
        if not content:
            raise OllamaPlannerError("Ollama returned an empty planning response.")

        try:
            plan = SemanticBuildPlan.model_validate_json(content)
        except Exception as exc:
            raise OllamaPlannerError(f"Ollama returned invalid plan JSON: {exc}") from exc

        normalized = self._normalize_plan(plan)
        record = ModelCallRecord(
            model=body.get("model", self.settings.ollama_model),
            provider="ollama",
            input_tokens=int(body.get("prompt_eval_count", 0) or 0),
            output_tokens=int(body.get("eval_count", 0) or 0),
            path="local",
        )
        warnings = [
            f"Planned locally with Ollama model {record.model}.",
        ]
        return normalized, record, warnings

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self.settings.ollama_base_url}/api/tags",
                timeout=min(self.settings.ollama_timeout_seconds, 5),
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "available": False,
                "reason": str(exc),
                "model": self.settings.ollama_model,
            }

        if not isinstance(body, dict):
            return {
                "available": False,
                "reason": "Ollama tag listing was not a JSON object.",
                "model": self.settings.ollama_model,
            }
        models = [item.get("name", "") for item in body.get("models", [])]
        return {
            "available": self.settings.ollama_model in models,
            "model": self.settings.ollama_model,
            "installed_models": models,
        }

    def _normalize_plan(self, plan: SemanticBuildPlan) -> SemanticBuildPlan:
        """Fill in the fields the model left blank.

        Parameters are millimetres here regardless of the brief's own unit,
        which is what the planning prompt asks for and what the compiler and
        macros assume further down. Labelling them with the brief's unit
        instead is how a centimetre brief turns into a silent 10x error.
        """
        plan.steps = [self._normalize_step(step) for step in plan.steps]
        if not plan.assumptions:
            plan.assumptions = ["Every dimension in this plan is in millimetres."]
        return plan

    def _normalize_step(self, step: SemanticStep) -> SemanticStep:
        if not step.workplane:
            step.workplane = "XY"
        if not step.location_notes:
            step.location_notes = [f"Use the {step.workplane} workplane as the reference frame."]
        if not step.size_notes and step.parameters:
            step.size_notes = [f"{key} = {value} mm" for key, value in step.parameters.items()]
        if not step.sketch_constraints:
            step.sketch_constraints = [
                "Anchor the sketch to the origin or a named reference so it is fully defined."
            ]
        if not step.manual_instructions:
            step.manual_instructions = [step.intent, step.postcondition]
        return step
