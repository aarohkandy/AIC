from __future__ import annotations

import math
from functools import cache
from typing import Any

# A wall (or boss, or lip) that eats more than this share of the smallest
# dimension it sits inside leaves nothing to cut away, and the macro emits a
# negative length or a self-intersecting profile. Requests past it are clamped.
MAX_FEATURE_FRACTION = 0.4


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _floor_to(value: float, places: int) -> float:
    """Largest number with `places` decimals that is not greater than `value`.

    `math.floor(value * 100) / 100` is the obvious spelling and it is wrong:
    17.2 * 100 is 1719.9999999999998 in binary floating point, so it answers
    17.19. Rounding to nearest and stepping back only when that overshoots
    leaves the exact cases exact.
    """
    rounded = round(value, places)
    if rounded > value:
        rounded = round(rounded - 10.0**-places, places)
    return rounded


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


class _ParameterProbe(dict):
    """Parameter dict stand-in that notes every key a macro template asks for."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: set[str] = set()

    def __missing__(self, key: str) -> str:
        self.seen.add(key)
        return "1"


@cache
def macro_parameter_names(macro: str) -> frozenset[str]:
    """Return the parameter names a macro's source template actually reads.

    Asking the template beats keeping a second list beside it. The two macro
    lists in this project already drifted apart once, and a planner is free to
    hang extra keys on a step that the recipe has no use for.
    """
    probe = _ParameterProbe()
    MACRO_SOURCES[macro](probe)
    return frozenset(probe.seen)


class _PromptDimensions:
    """The dimensions a request supplied, plus a record of what happened to them.

    Each recipe reads the handful of dimensions it has parameters for, falls
    back to a category default for the rest, and clamps anything that would
    produce a solid CadQuery cannot build. All three are worth telling the user
    about, so they are collected here instead of being discovered at build time.
    """

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values
        self._read: set[str] = set()
        self._defaulted: list[str] = []
        self._notes: list[str] = []

    def get(self, name: str, default: float) -> float:
        self._read.add(name)
        value = self._values.get(name)
        if value is None:
            if name not in self._defaulted:
                self._defaulted.append(name)
            return float(default)
        number = _float(value, default)
        if not math.isfinite(number) or number <= 0:
            # Reachable three ways: the form's number inputs have no minimum,
            # "0 mm across" parses, and JSON carries the bare NaN and Infinity
            # literals that pydantic accepts by default. None of them extrudes
            # to a solid, and the clamps below only ever cap a value, so this is
            # where it has to be caught. NaN in particular has to die here:
            # every comparison against it is False, so it slips through `clamp`
            # untouched and takes math.floor(math.log10(...)) down with it.
            self._notes.append(
                f"A {name.replace('_', ' ')} of {number:g} mm cannot be built, so the "
                f"{float(default):g} mm default was used instead."
            )
            return float(default)
        return number

    def clamp(self, value: float, ceiling: float, label: str, reason: str) -> float:
        """Cap a feature at what the geometry around it can hold, and say so.

        Every ceiling handed to this method is a fraction of a dimension `get`
        has already forced positive, and each recipe leaves itself a margin, so
        the ceiling is positive and so is the result.
        """
        if value <= ceiling:
            return value
        # Round down, never to nearest. 0.045 to nearest comes back as 0.05,
        # which is over the ceiling this call exists to enforce; the previous
        # version floored at 0.1 mm and overshot outright, so a 0.1 mm
        # enclosure kept its 0.1 mm walls and cut a negative cavity out of
        # itself. Hundredths is the resolution the size notes read at, and a
        # ceiling tighter than that gets quantised on its own scale so the
        # feature still has a positive size.
        places = max(2, 2 - math.floor(math.log10(ceiling)))
        clamped = _floor_to(ceiling, places)
        self._notes.append(
            f"{label} of {value:g} mm {reason}, so it was reduced to {clamped:g} mm."
        )
        return clamped

    def notes(self) -> list[str]:
        notes = list(self._notes)
        for name in self._values:
            if name in self._read:
                continue
            label = name.replace("_", " ")
            stated = _float(self._values[name], 0)
            notes.append(
                f"This recipe has no {label} parameter, so the {stated:g} mm {label} "
                "in the request was ignored."
            )
        if self._defaulted:
            missing = [name.replace("_", " ") for name in self._defaulted]
            listed = (
                missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + f" and {missing[-1]}"
            )
            notes.append(f"Request did not state {listed}, so category defaults were applied.")
        return notes


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


def macro_parameters_for_prompt(
    kind: str, prompt_parameters: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build a family's staged steps, plus notes on what became of each dimension.

    The second element is everything the plan should own up to: dimensions the
    request stated that this family has no parameter for, dimensions it never
    stated, and any value clamped to keep the solid buildable.
    """
    dims = _PromptDimensions(prompt_parameters)
    if kind == "mug":
        steps = _mug_steps(dims)
    elif kind == "l_bracket":
        steps = _l_bracket_steps(dims)
    elif kind == "project_box":
        steps = _project_box_steps(dims)
    elif kind == "phone_stand":
        steps = _phone_stand_steps(dims)
    else:
        steps = _bottle_cap_steps(dims)
    return steps, dims.notes()


