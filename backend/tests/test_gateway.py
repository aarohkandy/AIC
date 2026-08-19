"""Planner selection, quota bookkeeping, executor health, and plan validation.

Almost everything here needs settings the shipped defaults do not use:
``executor_health()`` returns early unless ``AI_CAD_EXECUTOR_MODE`` is
``containerized``, and the hosted path needs ``AI_CAD_ALLOW_HOSTED_MODELS``
plus a key. That is why this file did not exist while the gateway sat at 40%
coverage. The crashes it pins are real once those are turned on: a runtime
JSON file that a crashed process truncated used to come back as a 500 from
``GET /health`` or ``POST /designs/plan``.

Nothing here opens a socket. ``httpx.post`` and ``httpx.get`` are replaced for
every test in the module, and a test that forgets to stub one fails loudly
rather than depending on what happens to be listening.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_design_service
from app.core.settings import Settings, get_settings
from app.models.schemas import (
    DesignBrief,
    ModelCallRecord,
    SemanticBuildPlan,
    SemanticStep,
    TargetDimensions,
)
from app.services.gateway.model_gateway import ModelGateway
from app.services.planners.ollama_planner import OllamaPlanner, OllamaPlannerError
from app.services.planners.rule_based_planner import RuleBasedPlanner
from app.services.validation.design_validator import DesignValidator

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
)
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"


@pytest.fixture(autouse=True)
def refuse_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("this test reached for the network")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)


@pytest.fixture(autouse=True)
def reset_dependency_caches():
    yield
    get_settings.cache_clear()
    get_design_service.cache_clear()


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    # _env_file=None so a developer's own backend/.env cannot decide what this
    # suite tests, and the local model planner off unless a test asks for it, so
    # each test starts the planner ladder where it means to.
    fields: dict[str, object] = {"runtime_root": tmp_path, "prefer_local_model_planner": False}
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def hosted_settings(tmp_path: Path, *, healthy: bool = True, **overrides: object) -> Settings:
    """Settings with the hosted path unlocked: containerized, allowed, keyed."""
    fields: dict[str, object] = {
        "executor_mode": "containerized",
        "allow_hosted_models": True,
        "gemini_api_key": "not-a-real-key",
    }
    fields.update(overrides)
    settings = make_settings(tmp_path, **fields)
    settings.health_file.write_text(json.dumps({"healthy": healthy}), encoding="utf-8")
    return settings


def make_gateway(settings: Settings, ollama_planner: object | None = None) -> ModelGateway:
    return ModelGateway(
        settings,
        RuleBasedPlanner(),
        DesignValidator(),
        ollama_planner=ollama_planner,
    )


def sample_plan() -> SemanticBuildPlan:
    step = SemanticStep(
        id="create_outer_body",
        intent="Create the outer mug body.",
        primitive_or_macro="create_mug_body",
        workplane="XY",
        location_notes=["Place the circle center on the origin."],
        size_notes=["Outer diameter = 86 mm."],
        sketch_constraints=["Constrain the circle center to the origin."],
        manual_instructions=["Extrude the profile 96 mm."],
        parameters={"outer_diameter": 86.0, "height": 96.0},
        postcondition="Outer cylinder exists.",
    )
    return SemanticBuildPlan(
        summary="A mug in one step.",
        assumptions=["Every dimension in this plan is in millimetres."],
        steps=[step],
    )


def reply(
    url: str, *, status: int = 200, body: object = None, text: str | None = None
) -> httpx.Response:
    """A real httpx.Response, so raise_for_status() and json() behave as they do live."""
    request = httpx.Request("POST", url)
    if text is not None:
        return httpx.Response(status, text=text, request=request)
    return httpx.Response(status, json=body, request=request)


def gemini_body(plan: SemanticBuildPlan, *, input_tokens: int = 0, output_tokens: int = 0) -> dict:
    """The shape a successful generateContent reply has."""
    return {
        "candidates": [{"content": {"parts": [{"text": plan.model_dump_json()}]}}],
        "usageMetadata": {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
        },
    }


def answer_gemini(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list[str]:
    """Point httpx.post at one canned reply; returns the URLs it gets called with."""
    urls: list[str] = []

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        urls.append(url)
        return response

    monkeypatch.setattr(httpx, "post", fake_post)
    return urls


def answer_gemini_as_httpx_would(
    monkeypatch: pytest.MonkeyPatch, *, status: int, body: object
) -> None:
    """Like answer_gemini, but the response carries the URL httpx itself built.

    The difference matters for exactly one thing: the API key is a query
    parameter, so only a request built from `params` has it in its URL, and only
    then does `raise_for_status()` quote it in the error it raises.
    """

    def fake_post(url: str, *, params: dict[str, str] | None = None, **kwargs: object):
        return httpx.Response(status, json=body, request=httpx.Request("POST", url, params=params))

    monkeypatch.setattr(httpx, "post", fake_post)


def write_ledger(settings: Settings, counts: dict[str, int], *, day: date | None = None) -> None:
    ledger = {str(day or date.today()): counts}
    settings.quota_file.write_text(json.dumps(ledger), encoding="utf-8")


class StubOllamaPlanner:
    """Stands in for OllamaPlanner. The gateway only calls plan() and health()."""

    def __init__(
        self,
        result: tuple[SemanticBuildPlan, ModelCallRecord, list[str]] | None = None,
        error: Exception | None = None,
        health: dict[str, object] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.health_report = health or {"available": True, "model": "llama3.1:8b"}

    def plan(self, brief: DesignBrief) -> tuple[SemanticBuildPlan, ModelCallRecord, list[str]]:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    def health(self) -> dict[str, object]:
        return self.health_report


# --- executor health ------------------------------------------------------


def test_local_executor_mode_never_reads_the_health_file(tmp_path: Path) -> None:
    # The shipped default, and the reason every crash below needs a settings
    # change to reach: in local mode the file is not opened at all.
    settings = make_settings(tmp_path)
    settings.health_file.write_text("{{{", encoding="utf-8")

    health = make_gateway(settings).executor_health()

    assert health.executor_mode == "local"
    assert health.healthy is False
    assert "containerized" in health.details["reason"]


def test_a_missing_health_file_is_unhealthy_with_a_reason(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, executor_mode="containerized")

    health = make_gateway(settings).executor_health()

    assert health.healthy is False
    assert health.details["reason"] == "Executor health file is missing."


def test_a_healthy_file_is_reported_with_everything_it_carries(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, executor_mode="containerized")
    settings.health_file.write_text(
        json.dumps({"healthy": True, "image": "ai-cad-executor"}), encoding="utf-8"
    )

    health = make_gateway(settings).executor_health()

    assert health.healthy is True
    assert health.details["image"] == "ai-cad-executor"


def test_a_truncated_health_file_is_unhealthy_rather_than_an_exception(tmp_path: Path) -> None:
    # The executor writes this file; a crash partway through leaves half of it.
    settings = make_settings(tmp_path, executor_mode="containerized")
    settings.health_file.write_text('{"healthy": tru', encoding="utf-8")

    health = make_gateway(settings).executor_health()

    assert health.healthy is False
    assert "could not be read" in health.details["reason"]


def test_a_health_file_that_is_not_an_object_is_unhealthy(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, executor_mode="containerized")
    settings.health_file.write_text("[]", encoding="utf-8")

    health = make_gateway(settings).executor_health()

    assert health.healthy is False
    assert health.details["reason"] == "Executor health file is not a JSON object."


def test_health_endpoint_answers_200_when_the_health_file_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # /health is the poll the web app and the Tauri shell use to decide the
    # backend is alive, and both read any non-200 as "backend down". A truncated
    # health file used to make that a 500 with no reason in it.
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "executor-health.json").write_text('{"healthy": tru', encoding="utf-8")
    monkeypatch.setenv("AI_CAD_RUNTIME_ROOT", str(runtime))
    monkeypatch.setenv("AI_CAD_EXECUTOR_MODE", "containerized")
    monkeypatch.setenv("AI_CAD_PREFER_LOCAL_MODEL_PLANNER", "false")
    get_settings.cache_clear()
    get_design_service.cache_clear()
    from app import main as app_main

    monkeypatch.setattr(app_main, "_probe_cache", None)
    monkeypatch.setattr(
        get_design_service().gateway,
        "local_planner_health",
        lambda: {"available": False, "reason": "stubbed"},
    )
    client = TestClient(app_main.app, raise_server_exceptions=False)

    response = client.get("/health")

    assert response.status_code == 200
    executor = response.json()["executor_health"]
    assert executor["healthy"] is False
    assert "could not be read" in executor["details"]["reason"]


def test_health_says_so_when_no_local_planner_is_configured(tmp_path: Path) -> None:
    assert make_gateway(make_settings(tmp_path)).local_planner_health() == {
        "available": False,
        "reason": "Ollama planner is not configured.",
    }


def test_a_configured_local_planner_answers_for_itself(tmp_path: Path) -> None:
    report = {"available": False, "reason": "model not pulled", "model": "llama3.1:8b"}
    gateway = make_gateway(make_settings(tmp_path), ollama_planner=StubOllamaPlanner(health=report))

    assert gateway.local_planner_health() == report


# --- planner selection ----------------------------------------------------


def test_a_local_plan_is_taken_ahead_of_every_other_planner(tmp_path: Path) -> None:
    plan = sample_plan()
    record = ModelCallRecord(model="llama3.1:8b", provider="ollama", path="local")
    gateway = make_gateway(
        make_settings(tmp_path, prefer_local_model_planner=True),
        ollama_planner=StubOllamaPlanner(result=(plan, record, ["Planned locally."])),
    )

    returned, _, call, warnings = gateway.plan(DesignBrief(prompt="a mug"))

    assert returned is plan
    assert call.provider == "ollama"
    assert "Planned locally." in warnings


def test_a_local_plan_with_quality_problems_is_kept_and_flagged(tmp_path: Path) -> None:
    plan = sample_plan()
    plan.steps[0].size_notes = []
    record = ModelCallRecord(model="llama3.1:8b", provider="ollama", path="local")
    gateway = make_gateway(
        make_settings(tmp_path, prefer_local_model_planner=True),
        ollama_planner=StubOllamaPlanner(result=(plan, record, [])),
    )

    _, _, call, warnings = gateway.plan(DesignBrief(prompt="a mug"))

    assert call.provider == "ollama"
    assert "Step create_outer_body is missing size notes." in warnings
    assert any("quality warnings" in warning for warning in warnings)


def test_an_unavailable_local_planner_falls_back_and_says_why(tmp_path: Path) -> None:
    gateway = make_gateway(
        make_settings(tmp_path, prefer_local_model_planner=True),
        ollama_planner=StubOllamaPlanner(error=OllamaPlannerError("connection refused")),
    )

    plan, _, call, warnings = gateway.plan(DesignBrief(prompt="a mug"))

    assert call.model == "rule-based-local-fallback"
    assert "Local Ollama planner unavailable, falling back: connection refused" in warnings
    assert plan.steps


def test_the_fallback_warning_says_when_no_macro_family_matches(tmp_path: Path) -> None:
    gateway = make_gateway(make_settings(tmp_path))

    recognized = gateway.plan(DesignBrief(prompt="a mug"))[3]
    unrecognized = gateway.plan(DesignBrief(prompt="a teapot which can hold 1 gallon"))[3]

    assert recognized[-1] == "Using the deterministic rule-based planner."
    assert "stand-in" in unrecognized[-1]


@pytest.mark.parametrize(
    "overrides",
    [
        {"allow_hosted_models": False, "gemini_api_key": "not-a-real-key"},
        {"allow_hosted_models": True, "gemini_api_key": None},
    ],
)
def test_hosted_planning_needs_both_the_flag_and_a_key(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    settings = make_settings(tmp_path, executor_mode="containerized", **overrides)
    settings.health_file.write_text(json.dumps({"healthy": True}), encoding="utf-8")

    # The refuse_network fixture turns any hosted call into a failure here.
    _, _, call, _ = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.model == "rule-based-local-fallback"


def test_hosted_planning_needs_a_healthy_executor(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path, healthy=False)

    _, _, call, _ = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.model == "rule-based-local-fallback"


# --- hosted quota gates ---------------------------------------------------


def test_the_daily_flash_cap_closes_the_hosted_path(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)
    gateway = make_gateway(settings)
    write_ledger(settings, {"flash": settings.default_flash_calls_per_day, "pro": 0})

    assert gateway._can_use_hosted(0.1, 0, False) is False

    write_ledger(settings, {"flash": 99, "pro": 99}, day=date.today() - timedelta(days=1))
    assert gateway._can_use_hosted(0.1, 0, False) is True


def test_the_daily_pro_cap_leaves_the_flash_tier_open(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)
    gateway = make_gateway(settings)
    write_ledger(settings, {"flash": 0, "pro": settings.default_pro_calls_per_day})

    # 0.45 is over the 0.35 line that promotes a brief to the pro model.
    assert gateway._can_use_hosted(0.45, 0, False) is False
    assert gateway._can_use_hosted(0.10, 0, False) is True


def test_one_design_only_gets_so_many_pro_calls(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)
    gateway = make_gateway(settings)

    assert gateway._can_use_hosted(0.45, settings.max_pro_calls_per_design, False) is False
    assert gateway._can_use_hosted(0.45, 0, False) is True


def test_a_failed_flash_call_promotes_the_next_one_into_the_pro_budget(tmp_path: Path) -> None:
    # prior_flash_failure is the other way into the pro tier, so a low-risk
    # brief has to answer to the per-design pro cap as well.
    settings = hosted_settings(tmp_path)
    gateway = make_gateway(settings)

    assert gateway._can_use_hosted(0.10, settings.max_pro_calls_per_design, True) is False


def test_a_promoted_call_goes_to_the_model_its_budget_was_charged_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gate and the call each worked out the tier for themselves and
    # disagreed on this one case: the pro budget was charged and the flash model
    # was sent.
    settings = hosted_settings(tmp_path)
    urls = answer_gemini(monkeypatch, reply(GEMINI_URL, body=gemini_body(sample_plan())))

    _, risk, call, _ = make_gateway(settings).plan(
        DesignBrief(prompt="a mug"), prior_flash_failure=True
    )

    assert risk < 0.35
    assert settings.gemini_pro_model in urls[0]
    assert call.model == settings.gemini_pro_model
    ledger = json.loads(settings.quota_file.read_text(encoding="utf-8"))
    assert ledger[str(date.today())] == {"flash": 0, "pro": 1}


def test_a_truncated_quota_ledger_does_not_stop_a_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ledger is rewritten after every hosted call, so an interrupted write
    # leaves this behind, and json.loads raised it straight out of plan().
    settings = hosted_settings(tmp_path)
    settings.quota_file.write_text('{"2026-08-18": {"flash"', encoding="utf-8")
    answer_gemini(monkeypatch, reply(GEMINI_URL, body=gemini_body(sample_plan())))

    _, _, call, _ = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.path == "hosted"


def test_a_ledger_that_cannot_be_written_still_hands_back_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The tally is written after the call has been made and billed. A read-only
    # runtime mount or a full disk raised OSError here and threw away a plan the
    # user had already paid for; a directory in the ledger's place is the same
    # failure with a reproducible cause.
    settings = hosted_settings(tmp_path)
    settings.quota_file.mkdir(parents=True)
    answer_gemini(monkeypatch, reply(GEMINI_URL, body=gemini_body(sample_plan())))

    plan, _, call, _ = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.path == "hosted"
    assert plan.summary == "A mug in one step."


def test_a_ledger_day_that_counts_one_tier_reads_the_other_as_zero(tmp_path: Path) -> None:
    # A day entry from a build that only ever called flash. Indexing both tiers
    # out of it raised KeyError on the way into a plan.
    settings = hosted_settings(tmp_path)
    write_ledger(settings, {"flash": 1})

    assert make_gateway(settings)._can_use_hosted(0.45, 0, False) is True


def test_a_ledger_that_is_not_an_object_counts_today_from_zero(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)
    settings.quota_file.write_text("[]", encoding="utf-8")

    assert make_gateway(settings)._can_use_hosted(0.1, 0, False) is True


# --- the hosted call itself -----------------------------------------------


def test_a_hosted_plan_comes_back_with_its_token_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = hosted_settings(tmp_path)
    body = gemini_body(sample_plan(), input_tokens=812, output_tokens=1450)
    urls = answer_gemini(monkeypatch, reply(GEMINI_URL, body=body))

    plan, _, call, _ = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert plan.summary == "A mug in one step."
    assert call.path == "hosted"
    assert call.provider == "google"
    assert (call.input_tokens, call.output_tokens) == (812, 1450)
    assert settings.gemini_flash_model in urls[0]


def test_a_risky_brief_is_sent_to_the_pro_model_and_billed_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = hosted_settings(tmp_path)
    urls = answer_gemini(monkeypatch, reply(GEMINI_URL, body=gemini_body(sample_plan())))
    brief = DesignBrief(prompt="a bracket with a hinge joint that has to fit a shaft")

    _, risk, call, _ = make_gateway(settings).plan(brief)

    assert risk >= 0.35
    assert settings.gemini_pro_model in urls[0]
    assert call.model == settings.gemini_pro_model
    ledger = json.loads(settings.quota_file.read_text(encoding="utf-8"))
    assert ledger[str(date.today())] == {"flash": 0, "pro": 1}


def test_recording_a_hosted_call_repairs_a_half_written_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = hosted_settings(tmp_path)
    write_ledger(settings, {"flash": 1})
    answer_gemini(monkeypatch, reply(GEMINI_URL, body=gemini_body(sample_plan())))

    make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    ledger = json.loads(settings.quota_file.read_text(encoding="utf-8"))
    assert ledger[str(date.today())] == {"flash": 2, "pro": 0}


@pytest.mark.parametrize(
    ("label", "response_kwargs"),
    [
        # Rate limited or over quota: the request itself fails.
        ("rate limited", {"status": 429, "body": {"error": {"message": "quota exhausted"}}}),
        # Safety filtered: 200, with a candidate that carries a finishReason and
        # no content at all.
        ("safety filtered", {"body": {"candidates": [{"finishReason": "SAFETY"}]}}),
        # Blocked at the prompt: 200 with nothing to index into.
        ("no candidates", {"body": {"candidates": []}}),
        ("not an object", {"body": []}),
        # An HTML error page from a proxy in front of the API.
        ("not json", {"text": "<html>502</html>"}),
    ],
)
def test_a_hosted_reply_that_is_not_a_plan_falls_through_to_the_local_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    response_kwargs: dict[str, object],
) -> None:
    # The README promises the deterministic planner catches a failed planner
    # call. Every one of these used to be a 500 out of POST /designs/plan.
    settings = hosted_settings(tmp_path)
    answer_gemini(monkeypatch, reply(GEMINI_URL, **response_kwargs))

    plan, _, call, warnings = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.model == "rule-based-local-fallback", label
    assert plan.steps, label
    assert any(warning.startswith("Hosted Gemini planner failed") for warning in warnings), label
    # A call that produced no plan must not be billed against the day's quota.
    assert not settings.quota_file.exists(), label


@pytest.mark.parametrize(
    ("status", "body"),
    [
        # The first failure most people hit, and the one that used to print
        # their key back at them.
        (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}),
        (429, {"error": {"message": "quota exhausted"}}),
        (500, {"error": {"message": "internal"}}),
    ],
)
def test_a_failed_hosted_call_never_puts_the_api_key_in_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, body: dict
) -> None:
    # plan() puts the failure text into the response warnings and the web app
    # renders every one of them, so anything that quotes the request URL puts
    # the key on the page: the key travels in that URL's query string.
    key = "AIzaSyD-this-key-must-not-be-printed-anywhere"
    settings = hosted_settings(tmp_path, gemini_api_key=key)
    answer_gemini_as_httpx_would(monkeypatch, status=status, body=body)

    _, _, call, warnings = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.model == "rule-based-local-fallback"
    assert any(warning.startswith("Hosted Gemini planner failed") for warning in warnings)
    assert not any(key in warning for warning in warnings)
    assert any(f"HTTP {status}" in warning for warning in warnings)


def test_a_gemini_call_that_never_connects_says_so_without_quoting_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "AIzaSyD-this-key-must-not-be-printed-anywhere"
    settings = hosted_settings(tmp_path, gemini_api_key=key)

    def refuse(url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", refuse)

    _, _, call, warnings = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.model == "rule-based-local-fallback"
    assert any("ConnectError" in warning for warning in warnings)
    assert not any(key in warning for warning in warnings)


def test_hosted_json_that_is_not_a_build_plan_falls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = hosted_settings(tmp_path)
    body = {"candidates": [{"content": {"parts": [{"text": '{"summary": "a mug"}'}]}}]}
    answer_gemini(monkeypatch, reply(GEMINI_URL, body=body))

    _, _, call, warnings = make_gateway(settings).plan(DesignBrief(prompt="a mug"))

    assert call.model == "rule-based-local-fallback"
    assert any("invalid plan JSON" in warning for warning in warnings)


# --- the local Ollama planner ---------------------------------------------


def ollama_chat(monkeypatch: pytest.MonkeyPatch, **response_kwargs: object) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: reply(OLLAMA_CHAT_URL, **response_kwargs)
    )


def test_a_local_plan_carries_the_model_and_its_token_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {
        "model": "llama3.1:8b",
        "message": {"content": sample_plan().model_dump_json()},
        "prompt_eval_count": 640,
        "eval_count": 1120,
    }
    ollama_chat(monkeypatch, body=body)

    plan, record, warnings = OllamaPlanner(make_settings(tmp_path)).plan(
        DesignBrief(prompt="a mug")
    )

    assert plan.summary == "A mug in one step."
    assert record.model == "llama3.1:8b"
    assert (record.input_tokens, record.output_tokens) == (640, 1120)
    assert warnings == ["Planned locally with Ollama model llama3.1:8b."]


@pytest.mark.parametrize(
    ("expected", "response_kwargs"),
    [
        ("not a chat response object", {"body": []}),
        ("empty planning response", {"body": {"message": {"content": ""}}}),
        ("empty planning response", {"body": {"model": "llama3.1:8b"}}),
        ("invalid plan JSON", {"body": {"message": {"content": '{"summary": "a mug"}'}}}),
        ("request failed", {"status": 503, "body": {"error": "model is loading"}}),
    ],
)
def test_a_reply_the_planner_cannot_use_raises_one_error_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected: str,
    response_kwargs: dict[str, object],
) -> None:
    # The gateway only catches OllamaPlannerError, so every unusable reply has
    # to arrive as one or the rule-based fallback never runs.
    ollama_chat(monkeypatch, **response_kwargs)

    with pytest.raises(OllamaPlannerError) as caught:
        OllamaPlanner(make_settings(tmp_path)).plan(DesignBrief(prompt="a mug"))

    assert expected in str(caught.value)


def test_a_step_the_model_left_blank_is_filled_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = SemanticBuildPlan(
        summary="A mug in one step.",
        steps=[
            SemanticStep(
                id="body",
                intent="Create the outer mug body.",
                primitive_or_macro="create_mug_body",
                parameters={"outer_diameter": 86.0},
                postcondition="Outer cylinder exists.",
            )
        ],
    )
    ollama_chat(monkeypatch, body={"message": {"content": bare.model_dump_json()}})

    plan, _, _ = OllamaPlanner(make_settings(tmp_path)).plan(DesignBrief(prompt="a mug"))
    step = plan.steps[0]

    assert step.workplane == "XY"
    assert step.location_notes == ["Use the XY workplane as the reference frame."]
    assert step.size_notes == ["outer_diameter = 86.0 mm"]
    assert step.sketch_constraints == [
        "Anchor the sketch to the origin or a named reference so it is fully defined."
    ]
    assert step.manual_instructions == ["Create the outer mug body.", "Outer cylinder exists."]
    assert plan.assumptions == ["Every dimension in this plan is in millimetres."]


def test_size_notes_say_millimetres_even_for_a_centimetre_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The planning prompt asks for millimetres and the macro library reads
    # millimetres, so a size note labelled with the brief's own unit is a 10x
    # error waiting for a centimetre brief and a 25.4x one for inches. This line
    # interpolated the brief's unit until it was fixed; the test is here so it
    # cannot come back.
    bare = SemanticBuildPlan(
        summary="A mug in one step.",
        steps=[
            SemanticStep(
                id="body",
                intent="Create the outer mug body.",
                primitive_or_macro="create_mug_body",
                parameters={"outer_diameter": 86.0},
                postcondition="Outer cylinder exists.",
            )
        ],
    )
    ollama_chat(monkeypatch, body={"message": {"content": bare.model_dump_json()}})

    for units in ("cm", "in"):
        plan, _, _ = OllamaPlanner(make_settings(tmp_path)).plan(
            DesignBrief(prompt="a mug", units=units)
        )

        assert plan.steps[0].size_notes == ["outer_diameter = 86.0 mm"], units


def test_planner_health_reports_the_model_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    installed = {"models": [{"name": settings.ollama_model}, {"name": "qwen2.5-coder:7b"}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: reply(OLLAMA_TAGS_URL, body=installed))

    health = OllamaPlanner(settings).health()

    assert health["available"] is True
    assert health["installed_models"] == [settings.ollama_model, "qwen2.5-coder:7b"]


def test_planner_health_is_unavailable_when_the_model_is_not_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = {"models": [{"name": "qwen2.5-coder:7b"}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: reply(OLLAMA_TAGS_URL, body=installed))

    health = OllamaPlanner(make_settings(tmp_path)).health()

    assert health["available"] is False
    assert health["installed_models"] == ["qwen2.5-coder:7b"]


def test_planner_health_refuses_a_tag_listing_that_is_not_an_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Something other than Ollama on the port: a JSON array is still valid JSON.
    monkeypatch.setattr(httpx, "get", lambda *a, **k: reply(OLLAMA_TAGS_URL, body=[]))

    health = OllamaPlanner(make_settings(tmp_path)).health()

    assert health["available"] is False
    assert health["reason"] == "Ollama tag listing was not a JSON object."


def test_planner_health_reports_a_port_nothing_is_listening_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(httpx, "get", refused)

    health = OllamaPlanner(make_settings(tmp_path)).health()

    assert health["available"] is False
    assert "connection attempts failed" in health["reason"]


# --- planning risk and plan quality ---------------------------------------


def test_a_fully_dimensioned_simple_brief_scores_no_risk() -> None:
    brief = DesignBrief(
        prompt="a mug",
        target_dims=TargetDimensions(diameter=86, height=96),
    )

    assert DesignValidator().planning_risk_score(brief) == 0.0


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        # No dimensions at all.
        ("a mug", 0.15),
        # Words that mean more than one part, or a mating surface.
        ("a mug with a hinge", 0.45),
        # Hedging.
        ("maybe a mug", 0.30),
        # A conjunction next to a category word, which usually means two shapes.
        ("a mug and a lid", 0.25),
    ],
)
def test_each_risk_rule_adds_its_own_weight(prompt: str, expected: float) -> None:
    assert DesignValidator().planning_risk_score(DesignBrief(prompt=prompt)) == expected


def test_every_risk_rule_at_once_still_leaves_room_below_one() -> None:
    # 0.15 + 0.3 + 0.15 + 0.1. The four rules cannot add past 0.70, so the
    # score is a ranking, not a probability, and the min(risk, 1.0) clamp in
    # planning_risk_score has never had anything to do.
    brief = DesignBrief(prompt="maybe a mug and a bracket with a hinge, roughly")

    assert DesignValidator().planning_risk_score(brief) == 0.70


def test_a_complete_plan_draws_no_quality_warnings() -> None:
    assert DesignValidator().plan_quality_warnings(sample_plan()) == []


def test_a_step_missing_its_notes_is_named_in_every_warning() -> None:
    plan = sample_plan()
    step = plan.steps[0]
    step.workplane = ""
    step.location_notes = []
    step.size_notes = []
    step.sketch_constraints = []

    warnings = DesignValidator().plan_quality_warnings(plan)

    assert warnings == [
        "Step create_outer_body is missing a workplane.",
        "Step create_outer_body is missing location notes.",
        "Step create_outer_body is missing size notes.",
        "Step create_outer_body is missing sketch constraints.",
    ]


@pytest.mark.parametrize(
    "placeholder",
    ["{{outer_diameter}}", "<diameter>", "TBD"],
)
def test_a_placeholder_left_in_a_step_is_reported(placeholder: str) -> None:
    plan = sample_plan()
    plan.steps[0].size_notes = [f"Outer diameter = {placeholder}."]

    warnings = DesignValidator().plan_quality_warnings(plan)

    assert warnings == ["Step create_outer_body contains placeholders instead of concrete values."]


def test_a_placeholder_in_a_parameter_value_is_reported_too() -> None:
    plan = sample_plan()
    plan.steps[0].parameters = {"outer_diameter": "TBD", "height": 96.0}

    warnings = DesignValidator().plan_quality_warnings(plan)

    assert "contains placeholders" in warnings[0]


@pytest.mark.parametrize(
    ("macro", "parameters", "expected"),
    [
        (
            "create_mug_body",
            {"height": 96.0},
            "Step body is missing numeric mug body parameters: outer_diameter.",
        ),
        (
            "hollow_mug_body",
            {"wall_thickness": "thin"},
            "Step body is missing numeric wall_thickness.",
        ),
        (
            "add_mug_handle",
            {"handle_width": 28.0, "handle_span": 46.0},
            "Step body is missing numeric handle parameters: "
            "handle_thickness, offset, z_center.",
        ),
    ],
)
def test_a_macro_missing_its_numbers_names_the_parameters(
    macro: str, parameters: dict[str, object], expected: str
) -> None:
    # The compiler reports these as missing_parameter and refuses to build, so
    # the plan response is the last chance to say which number is absent.
    plan = sample_plan()
    plan.steps[0] = SemanticStep(
        id="body",
        intent="Build the body.",
        primitive_or_macro=macro,
        workplane="XY",
        location_notes=["On the origin."],
        size_notes=["As dimensioned."],
        sketch_constraints=["Fully constrained."],
        manual_instructions=["Build it."],
        parameters=parameters,
        postcondition="Body exists.",
    )

    assert DesignValidator().plan_quality_warnings(plan) == [expected]
