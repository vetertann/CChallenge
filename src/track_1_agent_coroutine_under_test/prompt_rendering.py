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


_WRAPPED_TOOL_USAGE_NOTES = {
    "calculate_charging_soc_by_time": (
        "Python wrapper for a charging calculation. Use after a station and plug "
        "ID are grounded to compute SOC after a charging duration."
    ),
    "calculate_charging_time_by_soc": (
        "Python wrapper for a charging calculation. Use after a station and plug "
        "ID are grounded to compute minutes from start SOC to target SOC."
    ),
    "calculate_datetime": (
        "Python wrapper for evaluator date/time arithmetic. Use for policy-date "
        "calculations instead of doing fragile manual datetime math."
    ),
    "calculate_math": (
        "Python wrapper for evaluator arithmetic. Use for numeric calculations "
        "when no domain-specific helper/tool already gives the value."
    ),
    "call_phone_by_number": (
        "Python wrapper that places a phone call to an exact grounded number. For "
        "selected charging providers, prefer call_selected_charging_provider()."
    ),
    "convert_route_distance_and_time": (
        "Python wrapper for converting distance/time along a grounded route_id."
    ),
    "delete_current_navigation": (
        "Python wrapper that deletes active navigation. Call only for an explicit "
        "user request to stop/delete the whole route."
    ),
    "get_ambient_light_status_and_color": (
        "Python read wrapper for current ambient-light on/off state and color."
    ),
    "get_car_color": (
        "Python read wrapper for the vehicle color. Direct get_car_color() returns "
        "the color string, e.g. PURPLE."
    ),
    "get_charging_specs_and_status": (
        "Python read wrapper for battery specs, SOC, and remaining_range. Read it "
        "before answering range or charging-need questions."
    ),
    "get_climate_settings": (
        "Python read wrapper for fan, airflow, AC, circulation, and defrost state."
    ),
    "get_contact_id_by_contact_name": (
        "Python wrapper for contact lookup by first/last name. It delegates through "
        "the guarded lookup path and can narrow against recent calendar attendees."
    ),
    "get_contact_information": (
        "Python read wrapper for contact details by grounded contact IDs. Prefer "
        "get_contact_details(...) when roles or normalized fields matter."
    ),
    "get_current_navigation_state": (
        "Python read wrapper for raw navigation state. Prefer get_navigation_state(...) "
        "for normalized waypoints, routes, start, destination, and intermediates."
    ),
    "get_distance_by_soc": (
        "Python wrapper for official distance from one SOC to another. Prefer "
        "get_distance_by_soc_value(...) when you need normalized distance_km."
    ),
    "get_entries_from_calendar": (
        "Python read wrapper for today's calendar entries. Use policy date; for the "
        "next entry, prefer get_next_calendar_entry()."
    ),
    "get_exterior_lights_status": (
        "Python read wrapper for fog, low-beam, and high-beam state."
    ),
    "get_location_id_by_location_name": (
        "Python wrapper for city/location ID lookup. Pass the main city/location "
        "name, then use the returned ID; never invent loc_* IDs."
    ),
    "get_reading_lights_status": (
        "Python read wrapper for current reading-light states by position."
    ),
    "get_routes_from_start_to_destination": (
        "Python wrapper for route alternatives between grounded IDs. It delegates "
        "through guarded route normalization; prefer get_route_options(...) plus "
        "select_route(...) for most reasoning."
    ),
    "get_seat_heating_level": (
        "Python read wrapper for current front-seat heating levels."
    ),
    "get_seats_occupancy": (
        "Python read wrapper for seat occupancy. Use before occupancy-scoped seat "
        "heating or reading-light actions."
    ),
    "get_steering_wheel_heating_level": (
        "Python read wrapper for current steering-wheel heating level. Direct "
        "get_steering_wheel_heating_level() returns the integer level."
    ),
    "get_sunroof_and_sunshade_position": (
        "Python read wrapper for current sunroof and sunshade percentages."
    ),
    "get_temperature_inside_car": (
        "Python read wrapper for current driver/passenger cabin temperatures."
    ),
    "get_trunk_door_position": (
        "Python read wrapper for current trunk-door position. Direct "
        "get_trunk_door_position() returns the position string, e.g. closed."
    ),
    "get_user_preferences": (
        "Python read wrapper for learned preferences. Treat preferences as evidence "
        "only when they match the current decision point."
    ),
    "get_vehicle_window_positions": (
        "Python read wrapper for current window percentages. Unknown values are "
        "possible; policy helpers handle the safe unknown-window paths."
    ),
    "get_weather": (
        "Python wrapper for weather at a grounded location/POI and policy date/time. "
        "For navigation decisions, prefer arrival-time helpers."
    ),
    "navigation_add_one_waypoint": (
        "Python wrapper that adds one waypoint to active navigation. It delegates "
        "through guarded route/waypoint validation."
    ),
    "navigation_delete_destination": (
        "Python wrapper that removes the final destination from an active multi-stop "
        "route, making the previous waypoint the destination."
    ),
    "navigation_delete_waypoint": (
        "Python wrapper that removes one intermediate waypoint. It delegates through "
        "guarded replacement-route validation."
    ),
    "navigation_replace_final_destination": (
        "Python wrapper that replaces only the final destination. It delegates "
        "through guarded active-route validation."
    ),
    "navigation_replace_one_waypoint": (
        "Python wrapper that replaces one intermediate waypoint. It delegates "
        "through guarded two-segment route validation."
    ),
    "open_close_sunroof": (
        "Python wrapper for exact sunroof percentage. It delegates to "
        "open_sunroof_safe(...), which applies sunshade/weather/confirmation policy."
    ),
    "open_close_sunshade": (
        "Python wrapper for exact sunshade percentage. For matching the sunshade to "
        "the sunroof, prefer sync_sunshade_to_sunroof()."
    ),
    "open_close_trunk_door": (
        "Python wrapper for trunk open/close when the live task exposes that capability."
    ),
    "open_close_window": (
        "Python wrapper for exact window percentage. It delegates to "
        "open_close_window_safe(...), which applies AC/full-open policy."
    ),
    "planning_tool": (
        "Wrapped evaluator planning tool. Usually unnecessary in this REPL; use "
        "Python variables, scratchpad, and helper reports unless the evaluator task "
        "specifically benefits from an official plan update."
    ),
    "search_poi_along_the_route": (
        "Python wrapper for POI search along a grounded route_id. For charging "
        "stations, pass at_kilometer; prefer charging/search helpers when available."
    ),
    "search_poi_at_location": (
        "Python wrapper for POI search at a grounded location_id. Use select_poi(...) "
        "or open-at-arrival helpers to choose one grounded POI."
    ),
    "send_email": (
        "Python wrapper for email send. It handles confirmation gating; call it with "
        "the final grounded recipient email(s) and complete final content."
    ),
    "set_air_circulation": (
        "Python wrapper for exact air-circulation mode after the mode is resolved."
    ),
    "set_air_conditioning": (
        "Python wrapper for AC on/off. Turning AC on delegates to "
        "set_air_conditioning_on_safe(), which applies window and fan policy."
    ),
    "set_ambient_lights": (
        "Python wrapper for ambient lights. Use a grounded color when setting color; "
        "get_preferred_ambient_light_color() can resolve stored preference."
    ),
    "set_climate_temperature": (
        "Python wrapper for exact climate temperature. It delegates to "
        "set_climate_temperature_safe(...) for zone-scope and warning policy."
    ),
    "set_fan_airflow_direction": (
        "Python wrapper for exact fan airflow direction after the target direction is resolved."
    ),
    "set_fan_speed": (
        "Python wrapper for exact fan speed level. For relative changes, prefer "
        "increase_fan_speed(...) or decrease_fan_speed(...)."
    ),
    "set_fog_lights": (
        "Python wrapper for fog lights. Turning them on delegates to "
        "set_fog_lights_on_safe(), which applies weather and beam policy."
    ),
    "set_head_lights_high_beams": (
        "Python wrapper for high beams. Turning them on delegates to "
        "set_high_beams_on_safe(), which applies fog-light and confirmation policy."
    ),
    "set_head_lights_low_beams": (
        "Python wrapper for low beams after the on/off target is resolved."
    ),
    "set_new_navigation": (
        "Python wrapper for starting inactive navigation from ordered route IDs. It "
        "delegates through guarded route validation; do not use it for active-route edits."
    ),
    "set_reading_light": (
        "Python wrapper for one exact reading-light position. For occupancy-scoped "
        "requests, prefer the reading-light helpers."
    ),
    "set_seat_heating": (
        "Python wrapper for exact front-seat heating level. For occupancy or sync "
        "requests, prefer the seat-heating helpers."
    ),
    "set_steering_wheel_heating": (
        "Python wrapper for exact steering-wheel heating level."
    ),
    "set_window_defrost": (
        "Python wrapper for window defrost. Front/all ON delegates to "
        "set_window_defrost_safe(...), which applies fan/airflow/AC policy."
    ),
    "think": (
        "Wrapped evaluator thinking tool. Usually unnecessary in this REPL; prefer "
        "normal Python variables, comments, and scratchpad facts."
    ),
}