def _mug_steps(dims: _PromptDimensions) -> list[dict[str, Any]]:
    outer_diameter = dims.get("diameter", 86)
    height = dims.get("height", 96)
    wall = dims.clamp(
        dims.get("wall_thickness", 4),
        MAX_FEATURE_FRACTION * min(outer_diameter / 2, height),
        "Wall thickness",
        f"leaves no cavity in a {outer_diameter:g} by {height:g} mm mug",
    )
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
                f"Outer diameter = {outer_diameter:g} mm.",
                f"Extrude height = {height:g} mm.",
            ],
            "sketch_constraints": [
                "Constrain the circle center coincident with the origin.",
                f"Apply one diameter dimension of {outer_diameter:g} mm so the sketch is fully defined.",
            ],
            "manual_instructions": [
                "Sketch one centered circle for the mug exterior.",
                f"Extrude the profile upward by {height:g} mm as a new solid.",
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
                f"Wall thickness = {wall:g} mm.",
            ],
            "sketch_constraints": [
                "No new sketch is required for this step.",
                "Preserve the body axis from the previous step so the hollow is concentric.",
            ],
            "manual_instructions": [
                f"Use a shell feature and remove the top face with {wall:g} mm thickness.",
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


def _l_bracket_steps(dims: _PromptDimensions) -> list[dict[str, Any]]:
    arm_width = dims.get("width", 80)
    arm_height = dims.get("height", 80)
    depth = dims.get("depth", 30)
    thickness = dims.clamp(
        12,
        MAX_FEATURE_FRACTION * min(arm_width, arm_height),
        "Bracket thickness",
        f"folds back on itself in a {arm_width:g} by {arm_height:g} mm L profile",
    )
    hole_margin = dims.clamp(
        25,
        0.5 * min(arm_width, arm_height),
        "Hole margin",
        "would put the mounting holes off the end of the arms",
    )
    hole_diameter = dims.clamp(6, thickness, "Hole diameter", "is wider than the bracket is thick")
    return [
        {
            "id": "create_bracket",
            "intent": "Create the L bracket body from a 2D profile.",
            "primitive_or_macro": "create_l_bracket",
            "workplane": "XY",
            "location_notes": [
                "Sketch the L profile on the XY plane with one corner at the origin."
            ],
            "size_notes": [
                f"Arm width = {arm_width:g} mm.",
                f"Arm height = {arm_height:g} mm.",
                f"Thickness = {thickness:g} mm.",
                f"Depth = {depth:g} mm.",
            ],
            "sketch_constraints": [
                "Lock one profile corner to the origin.",
                "Dimension every profile segment so the L shape is fully defined.",
            ],
            "manual_instructions": [
                f"Sketch the L profile as a closed polyline and extrude it {depth:g} mm."
            ],
            "parameters": {
                "arm_width": arm_width,
                "arm_height": arm_height,
                "thickness": thickness,
                "depth": depth,
            },
        },
        {
            "id": "add_holes",
            "intent": "Drill mounting holes into both arms.",
            "primitive_or_macro": "drill_mount_holes",
            "workplane": "Top faces",
            "location_notes": [
                "Place hole centers on each arm using equal offsets from the inside corner."
            ],
            "size_notes": [
                f"Hole margin = {hole_margin:g} mm.",
                f"Hole diameter = {hole_diameter:g} mm.",
            ],
            "sketch_constraints": [
                "Dimension each hole center from two bracket edges so each point is fully defined.",
            ],
            "manual_instructions": [
                "Create one hole on each arm with mirrored placement from the inside corner."
            ],
            "depends_on": ["create_bracket"],
            "parameters": {
                "thickness": thickness,
                "hole_margin": hole_margin,
                "hole_diameter": hole_diameter,
            },
        },
    ]


def _project_box_steps(dims: _PromptDimensions) -> list[dict[str, Any]]:
    width = dims.get("width", 120)
    depth = dims.get("depth", 80)
    height = dims.get("height", 48)
    wall = dims.clamp(
        dims.get("wall_thickness", 3),
        MAX_FEATURE_FRACTION * min(width, depth, height),
        "Wall thickness",
        f"would swallow a {width:g} by {depth:g} by {height:g} mm enclosure",
    )
    standoff_radius = dims.clamp(
        5,
        0.5 * ((min(width, depth) / 2) - wall),
        "Standoff radius",
        "does not fit between the enclosure walls",
    )
    standoff_height = dims.clamp(
        18, height - wall, "Standoff height", "is taller than the cavity is deep"
    )
    screw_diameter = dims.clamp(
        3, standoff_radius, "Screw diameter", "would drill the standoff away"
    )
    return [
        {
            "id": "create_shell",
            "intent": "Create a single-part enclosure shell.",
            "primitive_or_macro": "create_project_box_shell",
            "workplane": "XY",
            "location_notes": ["Center the outer box at the origin on the XY plane."],
            "size_notes": [
                f"Width = {width:g} mm.",
                f"Depth = {depth:g} mm.",
                f"Height = {height:g} mm.",
                f"Wall thickness = {wall:g} mm.",
            ],
            "sketch_constraints": [
                "If modeled from sketches, center rectangles on the origin and dimension all sides."
            ],
            "manual_instructions": [
                "Create the outer box first, then subtract the inner cavity while preserving a bottom floor."
            ],
            "parameters": {
                "width": width,
                "depth": depth,
                "height": height,
                "wall_thickness": wall,
            },
        },
        {
            "id": "add_standoffs",
            "intent": "Add internal standoffs for fasteners or a PCB.",
            "primitive_or_macro": "add_standoffs",
            "workplane": "XY",
            "location_notes": ["Place four standoffs symmetrically near the internal corners."],
            "size_notes": [
                f"Standoff radius = {standoff_radius:g} mm.",
                f"Standoff height = {standoff_height:g} mm.",
                f"Screw diameter = {screw_diameter:g} mm.",
            ],
            "sketch_constraints": [
                "Dimension each standoff center from the enclosure walls so the pattern is symmetric and fully defined."
            ],
            "manual_instructions": [
                "Create four circular bosses, then cut a centered pilot hole in each one."
            ],
            "depends_on": ["create_shell"],
            "parameters": {
                "width": width,
                "depth": depth,
                "wall_thickness": wall,
                "standoff_radius": standoff_radius,
                "standoff_height": standoff_height,
                "screw_diameter": screw_diameter,
            },
        },
    ]


def _phone_stand_steps(dims: _PromptDimensions) -> list[dict[str, Any]]:
    base_width = dims.get("width", 74)
    base_depth = dims.get("depth", 92)
    back_height = dims.get("height", 110)
    base_thickness = dims.clamp(
        8,
        MAX_FEATURE_FRACTION * back_height,
        "Base thickness",
        "is most of the stand's height",
    )
    back_thickness = dims.clamp(
        8,
        MAX_FEATURE_FRACTION * base_depth,
        "Back thickness",
        "is most of the base depth",
    )
    lip_depth = dims.clamp(
        10, MAX_FEATURE_FRACTION * base_depth, "Lip depth", "would cover the whole base"
    )
    lip_height = dims.clamp(
        12,
        MAX_FEATURE_FRACTION * back_height,
        "Lip height",
        "would reach the top of the backrest",
    )
    return [
        {
            "id": "create_stand",
            "intent": "Create the base and leaning backrest.",
            "primitive_or_macro": "create_phone_stand",
            "workplane": "XY",
            "location_notes": [
                "Center the base on the origin and attach the backrest at the rear edge."
            ],
            "size_notes": [
                f"Base width = {base_width:g} mm.",
                f"Base depth = {base_depth:g} mm.",
                f"Base thickness = {base_thickness:g} mm.",
                f"Back height = {back_height:g} mm.",
                "Back angle = 68 deg.",
            ],
            "sketch_constraints": [
                "Dimension the base rectangle and the backrest hinge/tilt reference so the profile is fully defined."
            ],
            "manual_instructions": [
                "Create the base slab first, then add the tilted back support as a second solid."
            ],
            "parameters": {
                "base_width": base_width,
                "base_depth": base_depth,
                "base_thickness": base_thickness,
                "back_thickness": back_thickness,
                "back_height": back_height,
                "back_angle": 68,
            },
        },
        {
            "id": "add_lip",
            "intent": "Add a small front retention lip.",
            "primitive_or_macro": "add_retention_lip",
            "workplane": "XY",
            "location_notes": ["Place the lip centered on the front edge of the base."],
            "size_notes": [
                f"Lip depth = {lip_depth:g} mm.",
                f"Lip height = {lip_height:g} mm.",
            ],
            "sketch_constraints": [
                "Center the lip profile on the stand midline and dimension its offset from the front edge."
            ],
            "manual_instructions": [
                "Add a centered lip feature at the front of the stand to stop the phone from sliding."
            ],
            "depends_on": ["create_stand"],
            "parameters": {
                "base_depth": base_depth,
                "base_thickness": base_thickness,
                "lip_width": base_width,
                "lip_depth": lip_depth,
                "lip_height": lip_height,
            },
        },
    ]


def _bottle_cap_steps(dims: _PromptDimensions) -> list[dict[str, Any]]:
    outer_diameter = dims.get("diameter", 34)
    height = dims.get("height", 20)
    wall = dims.clamp(
        dims.get("wall_thickness", 2.4),
        MAX_FEATURE_FRACTION * (outer_diameter / 2),
        "Wall thickness",
        f"leaves no bore in a {outer_diameter:g} mm cap",
    )
    top_thickness = dims.clamp(
        3,
        MAX_FEATURE_FRACTION * height,
        "Top thickness",
        f"is most of a {height:g} mm cap",
    )
    groove_depth = dims.clamp(
        1.2, 0.5 * wall, "Groove depth", "would cut straight through the cap wall"
    )
    return [
        {
            "id": "create_cap",
            "intent": "Create the cap body with a hollow interior.",
            "primitive_or_macro": "create_bottle_cap",
            "workplane": "XY",
            "location_notes": ["Sketch concentric circles on the XY plane centered at the origin."],
            "size_notes": [
                f"Outer diameter = {outer_diameter:g} mm.",
                f"Height = {height:g} mm.",
                f"Wall thickness = {wall:g} mm.",
                f"Top thickness = {top_thickness:g} mm.",
            ],
            "sketch_constraints": [
                "Make both circle centers coincident with the origin and dimension both diameters."
            ],
            "manual_instructions": [
                "Create the outer cylinder first, then remove the inner cylinder while leaving the top thickness intact."
            ],
            "parameters": {
                "outer_diameter": outer_diameter,
                "height": height,
                "wall_thickness": wall,
                "top_thickness": top_thickness,
            },
        },
        {
            "id": "add_grip",
            "intent": "Add grip cutouts around the perimeter.",
            "primitive_or_macro": "add_grip_cutouts",
            "workplane": "XY",
            "location_notes": ["Array the grip cutters around the cap center axis."],
            "size_notes": [
                "Groove count = 18.",
                "Groove width = 2.4 mm.",
                f"Groove depth = {groove_depth:g} mm.",
            ],
            "sketch_constraints": [
                "Define one groove profile fully, then pattern it evenly around the center axis."
            ],
            "manual_instructions": [
                "Create one grip cutout and circular-pattern it around the cap."
            ],
            "depends_on": ["create_cap"],
            "parameters": {
                "outer_diameter": outer_diameter,
                "height": height,
                "groove_count": 18,
                "groove_width": 2.4,
                "groove_depth": groove_depth,
            },
        },
    ]
