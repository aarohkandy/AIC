"""Executor and artifact-endpoint behavior.

None of these need CadQuery. They cover the paths where the executor
subprocess never produces geometry (it times out, cannot start, or dies
before writing its result), plus the artifact endpoint that serves what
it did produce.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_design_service
from app.core.settings import Settings, get_settings
from app.models.schemas import (
    BuildResult,
    CompileResult,
    DesignBrief,
    FailureReport,
    SemanticBuildPlan,
    SemanticStep,
)
from app.services.executors import cadquery_executor, runtime
from app.services.executors.cadquery_executor import CadQueryExecutor

BRIEF = {
    "prompt": "a mug 86 mm across and 96 mm tall",
    "target_dims": {"diameter": 86, "height": 96},
}


@pytest.fixture(autouse=True)
def reset_dependency_caches():
    yield
    get_settings.cache_clear()
    get_design_service.cache_clear()


def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    monkeypatch.setenv("AI_CAD_RUNTIME_ROOT", str(tmp_path / "runtime"))
    # Keep the suite off the network: the Ollama planner would otherwise decide
    # the plan, and whether it is running is not this test's business.
    monkeypatch.setenv("AI_CAD_PREFER_LOCAL_MODEL_PLANNER", "false")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    get_design_service.cache_clear()
    # Imported here so app.main reads the patched runtime root on first import.
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def build_design(client: TestClient) -> dict:
    response = client.post("/designs/build", json={"brief": BRIEF})
    assert response.status_code == 200, response.text
    return response.json()


def simple_plan() -> SemanticBuildPlan:
    return SemanticBuildPlan(
        summary="Extrude a disc.",
        steps=[
            SemanticStep(
                id="body",
                intent="Extrude the body",
                primitive_or_macro="cylinder",
                parameters={"outer_diameter": 86.0, "height": 96.0},
                postcondition="Body exists",
            )
        ],
    )


def test_executor_timeout_is_a_build_failure_not_a_500(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, AI_CAD_DEFAULT_EXECUTOR_TIMEOUT_SECONDS="0")

    payload = build_design(client)

    build = payload["build"]
    assert build["status"] == "failed"
    assert build["failure"]["failure_type"] == "executor_timeout"
    assert "0s budget" in build["failure"]["message"]
    assert "AI_CAD_DEFAULT_EXECUTOR_TIMEOUT_SECONDS" in build["failure"]["next_action"]
    # setup_unavailable stops the repair loop instead of burning three timeouts.
    assert build["attempts_used"] == 1


def test_missing_result_payload_reports_the_subprocess_exit_code(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(cadquery_executor, "BACKEND_ROOT", elsewhere)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    executor = CadQueryExecutor(Settings(runtime_root=tmp_path / "runtime"))

    result = executor.execute(
        design_id="d1",
        brief=DesignBrief(prompt="anything"),
        plan=simple_plan(),
        compile_result=CompileResult(source="x = 1\n"),
        artifacts_dir=artifacts_dir,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.failure_type == "executor_no_result"
    assert "exited with code 1" in result.failure.message
    assert "No module named 'app'" in result.failure.message
    log = artifacts_dir / "executor-stderr.log"
    assert log.exists() and "No module named 'app'" in log.read_text(encoding="utf-8")


def test_unstartable_interpreter_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_executor.sys, "executable", str(tmp_path / "no-such-python"))
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    executor = CadQueryExecutor(Settings(runtime_root=tmp_path / "runtime"))

    result = executor.execute(
        design_id="d1",
        brief=DesignBrief(prompt="anything"),
        plan=simple_plan(),
        compile_result=CompileResult(source="x = 1\n"),
        artifacts_dir=artifacts_dir,
    )

    assert result.failure is not None
    assert result.failure.failure_type == "executor_unavailable"
    assert "no-such-python" in result.failure.message


def test_unknown_artifact_kind_is_a_400(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    # The kind is rejected before the record is loaded, so an unknown design is
    # still a 400. The old code reached getattr() with whatever was in the URL.
    response = client.get("/designs/no-such-design/artifacts/nonsense")

    assert response.status_code == 400
    assert "nonsense" in response.json()["detail"]


def test_step_artifact_is_no_longer_advertised(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    payload = build_design(client)

    # The old runtime advertised a "steps" directory it never created, so the
    # endpoint 404'd on a path the API itself had handed out.
    assert "step_path" not in payload["build"]["artifacts"]
    assert client.get(f"/designs/{payload['design_id']}/artifacts/step").status_code == 400
    assert client.get(f"/designs/{payload['design_id']}/artifacts/glb").status_code == 404


def test_compiled_source_is_servable_even_when_the_build_fails(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    payload = build_design(client)

    response = client.get(f"/designs/{payload['design_id']}/artifacts/source")

    assert response.status_code == 200
    assert response.text == payload["compile"]["source"]


def test_artifact_outside_the_runtime_root_is_not_served(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    design_id = build_design(client)["design_id"]
    escapee = tmp_path / "secret.py"
    escapee.write_text("token = 'nope'\n", encoding="utf-8")
    record_path = tmp_path / "runtime" / "designs" / design_id / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["build"]["artifacts"]["source_path"] = str(escapee)
    record_path.write_text(json.dumps(record), encoding="utf-8")

    assert client.get(f"/designs/{design_id}/artifacts/source").status_code == 404


def test_health_probes_the_local_planner_once_per_ttl(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    from app import main as app_main

    monkeypatch.setattr(app_main, "_probe_cache", None)
    probes: list[int] = []

    def counted_health() -> dict[str, object]:
        probes.append(1)
        return {"available": False, "reason": "stubbed"}

    monkeypatch.setattr(get_design_service().gateway, "local_planner_health", counted_health)

    first = client.get("/health")
    second = client.get("/health")

    assert first.status_code == 200
    assert first.json()["local_planner_health"] == {
        "available": False,
        "reason": "stubbed",
    }
    assert second.json()["local_planner_health"] == first.json()["local_planner_health"]
    assert len(probes) == 1


def test_health_reports_a_port_that_is_not_ollama(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    from app import main as app_main

    monkeypatch.setattr(app_main, "_probe_cache", None)
    probes: list[int] = []

    def unparseable_response() -> dict[str, object]:
        # What OllamaPlanner.health() does when the port answers 200 with HTML,
        # which is any misconfigured AI_CAD_OLLAMA_BASE_URL. json.JSONDecodeError
        # is not an httpx.HTTPError, so it escapes the planner's own handler.
        probes.append(1)
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(get_design_service().gateway, "local_planner_health", unparseable_response)

    responses = [client.get("/health") for _ in range(3)]

    # The browser gate treats any non-200 /health as a dead backend and disables
    # every button, so a confused planner probe must not take the endpoint down.
    assert [response.status_code for response in responses] == [200, 200, 200]
    planner = responses[0].json()["local_planner_health"]
    assert planner["available"] is False
    assert "Expecting value" in planner["reason"]
    # A probe that raises is an answer too, and gets cached like any other.
    assert len(probes) == 1


class ScriptedExecutor:
    """Replays a fixed list of BuildResults so the repair loop can be driven."""

    def __init__(self, results: list[BuildResult]) -> None:
        self.results = list(results)
        self.plans: list[SemanticBuildPlan] = []

    def execute(self, *, plan: SemanticBuildPlan, **_: object) -> BuildResult:
        self.plans.append(plan)
        return self.results.pop(0)


def repairable_plan() -> SemanticBuildPlan:
    return SemanticBuildPlan(
        summary="Fillet a body.",
        steps=[
            SemanticStep(
                id="body",
                intent="Round the top edge",
                primitive_or_macro="fillet_edges",
                parameters={
                    "selector": ">Z",
                    "radius": 8.0,
                    "wall_thickness": 4.0,
                    "hollow": True,
                },
                postcondition="Edges rounded",
            )
        ],
    )


def execution_failure(message: str) -> BuildResult:
    return BuildResult(
        status="failed",
        failure=FailureReport(
            failure_type="cadquery_execution_failed",
            failed_step_id="body",
            message=message,
            next_action="Inspect the compiled step function.",
            attribution_basis="failed_step",
        ),
    )


def test_repair_only_shrinks_what_the_failure_named(tmp_path, monkeypatch):
    make_client(tmp_path, monkeypatch)
    service = get_design_service()
    plan = repairable_plan()
    executor = ScriptedExecutor(
        [
            execution_failure("BRep_API failed on radius"),
            BuildResult(status="succeeded"),
        ]
    )
    service.executor = executor

    result = service._attempt_build(
        design_id="d1",
        brief=DesignBrief(prompt="anything"),
        plan=plan,
        compile_result=service.compiler.compile(plan),
        artifacts_dir=tmp_path,
    )

    assert result.status == "succeeded"
    assert result.attempts_used == 2
    repaired = executor.plans[1].steps[0].parameters
    assert repaired["radius"] == 7.2
    assert repaired["wall_thickness"] == 4.0
    assert repaired["hollow"] is True


def test_repair_is_recorded_so_a_resized_part_is_visible(tmp_path, monkeypatch):
    make_client(tmp_path, monkeypatch)
    service = get_design_service()
    plan = repairable_plan()
    service.executor = ScriptedExecutor(
        [execution_failure("geometry did not build"), BuildResult(status="succeeded")]
    )

    result = service._attempt_build(
        design_id="d1",
        brief=DesignBrief(prompt="anything"),
        plan=plan,
        compile_result=service.compiler.compile(plan),
        artifacts_dir=tmp_path,
    )

    note = result.validation.checks["auto_repairs"]
    assert "radius 8 to 7.2" in note
    assert "wall_thickness 4 to 3.6" in note


# The runtime's step loop, cache, and export are the half of the pipeline that
# CadQuery normally owns. A stub standing in for `cq` exercises all of it here.

STUB_SOURCE = """
def export_artifacts(result, step_path, stl_path, glb_path):
    for path in (step_path, stl_path, glb_path):
        with open(path, "w") as handle:
            handle.write("artifact")


