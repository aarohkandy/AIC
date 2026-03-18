from __future__ import annotations

import json

from app.models.schemas import DesignBrief


SUPPORTED_MACROS = [
    "create_mug_body",
    "hollow_mug_body",
    "add_mug_handle",
    "create_l_bracket",
    "drill_mount_holes",
    "create_project_box_shell",
    "add_standoffs",
    "create_phone_stand",
    "add_retention_lip",
    "create_bottle_cap",
    "add_grip_cutouts",
    "manual_feature",
]


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
- Use one of the listed primitive_or_macro values when it clearly fits. If it does not fit, use manual_feature.
- Avoid vague words like "roughly", "somewhere", "nice looking", or "eyeball it".
- Never output template placeholders like {{value}}, <value>, or TBD. Use concrete numeric values.
- For common objects, prefer these macro sequences when they fit:
  - mug or cup: create_mug_body -> hollow_mug_body -> add_mug_handle
  - bracket: create_l_bracket -> drill_mount_holes
  - project box or enclosure: create_project_box_shell -> add_standoffs
  - phone stand: create_phone_stand -> add_retention_lip
  - bottle cap: create_bottle_cap -> add_grip_cutouts
- Make manual_instructions actionable and short.
""".strip()


def build_local_planner_prompt(brief: DesignBrief) -> str:
    return f"""
Design brief JSON:
{brief.model_dump_json(indent=2)}

Allowed primitive_or_macro values:
{json.dumps(SUPPORTED_MACROS)}

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
- Use {brief.units} as the unit system unless the brief clearly overrides it.
- Do not wrap the JSON in markdown.

Helpful defaults when the brief is vague:
- mug: outer_diameter 86 mm, height 96 mm, wall_thickness 4 mm, handle_width 28 mm, handle_span 46 mm, handle_thickness 12 mm, offset 24 mm, z_center 50 mm
""".strip()
