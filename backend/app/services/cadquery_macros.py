from __future__ import annotations

from textwrap import dedent
from typing import Any


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def emit_step_source(step_id: str, macro: str, parameters: dict[str, Any]) -> str:
    params = {key: repr(value) for key, value in parameters.items()}
    body = MACRO_SOURCES[macro](params)
    return dedent(
        f"""
        def {step_id}(state):
        {indent(body, 4)}
        """
    ).strip()


def indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else "" for line in text.splitlines())


MACRO_SOURCES: dict[str, Any] = {
    "create_mug_body": lambda p: f"""
outer_radius = {p['outer_diameter']} / 2
return cq.Workplane("XY").circle(outer_radius).extrude({p['height']})
""".strip(),
    "hollow_mug_body": lambda p: f"""
return state.faces(">Z").shell(-{p['wall_thickness']})
""".strip(),
    "add_mug_handle": lambda p: f"""
handle_outer = (
    cq.Workplane("YZ")
    .center(({p['outer_diameter']} / 2) + ({p['offset']} / 2), {p['z_center']})
    .rect({p['handle_width']}, {p['handle_span']})
    .extrude({p['handle_thickness']} / 2, both=True)
)
handle_inner = (
    cq.Workplane("YZ")
    .center(({p['outer_diameter']} / 2) + ({p['offset']} / 2), {p['z_center']})
    .rect(max({p['handle_width']} - ({p['handle_thickness']} * 1.2), 1), max({p['handle_span']} - ({p['handle_thickness']} * 1.2), 1))
    .extrude({p['handle_thickness']}, both=True)
)
return state.union(handle_outer.cut(handle_inner))
""".strip(),
    "fillet_edges": lambda p: f"""
return state.edges({p['selector']}).fillet({p['radius']})
""".strip(),
    "create_l_bracket": lambda p: f"""
profile = [(0, 0), ({p['arm_width']}, 0), ({p['arm_width']}, {p['thickness']}), ({p['thickness']}, {p['thickness']}), ({p['thickness']}, {p['arm_height']}), (0, {p['arm_height']})]
return cq.Workplane("XY").polyline(profile).close().extrude({p['depth']})
""".strip(),
    "drill_mount_holes": lambda p: f"""
result = state
for x_pos, y_pos in [({p['thickness']} / 2, {p['hole_margin']}), ({p['hole_margin']}, {p['thickness']} / 2)]:
    result = result.faces(">Z").workplane().center(x_pos, y_pos).hole({p['hole_diameter']})
return result
""".strip(),
    "create_project_box_shell": lambda p: f"""
outer = cq.Workplane("XY").box({p['width']}, {p['depth']}, {p['height']})
inner = cq.Workplane("XY").box({p['width']} - ({p['wall_thickness']} * 2), {p['depth']} - ({p['wall_thickness']} * 2), {p['height']} - {p['wall_thickness']}).translate((0, 0, {p['wall_thickness']} / 2))
return outer.cut(inner)
""".strip(),
    "add_standoffs": lambda p: f"""
result = state
offset_x = ({p['width']} / 2) - {p['wall_thickness']} - {p['standoff_radius']}
offset_y = ({p['depth']} / 2) - {p['wall_thickness']} - {p['standoff_radius']}
for x_pos in (-offset_x, offset_x):
    for y_pos in (-offset_y, offset_y):
        standoff = cq.Workplane("XY").center(x_pos, y_pos).circle({p['standoff_radius']}).extrude({p['standoff_height']}).faces(">Z").workplane().hole({p['screw_diameter']})
        result = result.union(standoff)
return result
""".strip(),
    "create_phone_stand": lambda p: f"""
base = cq.Workplane("XY").box({p['base_width']}, {p['base_depth']}, {p['base_thickness']})
back = cq.Workplane("XY").box({p['base_width']}, {p['back_thickness']}, {p['back_height']}).translate((0, ({p['base_depth']} / 2) - ({p['back_thickness']} / 2), ({p['back_height']} / 2))).rotate((0, ({p['base_depth']} / 2), 0), (1, ({p['base_depth']} / 2), 0), -{p['back_angle']})
return base.union(back)
""".strip(),
    "add_retention_lip": lambda p: f"""
lip = cq.Workplane("XY").box({p['lip_width']}, {p['lip_depth']}, {p['lip_height']}).translate((0, -({p['base_depth']} / 2) + ({p['lip_depth']} / 2), ({p['lip_height']} / 2) + ({p['base_thickness']} / 2)))
return state.union(lip)
""".strip(),
    "create_bottle_cap": lambda p: f"""
outer = cq.Workplane("XY").circle({p['outer_diameter']} / 2).extrude({p['height']})
inner = cq.Workplane("XY").circle(({p['outer_diameter']} / 2) - {p['wall_thickness']}).extrude({p['height']} - {p['top_thickness']}).translate((0, 0, {p['top_thickness']}))
return outer.cut(inner)
""".strip(),
    "add_grip_cutouts": lambda p: f"""
result = state
for index in range(int({p['groove_count']})):
    cutter = cq.Workplane("XY").box({p['groove_width']}, {p['groove_depth']}, {p['height']}).translate((({p['outer_diameter']} / 2) - ({p['groove_depth']} / 2), 0, {p['height']} / 2)).rotate((0, 0, 0), (0, 0, 1), index * (360 / {p['groove_count']}))
    result = result.cut(cutter)
return result
""".strip(),
}


