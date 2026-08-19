"""A fake ``cadquery`` module that the compiler's own output can be run against.

CadQuery is a conda-only dependency and is not installed here, so the only way
to find out whether a macro emits source that *runs* is to run it against a
stand-in. Every chained call is recorded, and the calls that take a length
refuse anything that is not finite and positive - that check is the point.
``box(0.1 - (0.1 * 2), ...)``, which this library really did emit for a 0.1 mm
enclosure, fails here instead of somewhere inside OpenCascade.

Nothing here computes geometry. ``Volume()`` and ``BoundingBox()`` answer fixed
numbers so ``runtime.py`` has something to read; they are not measurements.
"""

from __future__ import annotations

import math
import sys
import types
from pathlib import Path
from typing import Any

import pytest

Call = tuple[str, tuple[Any, ...], dict[str, Any]]

# Calls whose arguments are lengths. Each one needs a finite, strictly positive
# number to produce a solid. `center`, `translate` and `rotate` are absent
# because a coordinate or an angle of zero is ordinary.
LENGTH_CALLS = frozenset({"box", "circle", "extrude", "fillet", "hole", "rect"})

# `shell` is the one call that takes its length signed: `hollow_mug_body`
# negates the wall thickness to hollow inward. Only the sign is exempt - a
# shell of zero is still a wall that was clamped out of existence.
SIGNED_LENGTH_CALLS = frozenset({"shell"})

# Every Workplane method the macro library emits, and nothing else. Answering
# any name at all would make this stub agree with source that CadQuery would
# reject: a macro emitting `.hoel(...)` for `.hole(...)` still ran, still
# recorded a call, and quietly stopped being length-checked because
# LENGTH_CALLS is keyed by name. Grow this list when a macro learns a new call.
WORKPLANE_CALLS = frozenset(
    {
        "box",
        "center",
        "circle",
        "close",
        "cut",
        "edges",
        "extrude",
        "faces",
        "fillet",
        "hole",
        "polyline",
        "rect",
        "rotate",
        "shell",
        "translate",
        "union",
        "workplane",
    }
)

# What the stub reports for a solid. runtime.py needs a non-zero volume for its
# acceptance check and a bounding box it can serialize; neither number is
# measured from anything.
STUB_VOLUME = 42.0
STUB_BOUNDING_BOX = (86.0, 86.0, 96.0)


class NonPositiveLength(AssertionError):
    """A macro handed CadQuery a length it cannot build a solid from."""


def _check_length(call: str, label: str, value: Any, *, signed: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NonPositiveLength(f"{call}() got {label}={value!r}, which is not a length.")
    if not math.isfinite(value) or (value == 0 if signed else value <= 0):
        wanted = "non-zero" if signed else "positive"
        raise NonPositiveLength(
            f"{call}() got {label}={value!r}. A real CadQuery build needs a finite {wanted} "
            "length here, so whichever macro emitted this would have failed."
        )


def _check_call(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    signed = name in SIGNED_LENGTH_CALLS
    if not signed and name not in LENGTH_CALLS:
        return
    for index, value in enumerate(args):
        _check_length(name, f"argument {index}", value, signed=signed)
    for key, value in kwargs.items():
        # extrude(..., both=True) is a flag rather than a length.
        if not isinstance(value, bool):
            _check_length(name, key, value, signed=signed)


class _Solid:
    """What ``.val()`` returns: the three things runtime.py asks a solid, and no more."""

    def isValid(self) -> bool:
        return True

    def Volume(self) -> float:
        return STUB_VOLUME

    def BoundingBox(self) -> types.SimpleNamespace:
        xlen, ylen, zlen = STUB_BOUNDING_BOX
        return types.SimpleNamespace(xlen=xlen, ylen=ylen, zlen=zlen)


class RecordedShape:
    """Chainable stand-in for a cq.Workplane that logs and checks every call.

    ``val`` and ``export`` are spelled out because the runtime calls both by
    name: it measures each finished step and writes the step's STEP file into
    the build cache. The rest of ``WORKPLANE_CALLS`` falls through to
    ``__getattr__``; anything outside it raises ``AttributeError``, which is
    what the real class does.
    """

    def __init__(self, calls: list[Call]) -> None:
        self.calls = calls

    def __getattr__(self, name: str) -> Any:
        if name not in WORKPLANE_CALLS:
            raise AttributeError(
                f"cq.Workplane has no attribute {name!r}. The stub only answers the calls the "
                "macro library emits, so a misspelled one fails here instead of passing."
            )

        def record(*args: Any, **kwargs: Any) -> RecordedShape:
            _check_call(name, args, kwargs)
            self.calls.append((name, args, kwargs))
            return self

        return record

    def val(self) -> _Solid:
        self.calls.append(("val", (), {}))
        return _Solid()

    def export(self, path: str, **kwargs: Any) -> None:
        self.calls.append(("export", (path,), kwargs))
        Path(path).write_text("stub solid\n", encoding="utf-8")


class _Assembly:
    """The GLB side of ``export_artifacts``: collect one shape, write one file."""

    def __init__(self, calls: list[Call]) -> None:
        self.calls = calls

    def add(self, shape: Any, **kwargs: Any) -> _Assembly:
        self.calls.append(("Assembly.add", (shape,), kwargs))
        return self

    def export(self, path: str, **kwargs: Any) -> None:
        self.calls.append(("Assembly.export", (path,), kwargs))
        Path(path).write_text("stub glb\n", encoding="utf-8")


def make_module(calls: list[Call]) -> types.ModuleType:
    """Build the fake module, appending every call it sees to ``calls``."""
    module = types.ModuleType("cadquery")

    def workplane(plane: str) -> RecordedShape:
        calls.append(("Workplane", (plane,), {}))
        return RecordedShape(calls)

    def assembly() -> _Assembly:
        calls.append(("Assembly", (), {}))
        return _Assembly(calls)

    def color(*rgb: float) -> tuple[str, tuple[float, ...]]:
        calls.append(("Color", rgb, {}))
        return ("Color", rgb)

    def import_step(path: str) -> RecordedShape:
        calls.append(("importStep", (path,), {}))
        return RecordedShape(calls)

    module.Workplane = workplane  # type: ignore[attr-defined]
    module.Assembly = assembly  # type: ignore[attr-defined]
    module.Color = color  # type: ignore[attr-defined]
    module.importers = types.SimpleNamespace(importStep=import_step)  # type: ignore[attr-defined]
    return module


def install(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    """Stand the fake module in for ``cadquery`` and hand back its call log."""
    calls: list[Call] = []
    monkeypatch.setitem(sys.modules, "cadquery", make_module(calls))
    return calls


def call_names(calls: list[Call]) -> list[str]:
    return [name for name, _args, _kwargs in calls]
