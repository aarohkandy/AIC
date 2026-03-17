from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.core.settings import Settings
from app.models.schemas import DesignBrief, ExecutorHealth, ModelCallRecord, SemanticBuildPlan
from app.services.planners.rule_based_planner import RuleBasedPlanner
from app.services.validation.design_validator import DesignValidator


class ModelGateway:
    def __init__(
        self,
        settings: Settings,
        planner: RuleBasedPlanner,
        validator: DesignValidator,
    ) -> None:
        self.settings = settings
        self.planner = planner
        self.validator = validator

    def plan(
        self,
        brief: DesignBrief,
        *,
        design_pro_call_count: int = 0,
        prior_flash_failure: bool = False,
    ) -> tuple[SemanticBuildPlan, float, ModelCallRecord, list[str]]:
        risk = self.validator.planning_risk_score(brief)
        warnings: list[str] = [self.settings.python_warning]
        if self._can_use_hosted(risk, design_pro_call_count, prior_flash_failure):
            hosted_plan, record = self._plan_with_gemini(brief, risk)
            return hosted_plan, risk, record, warnings
        local_plan = self.planner.plan(brief)
        return (
            local_plan,
            risk,
            ModelCallRecord(
                model="rule-based-local",
                provider="local",
                input_tokens=0,
                output_tokens=0,
                path="local",
            ),
            warnings,
        )

    def executor_health(self) -> ExecutorHealth:
        if self.settings.executor_mode != "containerized":
            return ExecutorHealth(
                executor_mode=self.settings.executor_mode,
                healthy=False,
                details={"reason": "Hosted calls require a containerized Linux executor."},
            )
        path = self.settings.health_file
        if not path.exists():
            return ExecutorHealth(
                executor_mode=self.settings.executor_mode,
                healthy=False,
                details={"reason": "Executor health file is missing."},
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        healthy = bool(payload.get("healthy"))
        return ExecutorHealth(
            executor_mode="containerized",
            healthy=healthy,
            details=payload,
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
        ledger = self._load_ledger()
        today = str(date.today())
        today_counts = ledger.get(today, {"flash": 0, "pro": 0})
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

    def _load_ledger(self) -> dict[str, dict[str, int]]:
        path = self.settings.quota_file
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _record_call(self, tier: str) -> None:
        path = self.settings.quota_file
        ledger = self._load_ledger()
        today = str(date.today())
        ledger.setdefault(today, {"flash": 0, "pro": 0})
        ledger[today][tier] = int(ledger[today].get(tier, 0)) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