_COMPACT_WRAPPED_TOOL_SIGNATURES = {
    "get_car_color": "get_car_color() -> str",
    "get_steering_wheel_heating_level": "get_steering_wheel_heating_level() -> int",
    "get_trunk_door_position": "get_trunk_door_position() -> str",
    "planning_tool": "planning_tool(command, ...)",
}

_SUPPRESSED_ARGUMENT_NOTES = {
    "planning_tool",
}


def render_tool_functions(tools: list[dict[str, Any]]) -> str:
    if not tools:
        return "No low-level Python wrappers are documented. Use respond(...).\n"
    lines: list[str] = [
        "",
        "Low-level Python wrappers:",
        "These Python callable names match evaluator tools. A call first checks "
        "the current task's live tool/parameter surface, then either emits the "
        "official evaluator tool call or returns the safe missing-capability / "
        "confirmation response. Prefer the helpers above when one covers the "
        "request; use these directly for simple reads, simple grounded setters, "
        "or fallback workflows.",
    ]
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
        signature = _COMPACT_WRAPPED_TOOL_SIGNATURES.get(name) or f"{name}({', '.join(args)})"
        output_keys = ORIGINAL_TOOL_OUTPUTS.get(name)
        if output_keys:
            signature += " -> result{" + ", ".join(output_keys) + "}"
        lines.append(f"- `{signature}`")
        usage_note = _WRAPPED_TOOL_USAGE_NOTES.get(name) or description
        if usage_note:
            lines.append(f"  {usage_note}")
        notes = "" if name in _SUPPRESSED_ARGUMENT_NOTES else _render_argument_notes(properties)
        if notes:
            lines.append(f"  Key args: {notes}")
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
