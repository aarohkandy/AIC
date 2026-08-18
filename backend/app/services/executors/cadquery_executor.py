from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app.core.settings import Settings
from app.models.schemas import (
    ArtifactPaths,
    BuildResult,
    CompileResult,
    DesignBrief,
    FailureReport,
    SemanticBuildPlan,
    ValidationReport,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]


class CadQueryExecutor:
    """Run compiled CadQuery source in an out-of-process runtime.

    Writes the source and a JSON payload, invokes the runtime module as a
    subprocess, and parses its BuildResult. A subprocess that times out,
    cannot start, or dies before writing its result comes back as a
    BuildResult carrying a FailureReport, never as an exception for the
    API layer to turn into a 500. The subprocess output is kept in
    ``executor-stderr.log`` next to the artifacts.
    """

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
        log_path = artifacts_dir / "executor-stderr.log"
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
        # Repair attempts reuse this directory, so a result left behind by the
        # previous attempt would be read as if it belonged to this one.
        result_path.unlink(missing_ok=True)

        timeout = self.settings.default_executor_timeout_seconds
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.services.executors.runtime",
                    str(payload_path),
                ],
                cwd=BACKEND_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired carries whatever was read before the kill, as bytes
            # even under text=True.
            self._write_log(log_path, exc.stdout, exc.stderr)
            return self._subprocess_failure(
                source_path,
                failure_type="executor_timeout",
                message=(
                    f"Executor subprocess exceeded its {timeout}s budget on a "
                    f"{len(plan.steps)}-step plan and was killed."
                ),
                next_action=(
                    "Raise AI_CAD_DEFAULT_EXECUTOR_TIMEOUT_SECONDS or simplify the plan so "
                    "fewer steps rebuild."
                ),
            )
        except OSError as exc:
            self._write_log(log_path, None, str(exc))
            return self._subprocess_failure(
                source_path,
                failure_type="executor_unavailable",
                message=f"Could not start the executor subprocess with {sys.executable}: {exc}",
                next_action=(
                    "Check that the supported Python 3.11 conda environment is the "
                    "interpreter running the backend."
                ),
            )

        self._write_log(log_path, completed.stdout, completed.stderr)
        if not result_path.exists():
            return self._subprocess_failure(
                source_path,
                failure_type="executor_no_result",
                message=(
                    f"Executor exited with code {completed.returncode} without writing a "
                    f"result payload. Last output: {self._tail(completed.stderr, completed.stdout)}"
                ),
                next_action=f"Read {log_path} for the full executor output.",
            )
        return BuildResult.model_validate(json.loads(result_path.read_text(encoding="utf-8")))

    @staticmethod
    def _subprocess_failure(
        source_path: Path,
        *,
        failure_type: str,
        message: str,
        next_action: str,
    ) -> BuildResult:
        return BuildResult(
            status="failed",
            attempts_used=1,
            artifacts=ArtifactPaths(source_path=str(source_path)),
            validation=ValidationReport(status="failed", checks={failure_type: True}),
            failure=FailureReport(
                failure_type=failure_type,
                message=message,
                next_action=next_action,
                # The subprocess never got far enough to blame a step, and there is
                # nothing for the repair loop to shrink, so stop after one attempt.
                attribution_basis="setup_unavailable",
            ),
        )

    @staticmethod
    def _decode(stream: str | bytes | None) -> str:
        if stream is None:
            return ""
        if isinstance(stream, bytes):
            return stream.decode("utf-8", errors="replace")
        return stream

    @classmethod
    def _write_log(cls, path: Path, stdout: str | bytes | None, stderr: str | bytes | None) -> None:
        sections = [f"=== run at {datetime.now().isoformat(timespec='seconds')} ===\n"]
        for label, stream in (("stdout", stdout), ("stderr", stderr)):
            text = cls._decode(stream).strip()
            if text:
                sections.append(f"--- {label} ---\n{text}\n")
        # Repair attempts reuse the artifacts directory, so truncating here would
        # leave the next_action pointing at a log of only the last attempt.
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(sections))

    @classmethod
    def _tail(cls, *streams: str | bytes | None, lines: int = 5) -> str:
        for stream in streams:
            text = cls._decode(stream).strip()
            if text:
                return " / ".join(text.splitlines()[-lines:])
        return "(no output captured)"
