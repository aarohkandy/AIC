from app.models.schemas import SemanticBuildPlan, SemanticStep
from app.services.revision.revision_engine import RevisionEngine


def test_revision_engine_maps_handle_thickness_change() -> None:
    engine = RevisionEngine()
    plan = SemanticBuildPlan(
        summary="Create mug then add handle.",
        steps=[
            SemanticStep(
                id="add_handle",
                intent="Add handle",
                primitive_or_macro="add_mug_handle",
                parameters={"handle_thickness": 12, "handle_width": 30},
                depends_on=[],
                postcondition="Handle exists",
            )
        ],
    )

    revision, patch = engine.interpret("Make the handle thickness 10 mm.", plan)

    assert revision.operation == "update_parameter"
    assert revision.confidence_score >= 0.8
    assert patch is not None
    assert patch.parameter_updates["handle_thickness"] == 10.0
