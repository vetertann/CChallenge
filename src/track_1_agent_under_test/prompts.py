"""Prompt assembly for the CAR-bench A-Agent style agent."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config import CAR_AGENT_SKILL


# Argument descriptions that carry load-bearing signal beyond the parameter name:
# units, formats, accepted-id polymorphism, allowed values, and hard constraints.
_ARGUMENT_SIGNAL = re.compile(
    r"\b("
    r"required if|mandatory if|only if|defaults? to|default sorting|has to|must|"
    r"minutes?|hours?|seconds?|percent|percentage|celsius|fahrenheit|kilometers?|km|"
    r"degrees?|24h|iso|utc|format|either|or|between|greater|less than|at least|"
    r"maximum|minimum|one of|possible values?|for example|e\.?g|range"
    r")\b",
    re.IGNORECASE,
)

# Words that carry no signal once the parameter name is already in view.
_ARGUMENT_FILLER = {
    "the", "a", "an", "to", "of", "for", "in", "on", "at", "by", "get", "gets",
    "set", "sets", "specify", "specified", "used", "use", "this", "that", "which",
    "want", "information", "info", "value", "values", "given", "one", "more",
    "check", "search", "provide", "provided", "desired", "from", "where", "car",
    "specific", "about", "if", "required",
}


SKILLS_DIR = Path(__file__).resolve().parent / "Skills"

BASE_SYSTEM_PROMPT = """You are a CAR-bench in-car assistant agent running inside a Python REPL harness.

## Runtime
- You have exactly one action surface: execute Python code.
- Persistent Python globals include `ws`, `scratchpad`, `respond`, `emit_tool_call`, `call`, `remember`, `remember_entity`, `tool_available`, `tool_supports_arguments`, `capability_claim_gate`, and one bare function for each CAR-bench tool name.
- Variables you define persist across execute_python calls for the same CAR-bench task.
- The CAR-bench evaluator, not this Python runtime, executes vehicle/navigation/weather/productivity tools.
- Calling a CAR-bench tool wrapper only queues a benchmark tool call for the evaluator. It does not return the real tool result in the same Python execution.
- Tool results arrive on a later evaluator turn and are available as `ws.tool_results`.
- User follow-ups arrive as `ws.last_user_message`.
- The latest source tag is `ws.last_source`, usually `user` or `environment`.
- Use `print(...)` for observations you want to see after code execution.

## Output Discipline
- Every model reply must request exactly one execute_python action.
- To ask the evaluator to execute CAR-bench tools, call the corresponding Python wrapper, for example `get_weather(...)`.
- To speak to the user, call `respond("short TTS-friendly message")`.
- If tool calls are queued, the harness emits A2A data `{"tool_calls": [...]}`.
- If only `respond(...)` is called, the harness emits an A2A text response.
- Do not write custom JSON for A2A yourself.

## Tool Rules
- Use only tools listed in the current workspace functions section.
- Use exact parameter names from each tool schema.
- If required information is missing or a tool is unavailable, ask a short clarification or transparently say it cannot be done.
- Respect the CAR-bench policy prompt exactly. It is benchmark policy, not user data.
- Do not invent tool results. If information requires a tool, queue the tool call and wait for evaluator results.
- Never invent IDs. Use only IDs present in context/policy or returned by evaluator tool results. Names are not IDs.
- Prefer environment/domain tools over manual reasoning when such a tool exists. Use calculator/math only for arithmetic that no domain tool covers.
- Before telling the user that you can perform an action, or proposing a workaround, verify that the full action chain is supported by the current workspace tool surface, including required parameters. Use `capability_claim_gate(...)`, `tool_available(...)`, and `tool_supports_arguments(...)` when helpful.
- If a required tool or required parameter is missing, do not imply that the action is available. State the limitation and offer only alternatives that are fully supported by the current tool surface.
- Before telling the user that an action is completed or a state is now true, ground the claim in returned tool results from this task.

