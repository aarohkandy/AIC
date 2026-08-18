from __future__ import annotations

from typing import Any

from app.services.executors.runtime import _execution_order
from app.services.plan_order import order_by_dependencies


def test_a_step_runs_after_the_step_it_depends_on() -> None:
    ordered, unorderable = order_by_dependencies(
        ["hollow", "body"], {"hollow": {"body"}, "body": set()}
    )

    assert ordered == ["body", "hollow"]
    assert unorderable == []


def test_independent_steps_keep_the_order_they_arrived_in() -> None:
    step_ids = ["one", "two", "three"]

    ordered, unorderable = order_by_dependencies(step_ids, {name: set() for name in step_ids})

    assert ordered == step_ids
    assert unorderable == []


def test_a_step_waits_for_every_dependency_not_just_the_first() -> None:
    ordered, _ = order_by_dependencies(
        ["lid", "shell", "standoffs"],
        {"lid": {"shell", "standoffs"}, "shell": set(), "standoffs": {"shell"}},
    )

    assert ordered == ["shell", "standoffs", "lid"]


def test_steps_that_depend_on_each_other_come_back_unordered() -> None:
    ordered, unorderable = order_by_dependencies(
        ["body", "hollow", "lip"],
        {"body": {"hollow"}, "hollow": {"body"}, "lip": set()},
    )

    assert ordered == ["lip"]
    assert unorderable == ["body", "hollow"]


def plan_step(step_id: str, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {"id": step_id, "parameters": {}, "depends_on": depends_on or []}


def test_the_runtime_executes_in_the_order_the_compiled_driver_calls() -> None:
    # The runtime calls the step functions itself so it can cache each one, so
    # it has to reach the same order build_model() was written in.
    steps = [plan_step("hollow", ["body"]), plan_step("body")]

    assert [step["id"] for step in _execution_order(steps)] == ["body", "hollow"]


def test_the_runtime_drops_no_step_it_cannot_order() -> None:
    # A cycle is a compile error and should never get this far. If one does,
    # running the steps anyway keeps the failure attributable to a step.
    steps = [plan_step("body", ["hollow"]), plan_step("hollow", ["body"])]

    assert [step["id"] for step in _execution_order(steps)] == ["body", "hollow"]


def test_the_runtime_ignores_a_dependency_on_a_step_that_is_not_in_the_plan() -> None:
    steps = [plan_step("hollow", ["body"])]

    assert [step["id"] for step in _execution_order(steps)] == ["hollow"]


def test_the_runtime_resolves_a_dependency_by_its_normalized_name() -> None:
    # "create_fit_body" written with the fi ligature. Python binds the NFKC
    # form, so the compiler resolves this to the same function and orders the
    # body first. Matching the raw string here would drop the edge, and the
    # executor would hollow a body that does not exist yet.
    steps = [plan_step("hollow", ["create_ﬁt_body"]), plan_step("create_fit_body")]

    assert [step["id"] for step in _execution_order(steps)] == ["create_fit_body", "hollow"]
