#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from textwrap import fill

SUPPORTED_PYTHON = (3, 11)

try:
    from app.core.settings import Settings
    from app.models.schemas import DesignBrief
    from app.services.gateway.model_gateway import ModelGateway
    from app.services.planners.ollama_planner import OllamaPlanner
    from app.services.planners.rule_based_planner import RuleBasedPlanner
    from app.services.validation.design_validator import DesignValidator
except ImportError as exc:
    # Usually the stock system python: 3.9 cannot import the backend package at
    # all, and a bare interpreter is missing pydantic. Either way a traceback
    # tells the user nothing useful.
    running = sys.version_info[:2]
    if running < SUPPORTED_PYTHON:
        reason = (
            f"{sys.executable} is Python {running[0]}.{running[1]}, and this needs "
            f"{SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}"
        )
    else:
        reason = f"the backend dependencies are missing ({exc})"
    raise SystemExit(
        f"aic: {reason}.\n"
        "The supported environment is Python 3.11 from conda/mamba:\n"
        "\n"
        "    mamba env create -f environment.yml\n"
        "    conda activate ai-cad\n"
        "\n"
        "Full steps are in docs/setup.md."
    ) from exc


def bullet(text: str, indent: str = "") -> str:
    """Wrap one list item to the same 80 columns as the rules and the summary.

    Assumptions and warnings are full sentences and routinely run past 80
    characters, which broke the frame every time the planner had something
    honest to say.
    """
    return fill(
        str(text),
        width=80,
        initial_indent=f"{indent}- ",
        subsequent_indent=f"{indent}  ",
        # Warnings quote URLs and hyphenated words. Splitting either one to make
        # the column costs more than the overflow does.
        break_long_words=False,
        break_on_hyphens=False,
    )


def build_gateway() -> ModelGateway:
    settings = Settings()
    return ModelGateway(
        settings,
        RuleBasedPlanner(),
        DesignValidator(),
        ollama_planner=OllamaPlanner(settings),
    )


def render_plan(prompt: str, *, as_json: bool = False) -> int:
    gateway = build_gateway()
    brief = DesignBrief(prompt=prompt)
    plan, risk, record, warnings = gateway.plan(brief)

    if as_json:
        payload = {
            "prompt": prompt,
            "planning_risk_score": risk,
            "model_call": record.model_dump(mode="json"),
            "warnings": warnings,
            "plan": plan.model_dump(mode="json"),
        }
        print(json.dumps(payload, indent=2))
        return 0

    print()
    print("AI CAD")
    print("=" * 80)
    print(fill(prompt, width=80))
    print()
    print(f"Planner: {record.model} ({record.provider}, {record.path})")
    print(f"Planning risk: {risk:.2f}")
    if warnings:
        print()
        print("Warnings")
        print("-" * 80)
        for warning in warnings:
            print(bullet(warning))
    print()
    print("Summary")
    print("-" * 80)
    print(fill(plan.summary, width=80))
    if plan.assumptions:
        print()
        print("Assumptions")
        print("-" * 80)
        for assumption in plan.assumptions:
            print(bullet(assumption))
    print()
    print("Steps")
    print("-" * 80)
    for index, step in enumerate(plan.steps, start=1):
        print(f"{index}. {step.intent}")
        print(f"   id: {step.id}")
        print(f"   macro: {step.primitive_or_macro}")
        if step.workplane:
            print(f"   workplane: {step.workplane}")
        if step.depends_on:
            print(f"   depends_on: {', '.join(step.depends_on)}")
        if step.location_notes:
            print("   location:")
            for note in step.location_notes:
                print(bullet(note, indent="   "))
        if step.size_notes:
            print("   sizes:")
            for note in step.size_notes:
                print(bullet(note, indent="   "))
        if step.sketch_constraints:
            print("   sketch_constraints:")
            for note in step.sketch_constraints:
                print(bullet(note, indent="   "))
        if step.manual_instructions:
            print("   manual_recipe:")
            for note in step.manual_instructions:
                print(bullet(note, indent="   "))
        if step.parameters:
            print("   parameters:")
            for key, value in step.parameters.items():
                print(bullet(f"{key}: {value}", indent="   "))
        print(f"   postcondition: {step.postcondition}")
        print()
    return 0


def interactive_loop(as_json: bool) -> int:
    print("AI CAD terminal planner")
    print("Enter a prompt like: a teapot which can hold 1 gallon")
    print("Press Enter on an empty line to exit.")
    print()
    while True:
        try:
            prompt = input("object> ").strip()
        except EOFError:
            print()
            return 0
        if not prompt:
            return 0
        render_plan(prompt, as_json=as_json)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AI CAD planner in the terminal.")
    parser.add_argument("prompt", nargs="*", help="Object description to plan.")
    parser.add_argument(
        "--json", action="store_true", help="Print the raw planning payload as JSON."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.prompt:
        return render_plan(" ".join(args.prompt), as_json=args.json)
    return interactive_loop(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
