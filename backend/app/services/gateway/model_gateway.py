from __future__ import annotations

import json
from datetime import date
from math import isfinite
from typing import Any

import httpx

from app.core.settings import Settings
from app.models.schemas import (
    DesignBrief,
    ExecutorHealth,
    ModelCallRecord,
    SemanticBuildPlan,
)
from app.services.planners.ollama_planner import OllamaPlanner, OllamaPlannerError
from app.services.planners.rule_based_planner import RuleBasedPlanner
from app.services.validation.design_validator import DesignValidator


class ModelGateway:
    """Select and run a planner for a design brief.

    Prefers the local Ollama planner, optionally falls back to a hosted
    Gemini model, and finally to the deterministic rule-based planner.
    Also tracks hosted-call quotas and reports executor health.
    """

    def __init__(
        self,
        settings: Settings,
        planner: RuleBasedPlanner,
        validator: DesignValidator,
        ollama_planner: OllamaPlanner | None = None,
    ) -> None:
        self.settings = settings
        self.planner = planner
        self.validator = validator
        self.ollama_planner = ollama_planner

    def plan(
        self,
        brief: DesignBrief,
        *,
        design_pro_call_count: int = 0,
        prior_flash_failure: bool = False,
    ) -> tuple[SemanticBuildPlan, float, ModelCallRecord, list[str]]:
        risk = self.validator.planning_risk_score(brief)
        warnings: list[str] = [self.settings.python_warning]
        supported_shape = self._supports_rule_based_fallback(brief.prompt)
        should_try_local_model = (
            self.settings.prefer_local_model_planner and self.ollama_planner is not None
        )
        if should_try_local_model:
            try:
                local_plan, record, local_warnings = self.ollama_planner.plan(brief)
                warnings.extend(local_warnings)
                quality_warnings = self.validator.plan_quality_warnings(local_plan)
                if quality_warnings:
                    warnings.extend(quality_warnings)
                    warnings.append(
                        "Returning the local AI plan with quality warnings because local planning is the default path."
                    )
                return local_plan, risk, record, warnings
            except OllamaPlannerError as exc:
                warnings.append(f"Local Ollama planner unavailable, falling back: {exc}")
        if self._can_use_hosted(risk, design_pro_call_count, prior_flash_failure):
            hosted_plan, record = self._plan_with_gemini(brief, risk)
            return hosted_plan, risk, record, warnings
        local_plan = self.planner.plan(brief)
        if supported_shape:
            warnings.append(
                "Using the deterministic rule-based planner because the local AI planner failed."
            )
        else:
            warnings.append(
                "Using the deterministic rule-based planner because the local AI planner failed, "
                "and no macro family matches this shape, so the plan below is a stand-in."
            )
        return (
            local_plan,
            risk,
            ModelCallRecord(
                model="rule-based-local-fallback",
                provider="local",
                input_tokens=0,
                output_tokens=0,
                path="local",
            ),
            warnings,
        )

    def local_planner_health(self) -> dict[str, Any]:
        if self.ollama_planner is None:
            return {"available": False, "reason": "Ollama planner is not configured."}
        return self.ollama_planner.health()

    def _supports_rule_based_fallback(self, prompt: str) -> bool:
        """Ask the fallback planner itself whether it recognizes the shape.

        A second copy of the keyword list drifted away from the planner's own
        matching rules and reported "capacitor mount" as a supported shape.
        """
        return self.planner.infer_kind(prompt) is not None

    def executor_health(self) -> ExecutorHealth:
        """Report whether the containerized executor is ready to be handed work.

        Nothing in this repo writes the health file - whatever provisions the
        container is expected to - so it is untrusted input, and a crash partway
        through a write leaves a truncated one behind. /health is what the web
        app and the Tauri shell poll to decide the backend is alive, and raising
        here answered that poll with a 500, which both read as "backend down"
        with no reason attached. A file that will not parse is a health report
        of its own instead. Nothing here deletes it either: replacing the file
        belongs to whoever writes it.
        """
        if self.settings.executor_mode != "containerized":
            return self._unhealthy("Hosted calls require a containerized Linux executor.")
        path = self.settings.health_file
        if not path.exists():
            return self._unhealthy("Executor health file is missing.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A byte that is not UTF-8 and JSON that stops mid-token both land here.
            return self._unhealthy(f"Executor health file could not be read: {exc}")
        if not isinstance(payload, dict):
            return self._unhealthy("Executor health file is not a JSON object.")
        return ExecutorHealth(
            executor_mode="containerized",
            healthy=bool(payload.get("healthy")),
            details=payload,
        )

    def _unhealthy(self, reason: str) -> ExecutorHealth:
        return ExecutorHealth(
            executor_mode=self.settings.executor_mode,
            healthy=False,
            details={"reason": reason},
        )

    def _can_use_hosted(
        self,
        risk: float,
        design_pro_call_count: int,
        prior_flash_failure: bool,
    ) -> bool:
        if not self.settings.allow_hosted_models or not self.settings.gemini_api_key:
            return False
        health = self.executor_health()
        if not health.healthy:
            return False
        today_counts = self._today_counts(self._load_ledger())
        use_pro = risk >= 0.35 or prior_flash_failure
        if use_pro:
            if design_pro_call_count >= self.settings.max_pro_calls_per_design:
                return False
            if today_counts["pro"] >= self.settings.default_pro_calls_per_day:
                return False
        else:
            if today_counts["flash"] >= self.settings.default_flash_calls_per_day:
                return False
        return True

    def _plan_with_gemini(
        self,
        brief: DesignBrief,
        risk: float,
    ) -> tuple[SemanticBuildPlan, ModelCallRecord]:
        use_pro = risk >= 0.35
        model = self.settings.gemini_pro_model if use_pro else self.settings.gemini_flash_model
        payload = {
            "generationConfig": {"responseMimeType": "application/json"},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Return only JSON matching SemanticBuildPlan with summary, assumptions, "
                                "parameters, and steps. The plan must target a single parametric part. "
                                f"Design brief: {brief.model_dump_json()}"
                            )
                        }
                    ],
                }
            ],
        }
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": self.settings.gemini_api_key},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        plan = SemanticBuildPlan.model_validate_json(text)
        usage = body.get("usageMetadata", {})
        self._record_call("pro" if use_pro else "flash")
        return (
            plan,
            ModelCallRecord(
                model=model,
                provider="google",
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                path="hosted",
            ),
        )

    def _load_ledger(self) -> dict[str, Any]:
        """Read the quota ledger, treating one that will not parse as empty.

        Same reasoning as the health file: this process rewrites the ledger
        after every hosted call, so an interrupted write leaves a truncated one
        behind, and refusing to plan because the bookkeeping is damaged is a
        worse answer than counting today from zero. The cost of being wrong is
        at most one extra day of hosted calls. The next _record_call rewrites
        the file from today onward, so any earlier days an unreadable file was
        holding are gone; nothing reads them anyway, only today's two counts.
        """
        path = self.settings.quota_file
        if not path.exists():
            return {}
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return ledger if isinstance(ledger, dict) else {}

    @staticmethod
    def _today_counts(ledger: dict[str, Any]) -> dict[str, int]:
        """Today's hosted call counts, reading anything unusable as zero.

        A day entry written before the second tier existed carries only the one
        it used, and indexing both tiers out of it raised KeyError on the way
        into a plan.
        """
        counts = {"flash": 0, "pro": 0}
        day = ledger.get(str(date.today()))
        if not isinstance(day, dict):
            return counts
        for tier in ("flash", "pro"):
            value = day.get(tier)
            if isinstance(value, (int, float)) and isfinite(value):
                counts[tier] = int(value)
        return counts

    def _record_call(self, tier: str) -> None:
        """Count a hosted call, letting an unwritable ledger go uncounted.

        This runs after the call has been made and billed, so a read-only
        runtime mount or a full disk used to throw away a plan the user had
        already paid for. Losing a tally entry costs at most one extra hosted
        call, which is the same trade _load_ledger makes on the way in.
        """
        path = self.settings.quota_file
        ledger = self._load_ledger()
        counts = self._today_counts(ledger)
        counts[tier] += 1
        ledger[str(date.today())] = counts
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        except OSError:
            return
