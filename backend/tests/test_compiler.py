from app.models.schemas import SemanticBuildPlan, SemanticStep
from app.services.compilers.cadquery_compiler import CadQueryCompiler
from app.services.validation.source_validator import SourceValidator


def test_compiler_returns_step_functions_and_regions() -> None:
    compiler = CadQueryCompiler(SourceValidator())
    plan = SemanticBuildPlan(
        summary="Create a simple mug body.",
        steps=[
            SemanticStep(
                id="create_outer_body",
                intent="Create body",
                primitive_or_macro="create_mug_body",
                parameters={"outer_diameter": 80, "height": 90},
                depends_on=[],
                postcondition="Body exists",
            )
        ],
    )

    result = compiler.compile(plan)

    assert "def create_outer_body" in result.source
    assert result.editable_regions[0].step_id == "create_outer_body"
    assert result.whitelist_findings

