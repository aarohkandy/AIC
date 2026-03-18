#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from textwrap import fill

from app.core.settings import Settings
from app.models.schemas import DesignBrief
from app.services.gateway.model_gateway import ModelGateway
from app.services.planners.ollama_planner import OllamaPlanner
from app.services.planners.rule_based_planner import RuleBasedPlanner
from app.services.validation.design_validator import DesignValidator


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
            print(f"- {warning}")
    print()
    print("Summary")
    print("-" * 80)
    print(fill(plan.summary, width=80))
    if plan.assumptions:
        print()
        print("Assumptions")
        print("-" * 80)
        for assumption in plan.assumptions:
            print(f"- {assumption}")
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
                print(f"   - {note}")
        if step.size_notes:
            print("   sizes:")
            for note in step.size_notes:
                print(f"   - {note}")
        if step.sketch_constraints:
            print("   sketch_constraints:")
            for note in step.sketch_constraints:
                print(f"   - {note}")
        if step.manual_instructions:
            print("   manual_recipe:")
            for note in step.manual_instructions:
                print(f"   - {note}")
        if step.parameters:
            print("   parameters:")
            for key, value in step.parameters.items():
                print(f"   - {key}: {value}")
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
    parser.add_argument("--json", action="store_true", help="Print the raw planning payload as JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.prompt:
        return render_plan(" ".join(args.prompt), as_json=args.json)
    return interactive_loop(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
