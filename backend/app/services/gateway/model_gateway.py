from __future__ import annotations

import json
from datetime import date
from math import isfinite
from typing import Any

import httpx
from pydantic import ValidationError

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


class HostedPlannerError(RuntimeError):
    """Raised when the hosted Gemini planner is unavailable or returns unusable output."""


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
            try:
                tier = self._hosted_tier(risk, prior_flash_failure)
                hosted_plan, record = self._plan_with_gemini(brief, tier)
                return hosted_plan, risk, record, warnings
            except HostedPlannerError as exc:
                warnings.append(f"Hosted Gemini planner failed, falling back: {exc}")
        local_plan = self.planner.plan(brief)
        # Whichever planner failed has already said so above, and this line used
        # to blame the local one whether or not it had been asked: with
        # prefer_local_model_planner off, nothing was tried before this point.
        if supported_shape:
            warnings.append("Using the deterministic rule-based planner.")
        else:
            warnings.append(
                "Using the deterministic rule-based planner, but no macro family matches "
                "this shape, so the plan below is a stand-in."
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

    @staticmethod
    def _hosted_tier(risk: float, prior_flash_failure: bool) -> str:
        """Which model tier a brief goes to.

        The quota gate and the call itself both need this answer, and each used
        to work it out for itself. They disagreed on one case: a low-risk brief
        promoted by a prior flash failure was charged to the pro budget and then
        sent to the flash model.
        """
        return "pro" if risk >= 0.35 or prior_flash_failure else "flash"

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
        if self._hosted_tier(risk, prior_flash_failure) == "pro":
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
        tier: str,
    ) -> tuple[SemanticBuildPlan, ModelCallRecord]:
        model = (
            self.settings.gemini_pro_model if tier == "pro" else self.settings.gemini_flash_model
        )
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
        try:
            response = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": self.settings.gemini_api_key},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            # The API key rides in the query string, and str() on a status error
            # quotes the whole request URL. plan() puts this text into the
            # response warnings and the web app renders every one of them, so a
            # 400 "API key not valid" would have printed the key onto the page.
            # Only the status code goes out; the exception itself is chained for
            # the log.
            raise HostedPlannerError(f"Gemini answered HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            # Same rule for the transport errors, which is why this is a class
            # name and not a message: nothing that has seen the URL is quoted.
            raise HostedPlannerError(f"Gemini could not be reached: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise HostedPlannerError(f"Gemini returned a non-JSON response: {exc}") from exc

        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (IndexError, KeyError, TypeError) as exc:
            # A safety block answers 200 with a candidate that carries a
            # finishReason and no content, and a blocked prompt answers with no
            # candidates at all. Indexing straight through the four levels made
            # both of those a 500 instead of the fall-through to the
            # deterministic planner the README promises.
            raise HostedPlannerError(f"Gemini reply carried no plan text: {exc!r}") from exc
        try:
            plan = SemanticBuildPlan.model_validate_json(text)
        except ValidationError as exc:
            raise HostedPlannerError(f"Gemini returned invalid plan JSON: {exc}") from exc
        usage = body.get("usageMetadata") or {}
        self._record_call(tier)
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