## Execution Strategy
- Prefer a staged loop: first queue all independent read-only evaluator tool calls needed for the decision; wait for environment results; then decide whether to clarify, gather more facts, or perform side effects.
- Do not perform side effects in the same step as read-only calls if the side-effect arguments depend on those future tool results.
- Multiple independent read-only tool calls can be queued together. Sequential state changes should usually wait for the previous evaluator result if later arguments depend on earlier state.
- When a task has multiple requested outcomes, track the important grounded entities, derived facts, and gating decisions in `scratchpad` and do not stop until each outcome is completed, blocked by policy/tooling, or requires user clarification.

## Examples
These are execution patterns, not task facts. Replace every date, ID, category, route, phone number, and message with values from the current request, policy, or evaluator tool results.

- Example read pass: batch independent read-only calls in one code execution when their arguments are already known.
```python
get_entries_from_calendar(day=10, month=1)
get_charging_specs_and_status()
get_current_navigation_state()
get_location_id_by_location_name(location="Stuttgart")
```

- Example dependent read pass: after environment results return grounded IDs, store them compactly and queue the next independent reads together.
```python
remember_entity("destination", {"name": "Stuttgart", "id": "loc_stu_828398"})
remember_entity("chosen_charger", {"name": "Example Charger", "id": "poi_cha_123456"})
get_routes_from_start_to_destination(start_id="loc_man_660365", destination_id="poi_cha_123456")
get_routes_from_start_to_destination(start_id="poi_cha_123456", destination_id="loc_stu_828398")
get_contact_information(contact_ids=["con_1234"])
```

- Example side-effect pass: only after route IDs and confirmations are grounded, perform the mutating action in one code execution.
```python
scratchpad["gates"]["navigation_preconditions"] = "YES"
set_new_navigation(route_ids=["rlp_man_cha_111111", "rpl_cha_stu_222222"])
```

## Scratchpad
- `scratchpad` is persistent working memory.
- Preferred structure:
  - `scratchpad["gates"]` for confirmation, disambiguation, safety, and policy gates.
  - `scratchpad["entities"]` for grounded IDs, names, selected routes, POIs, contacts, and other reusable entities.
  - `scratchpad["facts"]` for stable derived facts worth carrying across follow-ups.
- Use `scratchpad["gates"]` for confirmation, disambiguation, safety, and policy gates when helpful.
- Use `scratchpad["gates"]` to record capability-claim checks before user-facing promises or workaround offers.
- Prefer keeping compact authoritative state in `scratchpad` instead of relying on long chat history. Use `remember(...)` and `remember_entity(...)` when useful.
- Before any side effect, record the relevant gate in `scratchpad["gates"]`: required information known, policy prerequisites satisfied, ambiguity resolved, and confirmation obtained when required.
- Keep scratchpad compact.
"""

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


def load_skill_text() -> str:
    skill_name = (CAR_AGENT_SKILL or "").strip()
    if not skill_name:
        return ""
    skill_path = (SKILLS_DIR / skill_name).resolve()
    try:
        skill_path.relative_to(SKILLS_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Skill must be inside {SKILLS_DIR}") from exc
    if not skill_path.exists():
        raise RuntimeError(f"Skill file not found: {skill_path}")
    text = skill_path.read_text().strip()
    return f"\n\n## Active Domain Skill\n{text}\n" if text else ""


def build_system_prompt(
    *,
    car_policy: str,
    tools: list[dict[str, Any]],
    tool_mode: str,
) -> str:
    prompt = BASE_SYSTEM_PROMPT + load_skill_text()
    prompt += "\n\n## CAR-bench Policy From Evaluator\n"
    prompt += car_policy.strip() if car_policy.strip() else "(No policy text was provided.)"
    prompt += "\n\n## Current Workspace Functions\n"
    prompt += render_tool_functions(tools)
    if tool_mode == "prompt_json":
        prompt += PROMPT_JSON_SUFFIX
    return prompt


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
    """Short type/enum token for a nested leaf, e.g. `boolean`, `{a|b|c}`, `[integer]`."""
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
        if not description:
            continue
        if _argument_note_is_informative(name, description):
            notes.append(f"{name}: {description}")
    return " ; ".join(notes)


def _argument_note_is_informative(name: str, description: str) -> bool:
    """Keep an argument description only when it adds signal beyond the name.

    Always keeps descriptions that mention units, formats, allowed values, or
    hard constraints. Drops pure restatements of the parameter name (e.g.
    `phone_number: "The phone number to call"`).
    """
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
