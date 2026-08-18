from __future__ import annotations

import keyword
import math
import re
import unicodedata
from typing import Any

from app.models.schemas import (
    CompileDiagnostic,
    CompileResult,
    EditableRegion,
    SemanticBuildPlan,
    SemanticStep,
)
from app.services.cadquery_macros import (
    SUPPORTED_MACROS,
    emit_step_source,
    macro_parameter_names,
)
from app.services.validation.source_validator import SourceValidator

MANUAL_MACRO = "manual_feature"

# Names the generated module already owns. A step id that collides with one of
# them would redefine the driver or the exporter the runtime calls by name.
RESERVED_STEP_IDS = {"cq", "build_model", "export_artifacts", "state"}

# Macro parameters that are legitimately text. Every other parameter ends up in
# arithmetic inside the emitted source, so a string there is a build-time
# TypeError waiting to happen.
TEXT_PARAMETERS = {("fillet_edges", "selector")}

# Planners quote dimensions surprisingly often, so "86" and "86 mm" both mean 86.
# Any other suffix is left alone: dropping it would turn "8.6 cm" into 8.6 mm.
NUMERIC_TEXT = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?:mm)?\s*$")


def _as_number(value: Any) -> float | int | None:
    """Return ``value`` as a number, unwrapping a quoted dimension if needed.

    Infinity and NaN are rejected along with the strings and the booleans.
    Python's json module both emits and accepts the bare ``Infinity`` literal
    and pydantic lets it through, so a plan really can arrive carrying one, and
    ``repr(inf)`` is ``inf`` - an undefined name in the generated module.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        match = NUMERIC_TEXT.match(value)
        if match:
            number = float(match.group(1))
            return number if math.isfinite(number) else None
    return None


class CadQueryCompiler:
    """Compile a semantic build plan into deterministic CadQuery source.

    Emits one function per step plus a ``build_model`` driver, records
    per-step editable regions, and lints the output via SourceValidator.

    Steps the compiler cannot turn into geometry are skipped rather than
    emitted, and ``build_model`` only calls the functions that were
    actually written, so the source never references a name that does not
    exist. ``manual_feature`` is the planner's escape hatch for shapes the
    macro library does not cover: it compiles to a pass-through and a
    warning, so the rest of the plan still builds.
    """

    def __init__(self, validator: SourceValidator) -> None:
        self.validator = validator

    def compile(self, plan: SemanticBuildPlan) -> CompileResult:
        diagnostics: list[CompileDiagnostic] = []
        source_lines = [
            "import cadquery as cq",
            "",
            "def export_artifacts(result, step_path, stl_path, glb_path):",
            "    result.export(step_path)",
            "    result.export(stl_path, tolerance=0.05, angularTolerance=0.1)",
            "    assembly = cq.Assembly()",
            '    assembly.add(result, name="part", color=cq.Color(0.8, 0.8, 0.82))',
            "    assembly.export(glb_path)",
            "",
        ]
        editable_regions: list[EditableRegion] = []
        emitted_step_ids: list[str] = []
        emitted_bindings: set[str] = set()

        for step in plan.steps:
            if not step.id.isidentifier() or keyword.iskeyword(step.id):
                diagnostics.append(
                    CompileDiagnostic(
                        level="error",
                        code="invalid_step_id",
                        message=(
                            f"Step id {step.id!r} is not a usable Python function name. "
                            "Plan steps need snake_case identifiers."
                        ),
                    )
                )
                continue

            # Python normalizes identifiers to NFKC when it parses them, so the
            # name a step id actually binds is not always the string it was
            # written as. Compare on the bound name: the ligature "ﬁx" is a
            # valid identifier that binds "fix", and two steps like that would
            # otherwise pass the duplicate check and then shadow each other.
            binding = unicodedata.normalize("NFKC", step.id)

            if binding in RESERVED_STEP_IDS:
                diagnostics.append(
                    CompileDiagnostic(
                        level="error",
                        code="reserved_step_id",
                        message=(
                            f"Step id {step.id!r} is reserved by the generated module. "
                            "Rename the step."
                        ),
                    )
                )
                continue

            if binding in emitted_bindings:
                diagnostics.append(
                    CompileDiagnostic(
                        level="error",
                        code="duplicate_step_id",
                        message=(
                            f"Step id {step.id!r} appears twice. The second definition would "
                            "shadow the first and the built model would not match the plan."
                        ),
                    )
                )
                continue

            if step.primitive_or_macro == MANUAL_MACRO:
                diagnostics.append(
                    CompileDiagnostic(
                        level="warning",
                        code="manual_step",
                        message=(
                            f"Step {step.id} ({step.intent}) has no macro and compiles to a "
                            "pass-through. Follow its manual instructions in CAD; the "
                            "generated model will not contain this feature."
                        ),
                    )
                )
                step_source = f"def {step.id}(state):\n    return state"
            elif step.primitive_or_macro not in SUPPORTED_MACROS:
                diagnostics.append(
                    CompileDiagnostic(
                        level="error",
                        code="unsupported_macro",
                        message=(
                            f"Step {step.id} uses macro {step.primitive_or_macro}, which the "
                            "compiler does not implement. Use manual_feature for shapes "
                            "outside the macro library."
                        ),
                    )
                )
                continue
            else:
                parameters, parameter_problems = self._clean_parameters(step)
                if parameter_problems:
                    diagnostics.extend(parameter_problems)
                    continue
                try:
                    step_source = emit_step_source(step.id, step.primitive_or_macro, parameters)
                except KeyError as exc:
                    # _clean_parameters already reports anything the recipe reads
                    # and the step did not supply. This only fires if a macro
                    # template reads a key the parameter probe never saw, and an
                    # escaping KeyError would surface as a bare 500.
                    diagnostics.append(
                        CompileDiagnostic(
                            level="error",
                            code="missing_parameter",
                            message=(
                                f"Step {step.id} does not supply {exc.args[0]!r}, which macro "
                                f"{step.primitive_or_macro} needs."
                            ),
                        )
                    )
                    continue

            start_line = len(source_lines) + 1
            source_lines.extend(step_source.splitlines())
            source_lines.append("")
            end_line = len(source_lines) - 1
            editable_regions.append(
                EditableRegion(
                    step_id=step.id,
                    label=step.intent,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
            emitted_step_ids.append(step.id)
            emitted_bindings.add(binding)

        source_lines.extend(
            [
                "def build_model():",
                "    state = None",
            ]
        )
        source_lines.extend(f"    state = {step_id}(state)" for step_id in emitted_step_ids)
        source_lines.extend(["    return state", ""])

        if not emitted_step_ids:
            diagnostics.append(
                CompileDiagnostic(
                    level="error",
                    code="no_compiled_steps",
                    message="No plan step compiled to source, so build_model() has nothing to run.",
                )
            )

        source = "\n".join(source_lines).strip() + "\n"
        whitelist_findings = self.validator.validate(source)
        diagnostics.append(
            CompileDiagnostic(
                level="info",
                code="step_count",
                message=f"Compiled {len(emitted_step_ids)} of {len(plan.steps)} semantic steps.",
            )
        )
        return CompileResult(
            source=source,
            editable_regions=editable_regions,
            whitelist_findings=whitelist_findings,
            diagnostics=diagnostics,
        )

    def _clean_parameters(
        self, step: SemanticStep
    ) -> tuple[dict[str, Any], list[CompileDiagnostic]]:
        """Coerce the parameters a macro reads to numbers, reporting the ones that resist.

        ``emit_step_source`` reprs whatever it is given, so an unnoticed string
        produces syntactically valid source that raises deep inside CadQuery.
        Catching it here lets the diagnostic name the step and the key.

        Only the keys the macro's template actually reads are checked. A planner
        that tags a step with something like ``"material": "PLA"`` has not made a
        build error, and refusing the whole plan over it would be one. Keys the
        template reads and the step never supplied are reported in one diagnostic
        rather than one per build attempt.
        """
        used = macro_parameter_names(step.primitive_or_macro)
        cleaned: dict[str, Any] = {}
        problems: list[CompileDiagnostic] = []

        missing = sorted(used - set(step.parameters))
        if missing:
            problems.append(
                CompileDiagnostic(
                    level="error",
                    code="missing_parameter",
                    message=(
                        f"Step {step.id} does not supply {', '.join(missing)}. Macro "
                        f"{step.primitive_or_macro} needs {', '.join(sorted(used))}."
                    ),
                )
            )

        for key, value in step.parameters.items():
            if key not in used:
                continue
            if (step.primitive_or_macro, key) in TEXT_PARAMETERS:
                if isinstance(value, str) and value.strip():
                    cleaned[key] = value
                else:
                    problems.append(
                        CompileDiagnostic(
                            level="error",
                            code="non_text_parameter",
                            message=(
                                f"Step {step.id} parameter {key!r} is {value!r}. Macro "
                                f"{step.primitive_or_macro} needs a CadQuery selector string "
                                'there, such as ">Z".'
                            ),
                        )
                    )
                continue
            number = _as_number(value)
            if number is None:
                problems.append(
                    CompileDiagnostic(
                        level="error",
                        code="non_numeric_parameter",
                        message=(
                            f"Step {step.id} parameter {key!r} is {value!r}. Macro "
                            f"{step.primitive_or_macro} needs a plain number in millimetres "
                            "there."
                        ),
                    )
                )
                continue
            cleaned[key] = number
        return cleaned, problems
