"""Compact rendering helpers for CAR-bench tool schemas."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# Stable result-key shapes per tool, compiled from observed evaluator tool
# results in our own run transcripts (benchmark-allowed data — not catalog
# internals or hidden mock data). Shown so the model writes the correct result
# access path (e.g. result["climate_temperature_driver"]) instead of guessing.
_TOOL_OUTPUTS_PATH = Path(__file__).resolve().parent / "original_tool_outputs.json"
try:
    ORIGINAL_TOOL_OUTPUTS: dict[str, list[str]] = json.loads(
        _TOOL_OUTPUTS_PATH.read_text(encoding="utf-8")
    )
except Exception:
    ORIGINAL_TOOL_OUTPUTS = {}


_ARGUMENT_SIGNAL = re.compile(
    r"\b("
    r"required if|mandatory if|only if|defaults? to|default sorting|has to|must|"
    r"minutes?|hours?|seconds?|percent|percentage|celsius|fahrenheit|kilometers?|km|"
    r"degrees?|24h|iso|utc|format|either|or|between|greater|less than|at least|"
    r"maximum|minimum|one of|possible values?|for example|e\.?g|range"
    r")\b",
    re.IGNORECASE,
)

_ARGUMENT_FILLER = {
    "the", "a", "an", "to", "of", "for", "in", "on", "at", "by", "get", "gets",
    "set", "sets", "specify", "specified", "used", "use", "this", "that", "which",
    "want", "information", "info", "value", "values", "given", "one", "more",
    "check", "search", "provide", "provided", "desired", "from", "where", "car",
    "specific", "about", "if", "required",
}


PROMPT_JSON_SUFFIX = """

