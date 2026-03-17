from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.core.settings import Settings
from app.models.schemas import BuildResult, CompileResult, DesignBrief, SemanticBuildPlan


class CadQueryExecutor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def execute(
        self,
        *,
        design_id: str,
        brief: DesignBrief,
        plan: SemanticBuildPlan,
        compile_result: CompileResult,
        artifacts_dir: Path,
        dirty_from_step: str | None = None,
    ) -> BuildResult:
        source_path = artifacts_dir / "compiled.py"
        payload_path = artifacts_dir / "executor-payload.json"
        result_path = artifacts_dir / "executor-result.json"
        source_path.write_text(compile_result.source, encoding="utf-8")
        payload_path.write_text(
            json.dumps(
                {
                    "design_id": design_id,
                    "brief": brief.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "source_path": str(source_path),
                    "artifacts_dir": str(artifacts_dir),
                    "cache_root": str(self.settings.cache_root),
                    "compiler_version": self.settings.compiler_version,
                    "dirty_from_step": dirty_from_step,
                    "result_path": str(result_path),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [sys.executable, "-m", "app.services.executors.runtime", str(payload_path)],
            cwd=Path(__file__).resolve().parents[3],
            check=False,
            timeout=self.settings.default_executor_timeout_seconds,
        )
        if not result_path.exists():
            return BuildResult.model_validate(
                {
                    "status": "failed",
                    "attempts_used": 1,
                    "failure": {
                        "failure_type": "executor_no_result",
                        "message": "Executor did not produce a result payload.",
                        "next_action": "Check the backend runtime logs and supported CadQuery environment.",
                        "attribution_basis": "setup_unavailable",
                    },
                }
            )
        return BuildResult.model_validate(json.loads(result_path.read_text(encoding="utf-8")))
