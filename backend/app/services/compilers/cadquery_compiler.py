from __future__ import annotations

from app.models.schemas import CompileDiagnostic, CompileResult, EditableRegion, SemanticBuildPlan
from app.services.cadquery_macros import SUPPORTED_MACROS, emit_step_source
from app.services.validation.source_validator import SourceValidator


class CadQueryCompiler:
    def __init__(self, validator: SourceValidator) -> None:
        self.validator = validator

    def compile(self, plan: SemanticBuildPlan) -> CompileResult:
        diagnostics: list[CompileDiagnostic] = []
        source_lines = [
            "import cadquery as cq",
            "",
            "def export_artifacts(result, step_path, stl_path, glb_path):",
            '    result.export(step_path)',
            '    result.export(stl_path, tolerance=0.05, angularTolerance=0.1)',
            "    assembly = cq.Assembly()",
            '    assembly.add(result, name="part", color=cq.Color(0.8, 0.8, 0.82))',
            "    assembly.export(glb_path)",
            "",
        ]
        editable_regions: list[EditableRegion] = []

        for step in plan.steps:
            if step.primitive_or_macro not in SUPPORTED_MACROS:
                diagnostics.append(
                    CompileDiagnostic(
                        level="error",
                        code="unsupported_macro",
                        message=f"Macro {step.primitive_or_macro} is not supported by the compiler.",
                    )
                )
                continue

            start_line = len(source_lines) + 1
            step_source = emit_step_source(step.id, step.primitive_or_macro, step.parameters)
            step_lines = step_source.splitlines()
            source_lines.extend(step_lines)
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

        source_lines.extend(
            [
                "def build_model():",
                "    state = None",
            ]
        )
        for step in plan.steps:
            source_lines.append(f"    state = {step.id}(state)")
        source_lines.extend(["    return state", ""])

        source = "\n".join(source_lines).strip() + "\n"
        whitelist_findings = self.validator.validate(source)
        diagnostics.extend(
            CompileDiagnostic(level="info", code="step_count", message=f"Compiled {len(plan.steps)} semantic steps.")
            for _ in [0]
        )
        return CompileResult(
            source=source,
            editable_regions=editable_regions,
            whitelist_findings=whitelist_findings,
            diagnostics=diagnostics,
        )
