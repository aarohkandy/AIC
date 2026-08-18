from __future__ import annotations

from app.models.schemas import DesignBrief
from app.services.cadquery_macros import SUPPORTED_MACROS as COMPILER_MACROS
from app.services.cadquery_macros import macro_parameter_names

# Derived from the compiler's macro library so the two lists cannot drift apart.
# manual_feature is the deliberate escape hatch: it has no macro source, and the
# compiler turns it into a pass-through plus a warning so the rest of the plan
# still builds.
SUPPORTED_MACROS = sorted(COMPILER_MACROS) + ["manual_feature"]


def _catalog_line(macro: str) -> str:
    if macro not in COMPILER_MACROS:
        return f"- {macro}: no parameters, and nothing is modelled for it"
    return f"- {macro}: {', '.join(sorted(macro_parameter_names(macro)))}"


# Naming the macro is only half the contract. A step that picks create_mug_body
# and calls its parameter "diameter" fails to compile just as hard as one that
# invents a macro name, so the prompt spells out the keys each recipe uses,
# taken from the recipes themselves.
MACRO_CATALOG = "\n".join(_catalog_line(macro) for macro in SUPPORTED_MACROS)


LOCAL_PLANNER_SYSTEM_PROMPT = """
You are a local AI CAD planning assistant.

Your job is to turn a natural-language object request into a human-reproducible CAD build plan for a single parametric part.

Return JSON only. It must validate against the provided SemanticBuildPlan schema.

Planning rules:
- Think in start -> middle -> end order.
- Prefer 3 to 8 steps.
- Every step must describe where it happens using a workplane and location notes.
- Every step must describe exact sizes. If the user omitted dimensions, choose practical defaults and record them in assumptions and size_notes.
- Every sketch-oriented step must include sketch_constraints that would help a human fully define the sketch in Onshape or a similar CAD tool.
- Keep the plan single-part. No assemblies, fasteners, hinges, or multiple files.
- Use snake_case step ids.
- Use parameters for reusable numeric values.
- Use one of the listed primitive_or_macro values when it clearly fits. If it does not fit, use manual_feature. A manual_feature step is not modelled automatically, so its manual_instructions have to stand on their own.
- A macro step must carry every parameter listed for that macro, spelled exactly as listed. A missing or renamed key means the step does not compile and its geometry is left out of the model.
- Every parameter value must be a bare number, not a string. Write 86, never "86" or "86 mm"; units belong in size_notes.
- Avoid vague words like "roughly", "somewhere", "nice looking", or "eyeball it".
- Never output template placeholders like {{value}}, <value>, or TBD. Use concrete numeric values.
- For common objects, prefer these macro sequences when they fit:
  - mug or cup: create_mug_body -> hollow_mug_body -> add_mug_handle
  - bracket: create_l_bracket -> drill_mount_holes
  - project box or enclosure: create_project_box_shell -> add_standoffs
  - phone stand: create_phone_stand -> add_retention_lip
  - bottle cap: create_bottle_cap -> add_grip_cutouts
- fillet_edges only ever follows a step that already made a solid. Its selector parameter is a CadQuery edge selector string such as ">Z", and radius is a number.
- Make manual_instructions actionable and short.
""".strip()


def build_local_planner_prompt(brief: DesignBrief) -> str:
    return f"""
Design brief JSON:
{brief.model_dump_json(indent=2)}

Allowed primitive_or_macro values, and the parameters each one needs:
{MACRO_CATALOG}

Return a JSON object with exactly these top-level keys:
- summary
- assumptions
- parameters
- steps

Each step must include exactly these keys:
- id
- intent
- primitive_or_macro
- workplane
- location_notes
- size_notes
- sketch_constraints
- manual_instructions
- parameters
- depends_on
- postcondition

Important:
- The user wants a plan they can manually verify in CAD.
- Include workplane, location_notes, size_notes, sketch_constraints, and manual_instructions on every step.
- Write sketch_constraints so a human can fully define the sketch without guessing.
- Use concrete numbers everywhere. No placeholders or symbolic references inside strings.
- If the object can be approximated by known CAD macros, use them. Otherwise use manual_feature and still provide a strong manual recipe.
- Every parameter value is in millimetres, whatever unit the brief uses. The brief above is written in {brief.units}, so convert those figures to millimetres before you write them into parameters. Mention the original {brief.units} figure in size_notes if it helps a human follow along.
- Do not wrap the JSON in markdown.

Helpful defaults when the brief is vague:
- mug: outer_diameter 86 mm, height 96 mm, wall_thickness 4 mm, handle_width 28 mm, handle_span 46 mm, handle_thickness 12 mm, offset 24 mm, z_center 50 mm
""".strip()