def body(state):
    return cq.State()
"""


class StubState:
    def val(self) -> SimpleNamespace:
        return SimpleNamespace(
            Volume=lambda: 42.0,
            BoundingBox=lambda: SimpleNamespace(xlen=86.0, ylen=86.0, zlen=96.0),
        )

    def export(self, path: str, **_: object) -> None:
        Path(path).write_text("solid\n", encoding="utf-8")


class StubCadQuery:
    State = StubState

    def __init__(self) -> None:
        self.imported: list[str] = []
        self.importers = SimpleNamespace(importStep=self._import_step)

    def _import_step(self, path: str) -> StubState:
        self.imported.append(path)
        return StubState()


def run_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    *,
    stub: StubCadQuery,
    dirty_from: str | None = None,
    plan: SemanticBuildPlan | None = None,
) -> dict:
    monkeypatch.setitem(sys.modules, "cadquery", stub)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    source_path = artifacts_dir / "compiled.py"
    source_path.write_text(source, encoding="utf-8")
    result_path = artifacts_dir / "executor-result.json"
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "design_id": "d1",
                "brief": DesignBrief.model_validate(BRIEF).model_dump(mode="json"),
                "plan": (plan or simple_plan()).model_dump(mode="json"),
                "source_path": str(source_path),
                "artifacts_dir": str(artifacts_dir),
                "cache_root": str(tmp_path / "cache"),
                "compiler_version": "v1",
                "dirty_from_step": dirty_from,
                "result_path": str(result_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["runtime", str(payload_path)])
    runtime.main()
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_runtime_exports_artifacts_without_advertising_a_steps_directory(tmp_path, monkeypatch):
    result = run_runtime(tmp_path, monkeypatch, STUB_SOURCE, stub=StubCadQuery())

    assert result["status"] == "succeeded"
    assert "step_path" not in result["artifacts"]
    assert Path(result["artifacts"]["glb_path"]).exists()
    assert result["validation"]["checks"]["height_within_tolerance"] is True


def test_runtime_reuses_cached_steps_before_the_dirty_one(tmp_path, monkeypatch):
    stub = StubCadQuery()
    first = run_runtime(tmp_path, monkeypatch, STUB_SOURCE, stub=stub, dirty_from="later_step")
    assert first["cache_hits"] == 0
    assert list((tmp_path / "cache").glob("*/entry.json"))

    second = run_runtime(tmp_path, monkeypatch, STUB_SOURCE, stub=stub, dirty_from="later_step")

    assert second["cache_hits"] == 1
    assert stub.imported and stub.imported[0].endswith("body.step")


def test_runtime_reports_source_that_will_not_load(tmp_path, monkeypatch):
    result = run_runtime(tmp_path, monkeypatch, "def body(state)\n", stub=StubCadQuery())

    assert result["status"] == "failed"
    assert result["failure"]["failure_type"] == "compiled_source_load_failed"
    assert "SyntaxError" in result["failure"]["message"]
    # No step ran, so none may be blamed.
    assert result["failure"].get("failed_step_id") is None


def test_runtime_blames_the_step_that_raised(tmp_path, monkeypatch):
    source = STUB_SOURCE.replace("return cq.State()", "raise ValueError('radius too large')")

    result = run_runtime(tmp_path, monkeypatch, source, stub=StubCadQuery())

    assert result["failure"]["failure_type"] == "cadquery_execution_failed"
    assert result["failure"]["failed_step_id"] == "body"
    assert "radius too large" in result["failure"]["message"]


TWO_STEP_SOURCE = STUB_SOURCE + """

