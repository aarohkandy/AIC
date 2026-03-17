from __future__ import annotations

from app.models.schemas import DesignBrief, SemanticBuildPlan, ValidationReport


class DesignValidator:
    def planning_risk_score(self, brief: DesignBrief) -> float:
        prompt = brief.prompt.lower()
        risk = 0.0
        if not any(value is not None for value in brief.target_dims.model_dump().values()):
            risk += 0.15
        if any(word in prompt for word in ("assembly", "multiple parts", "hinge", "joint", "fit")):
            risk += 0.3
        if any(word in prompt for word in ("maybe", "roughly", "something like")):
            risk += 0.15
        if "and" in prompt and any(word in prompt for word in ("mug", "bracket", "box", "cap", "stand")):
            risk += 0.1
        return round(min(risk, 1.0), 2)

    def validate_plan(self, brief: DesignBrief, plan: SemanticBuildPlan) -> ValidationReport:
        checks: dict[str, bool | float | str] = {
            "has_steps": bool(plan.steps),
            "has_summary": bool(plan.summary),
            "step_count": len(plan.steps),
        }
        status = "passed" if checks["has_steps"] and checks["has_summary"] else "failed"
        if not brief.required_features:
            checks["required_features_status"] = "not_requested"
        else:
            missing = [
                feature
                for feature in brief.required_features
                if feature.lower() not in plan.summary.lower()
            ]
            checks["required_features_status"] = "passed" if not missing else f"missing:{','.join(missing)}"
            if missing:
                status = "failed"
        return ValidationReport(status=status, checks=checks)