## execute_python JSON Contract
Native tool calling is disabled for this run.
Every assistant reply must be exactly one JSON object:
{
  "thought": "one or two short sentences",
  "code": "valid Python source only"
}
Do not wrap the JSON in markdown fences. Do not add text before or after it.
"""


def render_tool_functions(tools: list[dict[str, Any]]) -> str:
    if not tools:
        return "No CAR-bench tools are currently available. Use respond(...).\n"
    lines: list[str] = []
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        description = (fn.get("description") or "").strip()
        parameters = fn.get("parameters") or {}
        raw_properties = parameters.get("properties") or {}
        properties = {
            str(prop_name): prop_schema
            for prop_name, prop_schema in raw_properties.items()
            if isinstance(prop_schema, dict)
        }
        required = [str(item) for item in parameters.get("required", []) or []]
        optional = [str(item) for item in properties if item not in required]
        args = [
            _render_argument(name, properties.get(name) or {}, optional=False)
            for name in required
        ] + [
            _render_argument(name, properties.get(name) or {}, optional=True)
            for name in optional
        ]
        signature = f"{name}({', '.join(args)})"
        output_keys = ORIGINAL_TOOL_OUTPUTS.get(name)
        if output_keys:
            signature += " -> result{" + ", ".join(output_keys) + "}"
        lines.append(f"- `{signature}`")
        if description:
            lines.append(f"  {description}")
        notes = _render_argument_notes(properties)
        if notes:
            lines.append(f"  args: {notes}")
    return "\n".join(lines).strip() + "\n"


def _render_argument(name: str, schema: dict[str, Any], *, optional: bool) -> str:
    suffix = "=..." if optional else ""
    if not isinstance(schema, dict):
        return f"{name}{suffix}"

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return f"{name}={{{'|'.join(map(str, enum_values))}}}{suffix}"

    items = schema.get("items")
    if isinstance(items, dict):
        item_enum = items.get("enum")
        if isinstance(item_enum, list) and item_enum:
            return f"{name}=[{{{'|'.join(map(str, item_enum))}}}]{suffix}"
        if items.get("type") == "object" and isinstance(items.get("properties"), dict):
            return f"{name}=[{_render_object_shape(items)}]{suffix}"
        item_type = items.get("type")
        if item_type:
            return f"{name}=[{item_type}]{suffix}"

    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        return f"{name}={_render_object_shape(schema)}{suffix}"

    arg_type = schema.get("type")
    min_value = schema.get("minimum")
    max_value = schema.get("maximum")
    if min_value is not None or max_value is not None:
        return f"{name}={arg_type or 'number'}[{min_value}..{max_value}]{suffix}"

    if "default" in schema:
        return f"{name}=default:{schema.get('default')!r}{suffix}"

    return f"{name}{suffix}"


_SCALAR_TYPE_TOKENS = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


def _leaf_type_token(schema: dict[str, Any], *, depth: int) -> str:
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return "{" + "|".join(map(str, enum_values)) + "}"
    if schema.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            item_enum = items.get("enum")
            if isinstance(item_enum, list) and item_enum:
                return "[{" + "|".join(map(str, item_enum)) + "}]"
            if items.get("type") == "object" and isinstance(items.get("properties"), dict):
                return f"[{_render_object_shape(items, depth=depth + 1)}]"
            item_type = items.get("type")
            if item_type:
                return f"[{_SCALAR_TYPE_TOKENS.get(item_type, item_type)}]"
        return "[]"
    arg_type = schema.get("type")
    return _SCALAR_TYPE_TOKENS.get(arg_type, arg_type or "")


def _render_object_shape(schema: dict[str, Any], *, depth: int = 0) -> str:
    raw_properties = schema.get("properties") or {}
    if not isinstance(raw_properties, dict) or not raw_properties:
        return "{object}"
    properties = {
        str(prop_name): prop_schema
        for prop_name, prop_schema in raw_properties.items()
        if isinstance(prop_schema, dict)
    }
    if not properties:
        return "{object}"
    rendered: list[str] = []
    for key in _ordered_property_names(schema, properties):
        child = properties.get(key) or {}
        child_properties = child.get("properties") if isinstance(child, dict) else None
        if isinstance(child_properties, dict) and child_properties:
            shape = _render_object_shape(child, depth=depth + 1) if depth < 1 else "{object}"
            rendered.append(f"{key}:{shape}")
        else:
            kind = _leaf_type_token(child, depth=depth)
            rendered.append(f"{key}:{kind}" if kind else str(key))
    return "{" + "|".join(rendered) + "}"


def _ordered_property_names(schema: dict[str, Any], properties: dict[str, Any]) -> list[str]:
    required = [str(item) for item in schema.get("required", []) or []]
    return required + [str(name) for name in properties if str(name) not in required]


def _render_argument_notes(properties: dict[str, Any]) -> str:
    notes: list[str] = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        description = " ".join(str(schema.get("description") or "").split())
        if description and _argument_note_is_informative(name, description):
            notes.append(f"{name}: {description}")
    return " ; ".join(notes)


def _argument_note_is_informative(name: str, description: str) -> bool:
    if _ARGUMENT_SIGNAL.search(description):
        return True
    name_tokens = {token for token in re.split(r"[^a-z0-9]+", name.lower()) if token}
    name_tokens |= {token[:-1] for token in list(name_tokens) if token.endswith("s")}
    residual = [
        token
        for token in re.split(r"[^a-z0-9]+", description.lower())
        if token
        and token not in name_tokens
        and token[:-1] not in name_tokens
        and token not in _ARGUMENT_FILLER
    ]
    return len(residual) >= 2


def initial_user_message(user_request: str) -> str:
    return (
        "Initial user request:\n"
        f"{user_request.strip() or 'none'}\n\n"
        "Decide the next action by executing Python. Use CAR-bench tool wrappers "
        "or respond(...)."
    )


def user_followup_message(user_text: str) -> str:
    return (
        "User follow-up:\n"
        f"{user_text.strip() or 'none'}\n\n"
        "Continue from the current scratchpad and transcript."
    )


def environment_message(tool_results: list[dict[str, Any]]) -> str:
    return (
        "Environment tool results from the evaluator:\n"
        f"{json.dumps(tool_results, indent=2, ensure_ascii=True)}\n\n"
        "These are now available as ws.tool_results. Continue by executing Python."
    )
