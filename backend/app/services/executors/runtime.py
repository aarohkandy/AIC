from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any


def _hash_payload(payload: object) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _cache_key(
    design_id: str,
    step_id: str,
    parameter_hash: str,
    parent_artifact_hash: str,
    compiler_version: str,
) -> str:
    return _hash_payload(
        {
            "design_id": design_id,
            "step_id": step_id,
            "parameter_hash": parameter_hash,
            "parent_artifact_hash": parent_artifact_hash,
            "compiler_version": compiler_version,
        }
    )


def _load_cadquery() -> Any:
    try:
        import cadquery as cq  # type: ignore
    except Exception:
        return None
    return cq


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
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
                "metrics": {"bounding_box": {}, "planning_risk_score": 0.0, "token_usage": {}},
                "validation": {"status": "skipped", "checks": {"cadquery_available": False}},
                "failure": {
                    "failure_type": "cadquery_unavailable",
                    "message": "CadQuery is unavailable in the active Python runtime.",
                    "next_action": "Create the supported Python 3.11 conda environment from environment.yml.",
                    "attribution_basis": "setup_unavailable",
                },
            },
        )
        return

    namespace: dict[str, Any] = {"cq": cq}
    source = Path(payload["source_path"]).read_text(encoding="utf-8")
    exec(compile(source, payload["source_path"], "exec"), namespace)

    plan = payload["plan"]
    brief = payload["brief"]
    artifacts_dir = Path(payload["artifacts_dir"])
    cache_root = Path(payload["cache_root"])
    cache_root.mkdir(parents=True, exist_ok=True)
    dirty_from = payload.get("dirty_from_step")

    state = None
    failed_step_id = None
    cache_hits = 0
    parent_hash = "root"
    step_metrics: dict[str, Any] = {}
    encountered_dirty = dirty_from is None

    try:
        for step in plan["steps"]:
            if dirty_from and step["id"] == dirty_from:
                encountered_dirty = True
            parameter_hash = _hash_payload(step["parameters"])
            cache_key = _cache_key(
                payload["design_id"],
                step["id"],
                parameter_hash,
                parent_hash,
                payload["compiler_version"],
            )
            cache_dir = cache_root / cache_key
            entry_path = cache_dir / "entry.json"
            cached_artifact = cache_dir / f"{step['id']}.step"
            cached_metrics = cache_dir / f"{step['id']}-metrics.json"

            if not encountered_dirty and entry_path.exists() and cached_artifact.exists() and cached_metrics.exists():
                state = cq.importers.importStep(str(cached_artifact))
                step_metrics[step["id"]] = json.loads(cached_metrics.read_text(encoding="utf-8"))
                parent_hash = cache_key
                cache_hits += 1
                continue

            step_fn = namespace[step["id"]]
            state = step_fn(state)
            solid = state.val()
            box = solid.BoundingBox()
            metrics = {
                "volume": float(solid.Volume()),
                "bounding_box": {"x": float(box.xlen), "y": float(box.ylen), "z": float(box.zlen)},
            }
            step_metrics[step["id"]] = metrics

            cache_dir.mkdir(parents=True, exist_ok=True)
            state.export(str(cached_artifact))
            cached_metrics.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            entry_path.write_text(
                json.dumps(
                    {
                        "cache_key": cache_key,
                        "design_id": payload["design_id"],
                        "step_id": step["id"],
                        "parent_artifact_hash": parent_hash,
                        "parameter_hash": parameter_hash,
                        "compiler_version": payload["compiler_version"],
                        "artifact_path": str(cached_artifact),
                        "metrics_path": str(cached_metrics),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            parent_hash = cache_key

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
                "step_path": str(artifacts_dir / "steps"),
                "step_export_path": str(step_export_path),
                "stl_path": str(stl_path),
                "glb_path": str(glb_path),
            },
            "metrics": {
                "volume": float(final_solid.Volume()),
                "bounding_box": {"x": float(final_box.xlen), "y": float(final_box.ylen), "z": float(final_box.zlen)},
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
                "failed_step_id": plan["steps"][-1]["id"] if plan["steps"] else None,
                "message": "Build finished but failed geometry acceptance checks.",
                "next_action": "Revise the plan parameters or inspect the generated source.",
                "attribution_basis": "failed_step",
            }
        _write_json(result_path, result)
    except Exception as exc:
        failed_step_id = failed_step_id or next(
            (
                step["id"]
                for step in plan["steps"]
                if step["id"] not in step_metrics
            ),
            None,
        )
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
                "failure": {
                    "failure_type": "cadquery_execution_failed",
                    "failed_step_id": failed_step_id,
                    "message": str(exc),
                    "next_action": "Inspect the compiled step function and revise the plan or parameters.",
                    "attribution_basis": "failed_step",
                },
            },
        )


if __name__ == "__main__":
    main()
