# Planner Coverage

The five shape families the compiler can build, and how a prompt is read for
dimensions. The short version is in the [README](../README.md).

## The Five Families

Geometry comes from a fixed macro library, so the compiler can only *build* the
shapes it has recipes for. Five families are modelled end to end:

- mug (`mug`, `cup`): outer body, shell, blocky handle
- L bracket (`bracket`): L profile plus two mounting holes
- project box (`box`, `enclosure`): shelled enclosure plus four standoffs
- phone stand (`stand`): base slab, tilted backrest, front lip
- bottle cap (`bottle`, `cap`): hollow cap body plus perimeter grip cutouts

## Reading Dimensions from a Prompt

Prompts are scanned for diameter, height, width, depth and wall thickness, in
either word order: `86 mm diameter` and `diameter 86 mm` both read, as do
`86 mm across`, `walls 3 mm thick` and `2.5 mm thickness`. A radius is doubled
into a diameter, since that is what the recipes are written against, and the
assumptions say it was doubled.

`200x150x60 mm` is read as three dimensions at once, spaced or not. Nothing in
that form says which number is which axis, so the scan guesses width, depth,
height in that order and the plan states the guess:
`Read "200x150x60" as width x depth x height.` An axis named anywhere else in
the prompt wins over the guess, and then the note lists only the axes the chain
was actually read for. Two numbers are read as width by depth, but only with a
unit on them, because `2x4 mounting holes` is a count and not a size. Four
numbers are not read at all, since at that point the scan does not know what it
is looking at. Length is deliberately not read either: no recipe has a length
parameter, and which axis it means changes from family to family.

A recipe uses the dimensions it has parameters for, and the plan's assumptions
say what became of the rest: which dimensions this recipe has no parameter for,
which ones fell back to a category default, and any value that had to be
clamped to keep the solid buildable. Ask for a 20 mm box with 15 mm walls and
you get 8 mm walls and a line saying so, because 15 mm walls cut a negative
cavity.

Every plan is in millimetres. A prompt or form figure given in cm or inches is
converted on the way in and the assumptions name the original unit. Units are
read spelled out as well as abbreviated, so `3.4 in tall` and `86 millimetres`
both land. A bare `in` sitting directly in front of a dimension word is the
preposition rather than the unit, which is what makes `86 in diameter` read as
86 mm; write `3.4 inches in diameter` when the unit is what you mean. A zero or
negative dimension cannot be extruded, so it falls back to the category default
and the plan says which one it used.

## Shapes Outside the Library

Anything outside those five families is not recognized. The plan summary and
the assumptions both say so, and the planner still hands back the closest
recipe as a stand-in, so the workplanes, locations, sizes and sketch
constraints remain a usable manual CAD recipe. It will not quietly call a
bottle cap a teapot.

## Steps No Macro Fits

When the local AI planner writes a step no macro fits, it marks it
`manual_feature`. Those compile to a pass-through so the rest of the plan still
builds, and the compile diagnostics name the step and point at its manual
instructions.
