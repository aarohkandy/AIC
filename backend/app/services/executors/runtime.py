"""Out-of-process executor entry point.

Invoked as a subprocess by :class:`CadQueryExecutor` with a JSON payload
path. Loads CadQuery, executes the compiled step functions in dependency
order with per-step artifact caching, exports STEP/STL/GLB artifacts, runs
geometry acceptance checks, and writes a BuildResult-shaped JSON result.
When CadQuery is unavailable it writes a graceful setup-unavailable failure.

Anything that goes wrong from loading the compiled module onward is
written into the result file; the traceback also goes to stderr, which
the parent process captures into ``executor-stderr.log``.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
import unicodedata
from pathlib import Path
from typing import Any

from app.services.plan_order import order_by_dependencies
from app.services.storage.cache_store import CacheStore


def _load_cadquery() -> Any:
    try:
        import cadquery as cq  # type: ignore
    except Exception:
        return None
    return cq


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _execution_order(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order the plan's steps the way the compiled build_model() calls them.

    This loop runs the step functions itself rather than calling build_model(),
    so that it can cache each step's artifact - which means it has to reach the
    same order the compiler did, or the cached chain describes a different
    program from the one the user is reading.

    Names are matched on their NFKC form, which is what the compiler does and
    what Python itself does to identifiers: the ligature "ﬁx" is a valid step id
    that binds "fix". Matching raw here would resolve a dependency the compiler
    resolved into nothing, and the two would run different orders.

    A dependency naming a step the plan does not build is dropped, same as in
    the compiler, where it is a warning. A cycle is a compile error and never
    reaches the executor; if one does anyway, running those steps in plan order
    at the end is better than dropping them, because the failure then names a
    step.
    """
    by_binding = {unicodedata.normalize("NFKC", step["id"]): step["id"] for step in steps}
    by_id = {step["id"]: step for step in steps}
    dependencies = {
        step["id"]: {
            by_binding[binding]
            for binding in (
                unicodedata.normalize("NFKC", name) for name in step.get("depends_on", [])
            )
            if binding in by_binding
        }
        for step in steps
    }
    ordered, unorderable = order_by_dependencies(list(by_id), dependencies)
    return [by_id[step_id] for step_id in ordered + unorderable]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m app.services.executors.runtime <payload.json>")
    payload_path = Path(sys.argv[1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    result_path = Path(payload["result_path"])
    started = time.perf_counter()
    cq = _load_cadquery()

    if cq is None:
        _write_json(
            result_path,
            {
                "status": "failed",
                "attempts_used": 1,
                "cache_hits": 0,
                "artifacts": {"source_path": payload["source_path"]},
                "metrics": {
                    "bounding_box": {},
                    "planning_risk_score": 0.0,
                    "token_usage": {},
                },
                "validation": {
                    "status": "skipped",
                    "checks": {"cadquery_available": False},
                },
                "failure": {
                    "failure_type": "cadquery_unavailable",
                    "message": "CadQuery is unavailable in the active Python runtime.",
                    "next_action": "Create the supported Python 3.11 conda environment from environment.yml.",
                    "attribution_basis": "setup_unavailable",
                },
            },
        )
        return

    plan = payload["plan"]
    brief = payload["brief"]
    artifacts_dir = Path(payload["artifacts_dir"])
    cache = CacheStore(Path(payload["cache_root"]), payload["compiler_version"])
    dirty_from = payload.get("dirty_from_step")

    state = None
    cache_hits = 0
    parent_hash = "root"
    encountered_dirty = dirty_from is None
    module_loaded = False
    # Whatever is running right now, so a raised exception can name it. Inferring
    # it afterwards from which steps recorded metrics blames the wrong step when
    # the failure is in the cache export, which happens after metrics are taken.
    running_step: str | None = None

    try:
        namespace: dict[str, Any] = {"cq": cq}
        source = Path(payload["source_path"]).read_text(encoding="utf-8")
        exec(compile(source, payload["source_path"], "exec"), namespace)
        module_loaded = True

        ordered_steps = _execution_order(plan["steps"])
        for step in ordered_steps:
            running_step = step["id"]
            if dirty_from and step["id"] == dirty_from:
                encountered_dirty = True
            parameter_hash = cache.make_hash(step["parameters"])
            cache_key = cache.make_cache_key(
                payload["design_id"],
                step["id"],
                parameter_hash,
                parent_hash,
            )

            entry = None if encountered_dirty else cache.get(cache_key, step["id"])
            if entry is not None:
                state = cq.importers.importStep(entry.artifact_path)
                parent_hash = cache_key
                cache_hits += 1
                continue

            step_fn = namespace[step["id"]]
            state = step_fn(state)
            if state is None:
                # A manual_feature step compiles to a pass-through, so a plan that
                # opens with one has no solid yet. Nothing to measure or cache, and
                # blaming this step for the missing geometry would be wrong.
                continue
            solid = state.val()
            box = solid.BoundingBox()
            metrics = {
                "volume": float(solid.Volume()),
                "bounding_box": {
                    "x": float(box.xlen),
                    "y": float(box.ylen),
                    "z": float(box.zlen),
                },
            }
            cached_artifact = cache.artifact_path(cache_key, step["id"])
            cached_metrics = cache.metrics_path(cache_key, step["id"])
            cache.entry_dir(cache_key).mkdir(parents=True, exist_ok=True)
            state.export(str(cached_artifact))
            cached_metrics.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            cache.save(
                payload["design_id"],
                step["id"],
                parameter_hash,
                parent_hash,
                cached_artifact,
                cached_metrics,
            )
            parent_hash = cache_key

        # Exporting and measuring the finished model is not any one step's doing.
        running_step = None

        if state is None:
            _write_json(
                result_path,
                {
                    "status": "failed",
                    "attempts_used": 1,
                    "cache_hits": cache_hits,
                    "artifacts": {"source_path": payload["source_path"]},
                    "metrics": {
                        "bounding_box": {},
                        "attempt_latency_ms": int((time.perf_counter() - started) * 1000),
                        "planning_risk_score": 0.0,
                        "token_usage": {},
                    },
                    "validation": {
                        "status": "failed",
                        "checks": {"produced_geometry": False},
                    },
                    "failure": {
                        "failure_type": "no_geometry_produced",
                        "message": (
                            "Every step in the plan compiled to a manual pass-through, so the "
                            "build has no solid to export."
                        ),
                        "next_action": (
                            "Follow the manual instructions in the plan, or restate the request "
                            "in terms the macro library covers."
                        ),
                        "attribution_basis": "setup_unavailable",
                    },
                },
            )
            return

        step_export_path = artifacts_dir / "model.step"
        stl_path = artifacts_dir / "model.stl"
        glb_path = artifacts_dir / "preview.glb"
        namespace["export_artifacts"](state, str(step_export_path), str(stl_path), str(glb_path))

        final_solid = state.val()
        final_box = final_solid.BoundingBox()
        validation_checks = {
            "closed_solid": True,
            "non_zero_volume": float(final_solid.Volume()) > 0,
            "glb_exists": glb_path.exists(),
            "step_exists": step_export_path.exists(),
        }
        target_dims = brief.get("target_dims", {})
        tolerance_pass = True
        if target_dims.get("height") is not None:
            target = float(target_dims["height"])
            measured = float(final_box.zlen)
            tolerance_pass = abs(measured - target) <= max(target * 0.1, 1.0)
            validation_checks["height_within_tolerance"] = tolerance_pass

        result = {
            "status": "succeeded" if all(validation_checks.values()) else "failed",
            "attempts_used": 1,
            "cache_hits": cache_hits,
            "artifacts": {
                "source_path": payload["source_path"],
                "step_export_path": str(step_export_path),
                "stl_path": str(stl_path),
                "glb_path": str(glb_path),
            },
            "metrics": {
                "volume": float(final_solid.Volume()),
                "bounding_box": {
                    "x": float(final_box.xlen),
                    "y": float(final_box.ylen),
                    "z": float(final_box.zlen),
                },
                "attempt_latency_ms": int((time.perf_counter() - started) * 1000),
                "planning_risk_score": 0.0,
                "token_usage": {},
            },
            "validation": {
                "status": "passed" if all(validation_checks.values()) else "failed",
                "checks": validation_checks,
            },
        }
        if not all(validation_checks.values()):
            result["failure"] = {
                "failure_type": "geometry_validation_failed",
                # The last step that ran, not the last one the plan listed. The
                # loop above follows depends_on, so those are not always the
                # same step and the repair loop tunes whichever one it is given.
                "failed_step_id": ordered_steps[-1]["id"] if ordered_steps else None,
                "message": "Build finished but failed geometry acceptance checks.",
                "next_action": "Revise the plan parameters or inspect the generated source.",
                "attribution_basis": "failed_step",
            }
        _write_json(result_path, result)
    except Exception as exc:
        traceback.print_exc()
        if running_step is not None:
            failure = {
                "failure_type": "cadquery_execution_failed",
                "failed_step_id": running_step,
                "message": str(exc),
                "next_action": "Inspect the compiled step function and revise the plan or parameters.",
                "attribution_basis": "failed_step",
            }
        elif module_loaded:
            # Every step finished, so the solid exists and shrinking a parameter
            # will not help. The exporters are the thing that broke.
            failure = {
                "failure_type": "artifact_export_failed",
                "message": f"{type(exc).__name__} while exporting the finished model: {exc}",
                "next_action": "Check the STEP/STL/GLB exporters in the generated source and the CadQuery build.",
                "attribution_basis": "setup_unavailable",
            }
        else:
            # No step ran, so blaming one would be a guess. The compiled module
            # itself is the thing to look at.
            failure = {
                "failure_type": "compiled_source_load_failed",
                "message": f"{type(exc).__name__} while loading the compiled source: {exc}",
                "next_action": "Inspect the generated source; the compiler emitted a module that does not import.",
                "attribution_basis": "setup_unavailable",
            }
        _write_json(
            result_path,
            {
                "status": "failed",
                "attempts_used": 1,
                "cache_hits": cache_hits,
                "artifacts": {"source_path": payload["source_path"]},
                "metrics": {
                    "bounding_box": {},
                    "attempt_latency_ms": int((time.perf_counter() - started) * 1000),
                    "planning_risk_score": 0.0,
                    "token_usage": {},
                },
                "validation": {
                    "status": "failed",
                    "checks": {"exception": str(exc)},
                },
                "failure": failure,
            },
        )


if __name__ == "__main__":
    main()
