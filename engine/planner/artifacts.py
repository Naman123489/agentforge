"""TypeScript declarations and task assignments, kept from ForgeAI ai1-planner (main.py)."""

from __future__ import annotations

import json
from typing import Any

from .schemas import ProjectPlan


def schema_to_typescript(schema: dict[str, Any]) -> str:
    """Convert common OpenAPI component schemas to TypeScript types."""
    definitions = schema.get("components", {}).get("schemas", {})
    output = []

    def convert(prop):
        if "$ref" in prop:
            return prop["$ref"].rsplit("/", 1)[-1]

        kind = prop.get("type")
        if kind == "string":
            if "enum" in prop:
                return " | ".join(
                    json.dumps(value) for value in prop["enum"]
                )
            return "string"
        if kind in ("integer", "number"):
            return "number"
        if kind == "boolean":
            return "boolean"
        if kind == "array":
            return f"Array<{convert(prop.get('items', {}))}>"
        if kind == "object":
            properties = prop.get("properties", {})
            if not properties:
                return "Record<string, unknown>"
            return "{ " + "; ".join(
                f"{name}: {convert(value)}"
                for name, value in properties.items()
            ) + " }"
        if "oneOf" in prop:
            return " | ".join(
                convert(item) for item in prop["oneOf"]
            )
        return "unknown"

    for name, definition in definitions.items():
        if not isinstance(definition, dict):
            continue

        properties = definition.get("properties", {})
        required = set(definition.get("required", []))
        lines = [f"export interface {name} {{"]

        for prop_name, prop in properties.items():
            optional = "" if prop_name in required else "?"
            lines.append(
                f"  {prop_name}{optional}: {convert(prop)};"
            )

        lines.append("}")
        output.append("\n".join(lines))

    return "\n\n".join(output) + "\n"


def create_task_assignments(plan: ProjectPlan) -> list[dict]:
    assignments = []

    for task in plan.tasks:
        assignments.append({
            "stage_id": task.task_id,
            "agent": task.owner,
            "instructions": task.description,
            "contract_ref": "contract/openapi.yaml",
            "files_allowed": task.files_allowed,
            "failed_attempts": 0,
            "reviewer_failures": [],
            "budget_context": {},
        })

    return assignments