def rim(state):
    return cq.State()
"""


def two_step_plan() -> SemanticBuildPlan:
    plan = simple_plan()
    plan.steps.append(
        SemanticStep(
            id="rim",
            intent="Round the rim",
            primitive_or_macro="fillet_edges",
            parameters={"selector": ">Z", "radius": 2.0},
            postcondition="Rim rounded",
        )
    )
    return plan


def test_a_failed_cache_export_blames_the_step_it_belonged_to(tmp_path, monkeypatch):
    def refuse_to_export(self: StubState, path: str, **_: object) -> None:
        raise RuntimeError("BRep_API: STEP write failed")

    monkeypatch.setattr(StubState, "export", refuse_to_export)

    result = run_runtime(
        tmp_path,
        monkeypatch,
        TWO_STEP_SOURCE,
        stub=StubCadQuery(),
        plan=two_step_plan(),
    )

    assert result["failure"]["failure_type"] == "cadquery_execution_failed"
    # The cache export runs after the step's metrics are taken, so working the
    # blame out from "first step with no metrics" named `rim`, which never ran,
    # and sent the repair loop off to shrink its parameters.
    assert result["failure"]["failed_step_id"] == "body"


BROKEN_EXPORT_SOURCE = """
def export_artifacts(result, step_path, stl_path, glb_path):
    raise RuntimeError("glTF tessellation failed")


