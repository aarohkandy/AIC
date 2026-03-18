from __future__ import annotations

from typing import Any


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def emit_step_source(step_id: str, macro: str, parameters: dict[str, Any]) -> str:
    params = {key: repr(value) for key, value in parameters.items()}
    body = MACRO_SOURCES[macro](params)
    return "\n".join(
        [
            f"def {step_id}(state):",
            indent(body, 4),
        ]
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
                "workplane": "XY",
                "location_notes": [
                    "Start a sketch on the Top or XY plane.",
                    "Place the outer circle center at the global origin.",
                ],
                "size_notes": [
                    f"Outer diameter = {outer_diameter} mm.",
                    f"Extrude height = {height} mm.",
                ],
                "sketch_constraints": [
                    "Constrain the circle center coincident with the origin.",
                    f"Apply one diameter dimension of {outer_diameter} mm so the sketch is fully defined.",
                ],
                "manual_instructions": [
                    "Sketch one centered circle for the mug exterior.",
                    f"Extrude the profile upward by {height} mm as a new solid.",
                ],
                "parameters": {"outer_diameter": outer_diameter, "height": height},
            },
            {
                "id": "hollow_body",
                "intent": "Hollow the mug body while keeping a sturdy wall.",
                "primitive_or_macro": "hollow_mug_body",
                "workplane": "Top face",
                "location_notes": [
                    "Select the top opening face of the cylinder.",
                    "Shell inward from that face so the bottom stays closed.",
                ],
                "size_notes": [
                    f"Wall thickness = {wall} mm.",
                ],
                "sketch_constraints": [
                    "No new sketch is required for this step.",
                    "Preserve the body axis from the previous step so the hollow is concentric.",
                ],
                "manual_instructions": [
                    f"Use a shell feature and remove the top face with {wall} mm thickness.",
                ],
                "depends_on": ["create_outer_body"],
                "parameters": {"wall_thickness": wall},
            },
            {
                "id": "add_handle",
                "intent": "Add a blocky handle that can be refined later.",
                "primitive_or_macro": "add_mug_handle",
                "workplane": "YZ",
                "location_notes": [
                    "Start a sketch on the Right or YZ plane.",
                    f"Center the handle sketch at X = {(outer_diameter / 2) + (24 / 2):.2f} mm from the mug axis and Z = {height * 0.52:.2f} mm from the base.",
                    "Keep the handle centered vertically around that anchor point.",
                ],
                "size_notes": [
                    "Handle width = 28 mm.",
                    f"Handle span = {height * 0.48:.2f} mm.",
                    "Handle thickness = 12 mm.",
                    "Handle offset from body = 24 mm.",
                ],
                "sketch_constraints": [
                    "Constrain the handle center point to the YZ reference plane.",
                    "Dimension the outer rectangle width and height.",
                    "Dimension the inner cutout relative to the outer rectangle so wall thickness stays consistent.",
                    "Keep the outer and inner rectangles concentric so the handle sketch is fully defined.",
                ],
                "manual_instructions": [
                    "Sketch an outer handle rectangle and a concentric inner cutout on the YZ plane.",
                    "Extrude the ring profile symmetrically to create the handle thickness.",
                    "Boolean-union the handle into the mug body.",
                ],
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
                "workplane": "XY",
                "location_notes": ["Sketch the L profile on the XY plane with one corner at the origin."],
                "size_notes": ["Arm width = 80 mm.", "Arm height = 80 mm.", "Thickness = 12 mm.", "Depth = 30 mm."],
                "sketch_constraints": [
                    "Lock one profile corner to the origin.",
                    "Dimension every profile segment so the L shape is fully defined.",
                ],
                "manual_instructions": ["Sketch the L profile as a closed polyline and extrude it 30 mm."],
                "parameters": {"arm_width": 80, "arm_height": 80, "thickness": 12, "depth": 30},
            },
            {
                "id": "add_holes",
                "intent": "Drill mounting holes into both arms.",
                "primitive_or_macro": "drill_mount_holes",
                "workplane": "Top faces",
                "location_notes": ["Place hole centers on each arm using equal offsets from the inside corner."],
                "size_notes": ["Hole margin = 25 mm.", "Hole diameter = 6 mm."],
                "sketch_constraints": [
                    "Dimension each hole center from two bracket edges so each point is fully defined.",
                ],
                "manual_instructions": ["Create one hole on each arm with mirrored placement from the inside corner."],
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
                "workplane": "XY",
                "location_notes": ["Center the outer box at the origin on the XY plane."],
                "size_notes": ["Width = 120 mm.", "Depth = 80 mm.", "Height = 48 mm.", "Wall thickness = 3 mm."],
                "sketch_constraints": ["If modeled from sketches, center rectangles on the origin and dimension all sides."],
                "manual_instructions": ["Create the outer box first, then subtract the inner cavity while preserving a bottom floor."],
                "parameters": {"width": 120, "depth": 80, "height": 48, "wall_thickness": 3},
            },
            {
                "id": "add_standoffs",
                "intent": "Add internal standoffs for fasteners or a PCB.",
                "primitive_or_macro": "add_standoffs",
                "workplane": "XY",
                "location_notes": ["Place four standoffs symmetrically near the internal corners."],
                "size_notes": ["Standoff radius = 5 mm.", "Standoff height = 18 mm.", "Screw diameter = 3 mm."],
                "sketch_constraints": ["Dimension each standoff center from the enclosure walls so the pattern is symmetric and fully defined."],
                "manual_instructions": ["Create four circular bosses, then cut a centered pilot hole in each one."],
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
                "workplane": "XY",
                "location_notes": ["Center the base on the origin and attach the backrest at the rear edge."],
                "size_notes": ["Base width = 74 mm.", "Base depth = 92 mm.", "Back height = 110 mm.", "Back angle = 68 deg."],
                "sketch_constraints": ["Dimension the base rectangle and the backrest hinge/tilt reference so the profile is fully defined."],
                "manual_instructions": ["Create the base slab first, then add the tilted back support as a second solid."],
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
                "workplane": "XY",
                "location_notes": ["Place the lip centered on the front edge of the base."],
                "size_notes": ["Lip depth = 10 mm.", "Lip height = 12 mm."],
                "sketch_constraints": ["Center the lip profile on the stand midline and dimension its offset from the front edge."],
                "manual_instructions": ["Add a centered lip feature at the front of the stand to stop the phone from sliding."],
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
            "workplane": "XY",
            "location_notes": ["Sketch concentric circles on the XY plane centered at the origin."],
            "size_notes": ["Outer diameter = 34 mm.", "Height = 20 mm.", "Wall thickness = 2.4 mm.", "Top thickness = 3 mm."],
            "sketch_constraints": ["Make both circle centers coincident with the origin and dimension both diameters."],
            "manual_instructions": ["Create the outer cylinder first, then remove the inner cylinder while leaving the top thickness intact."],
            "parameters": {"outer_diameter": 34, "height": 20, "wall_thickness": 2.4, "top_thickness": 3},
        },
        {
            "id": "add_grip",
            "intent": "Add grip cutouts around the perimeter.",
            "primitive_or_macro": "add_grip_cutouts",
            "workplane": "XY",
            "location_notes": ["Array the grip cutters around the cap center axis."],
            "size_notes": ["Groove count = 18.", "Groove width = 2.4 mm.", "Groove depth = 1.2 mm."],
            "sketch_constraints": ["Define one groove profile fully, then pattern it evenly around the center axis."],
            "manual_instructions": ["Create one grip cutout and circular-pattern it around the cap."],
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