SUPPORTED_MACROS = set(MACRO_SOURCES)


def default_postcondition(macro: str) -> str:
    return {
        "create_mug_body": "Outer cylinder exists with target height and outer diameter.",
        "hollow_mug_body": "Cup interior is hollowed with requested wall thickness.",
        "add_mug_handle": "Handle bridges body with requested width and thickness.",
        "fillet_edges": "Selected edges are rounded.",
        "create_l_bracket": "L bracket profile extruded to target depth.",
        "drill_mount_holes": "Mount holes exist on both arms.",
        "create_project_box_shell": "Enclosure shell exists with requested wall thickness.",
        "add_standoffs": "Internal standoffs are added near the corners.",
        "create_phone_stand": "Base and back support create a phone stand silhouette.",
        "add_retention_lip": "Front lip prevents device sliding.",
        "create_bottle_cap": "Cap body exists with hollow interior and top thickness.",
        "add_grip_cutouts": "Grip cutouts are repeated around the perimeter.",
    }[macro]


def macro_parameters_for_prompt(kind: str, prompt_parameters: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == "mug":
        outer_diameter = _float(prompt_parameters.get("diameter"), 86)
        height = _float(prompt_parameters.get("height"), 96)
        wall = _float(prompt_parameters.get("wall_thickness"), 4)
        return [
            {
                "id": "create_outer_body",
                "intent": "Create the outer mug body as a cylinder.",
                "primitive_or_macro": "create_mug_body",
                "parameters": {"outer_diameter": outer_diameter, "height": height},
            },
            {
                "id": "hollow_body",
                "intent": "Hollow the mug body while keeping a sturdy wall.",
                "primitive_or_macro": "hollow_mug_body",
                "depends_on": ["create_outer_body"],
                "parameters": {"wall_thickness": wall},
            },
            {
                "id": "add_handle",
                "intent": "Add a blocky handle that can be refined later.",
                "primitive_or_macro": "add_mug_handle",
                "depends_on": ["hollow_body"],
                "parameters": {
                    "outer_diameter": outer_diameter,
                    "handle_width": 28,
                    "handle_span": height * 0.48,
                    "handle_thickness": 12,
                    "offset": 24,
                    "z_center": height * 0.52,
                },
            },
        ]
    if kind == "l_bracket":
        return [
            {
                "id": "create_bracket",
                "intent": "Create the L bracket body from a 2D profile.",
                "primitive_or_macro": "create_l_bracket",
                "parameters": {"arm_width": 80, "arm_height": 80, "thickness": 12, "depth": 30},
            },
            {
                "id": "add_holes",
                "intent": "Drill mounting holes into both arms.",
                "primitive_or_macro": "drill_mount_holes",
                "depends_on": ["create_bracket"],
                "parameters": {"thickness": 12, "hole_margin": 25, "hole_diameter": 6},
            },
        ]
    if kind == "project_box":
        return [
            {
                "id": "create_shell",
                "intent": "Create a single-part enclosure shell.",
                "primitive_or_macro": "create_project_box_shell",
                "parameters": {"width": 120, "depth": 80, "height": 48, "wall_thickness": 3},
            },
            {
                "id": "add_standoffs",
                "intent": "Add internal standoffs for fasteners or a PCB.",
                "primitive_or_macro": "add_standoffs",
                "depends_on": ["create_shell"],
                "parameters": {
                    "width": 120,
                    "depth": 80,
                    "wall_thickness": 3,
                    "standoff_radius": 5,
                    "standoff_height": 18,
                    "screw_diameter": 3,
                },
            },
        ]
    if kind == "phone_stand":
        return [
            {
                "id": "create_stand",
                "intent": "Create the base and leaning backrest.",
                "primitive_or_macro": "create_phone_stand",
                "parameters": {
                    "base_width": 74,
                    "base_depth": 92,
                    "base_thickness": 8,
                    "back_thickness": 8,
                    "back_height": 110,
                    "back_angle": 68,
                },
            },
            {
                "id": "add_lip",
                "intent": "Add a small front retention lip.",
                "primitive_or_macro": "add_retention_lip",
                "depends_on": ["create_stand"],
                "parameters": {
                    "base_width": 74,
                    "base_depth": 92,
                    "base_thickness": 8,
                    "lip_width": 74,
                    "lip_depth": 10,
                    "lip_height": 12,
                },
            },
        ]
    return [
        {
            "id": "create_cap",
            "intent": "Create the cap body with a hollow interior.",
            "primitive_or_macro": "create_bottle_cap",
            "parameters": {"outer_diameter": 34, "height": 20, "wall_thickness": 2.4, "top_thickness": 3},
        },
        {
            "id": "add_grip",
            "intent": "Add grip cutouts around the perimeter.",
            "primitive_or_macro": "add_grip_cutouts",
            "depends_on": ["create_cap"],
            "parameters": {
                "outer_diameter": 34,
                "height": 20,
                "groove_count": 18,
                "groove_width": 2.4,
                "groove_depth": 1.2,
            },
        },
    ]