def body(state):
    return cq.State()
"""


def test_a_failed_artifact_export_is_not_pinned_on_a_step(tmp_path, monkeypatch):
    result = run_runtime(tmp_path, monkeypatch, BROKEN_EXPORT_SOURCE, stub=StubCadQuery())

    assert result["failure"]["failure_type"] == "artifact_export_failed"
    assert result["failure"].get("failed_step_id") is None
    assert "glTF tessellation failed" in result["failure"]["message"]
    # Every step built its geometry, so there is nothing for the repair loop to
    # shrink and no reason to spend two more attempts finding that out.
    assert result["failure"]["attribution_basis"] == "setup_unavailable"


# A manual_feature step compiles to `return state`, so a plan that opens with one
# reaches the loop with nothing built yet. That used to end in
# `'NoneType' object has no attribute 'val'` blamed on the pass-through step.

PASS_THROUGH_SOURCE = """
def export_artifacts(result, step_path, stl_path, glb_path):
    for path in (step_path, stl_path, glb_path):
        with open(path, "w") as handle:
            handle.write("artifact")


def carve_spout(state):
    return state


def body(state):
    return cq.State()
"""


def manual_first_plan() -> SemanticBuildPlan:
    plan = simple_plan()
    plan.steps.insert(
        0,
        SemanticStep(
            id="carve_spout",
            intent="Carve the spout by hand",
            primitive_or_macro="manual_feature",
            parameters={},
            postcondition="Spout carved in CAD",
        ),
    )
    return plan


def test_leading_manual_step_does_not_break_the_build(tmp_path, monkeypatch):
    plan = manual_first_plan()

    result = run_runtime(tmp_path, monkeypatch, PASS_THROUGH_SOURCE, stub=StubCadQuery(), plan=plan)

    assert result["status"] == "succeeded", result.get("failure")
    assert Path(result["artifacts"]["glb_path"]).exists()


def test_a_plan_of_only_manual_steps_says_it_built_nothing(tmp_path, monkeypatch):
    plan = manual_first_plan()
    del plan.steps[1]
    source = PASS_THROUGH_SOURCE.replace("    return cq.State()", "    return state")

    result = run_runtime(tmp_path, monkeypatch, source, stub=StubCadQuery(), plan=plan)

    assert result["status"] == "failed"
    assert result["failure"]["failure_type"] == "no_geometry_produced"
    # Nothing here is worth another executor attempt.
    assert result["failure"]["attribution_basis"] == "setup_unavailable"


def test_unknown_design_id_is_a_404_and_creates_no_directory(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    assert client.get(f"/designs/{'a' * 300}/artifacts/source").status_code == 404
    assert client.get("/designs/junk-0/artifacts/source").status_code == 404
    revise = client.post(
        "/designs/revise",
        json={"design_id": "junk-revise", "instruction": "make it taller"},
    )
    assert revise.status_code == 404

    designs_root = tmp_path / "runtime" / "designs"
    assert not designs_root.exists() or list(designs_root.iterdir()) == []


def test_executor_log_keeps_every_attempt(tmp_path, monkeypatch):
    log = tmp_path / "executor-stderr.log"
    CadQueryExecutor._write_log(log, "first attempt", None)
    CadQueryExecutor._write_log(log, "second attempt", None)

    contents = log.read_text(encoding="utf-8")
    assert "first attempt" in contents
    assert "second attempt" in contents


def test_a_record_from_an_older_version_is_a_404_not_a_500(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    design_id = build_design(client)["design_id"]
    record_path = tmp_path / "runtime" / "designs" / design_id / "record.json"

    # runtime/ outlives the code that wrote it, and this is the shape the
    # previous version of the executor left behind on every build.
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["build"]["artifacts"]["step_path"] = "/tmp/steps"
    record_path.write_text(json.dumps(record), encoding="utf-8")

    assert client.get(f"/designs/{design_id}/artifacts/source").status_code == 404
    revise = client.post(
        "/designs/revise",
        json={"design_id": design_id, "instruction": "make it taller"},
    )
    assert revise.status_code == 404


def test_a_half_written_record_is_a_404_not_a_500(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    design_id = build_design(client)["design_id"]
    record_path = tmp_path / "runtime" / "designs" / design_id / "record.json"
    record_path.write_text('{"design_id": "x', encoding="utf-8")

    assert client.get(f"/designs/{design_id}/artifacts/source").status_code == 404


def test_a_repair_shrinks_every_step_that_names_the_same_dimension(tmp_path, monkeypatch):
    make_client(tmp_path, monkeypatch)
    service = get_design_service()
    plan = service.gateway.planner.plan(
        DesignBrief(prompt="a project box 200 mm wide, 150 mm deep and 60 mm tall")
    )
    failure = FailureReport(
        failure_type="cadquery_execution_failed",
        failed_step_id="add_standoffs",
        message="BRep_API: command not done",
        next_action="Retry with a smaller feature.",
        attribution_basis="failed_step",
    )

    patch = service._repair_patch(plan, failure)
    repaired = service.revision_engine.apply_patch(plan, patch)

    shell, standoffs = repaired.steps[0], repaired.steps[1]
    # The shell and the standoffs describe one box. Shrinking only the failed
    # step would place the standoffs for a box that is not the one built.
    assert shell.parameters["width"] == standoffs.parameters["width"]
    assert shell.parameters["depth"] == standoffs.parameters["depth"]
    assert shell.parameters["wall_thickness"] == standoffs.parameters["wall_thickness"]
    assert shell.parameters["width"] < 200
    # The stale shell must not be served from cache on the retry.
    assert service._earliest_dirty_step(repaired, patch) == "create_shell"
