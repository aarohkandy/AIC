"""Order plan steps by the dependencies they declare.

Two places need this and they have to agree. The compiler writes
``build_model``, which calls the step functions in one fixed order, and the
executor runtime calls the same functions one at a time so it can cache each
step's artifact. If those two orders differ, the program the user is shown is
not the program that was built.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def order_by_dependencies(
    step_ids: Sequence[str], dependencies: Mapping[str, set[str]]
) -> tuple[list[str], list[str]]:
    """Return the steps in dependency order, plus the ones that cannot be ordered.

    ``dependencies`` maps every id in ``step_ids`` to the ids it must run after;
    resolving the names a plan wrote to the ids that exist is the caller's job,
    because the two callers do different things about a name that resolves to
    nothing. Steps left over at the end depend on each other, which is to say
    they form a cycle.

    Ties keep the caller's order, so a plan that already lists its steps in
    dependency order comes back exactly as it went in.
    """
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(step_ids)

    while remaining:
        ready = next((step_id for step_id in remaining if dependencies[step_id] <= placed), None)
        if ready is None:
            break
        remaining.remove(ready)
        ordered.append(ready)
        placed.add(ready)

    return ordered, remaining
