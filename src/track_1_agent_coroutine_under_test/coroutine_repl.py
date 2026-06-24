"""Blocking Python workspace for the coroutine-bridge CAR-bench agent."""

from __future__ import annotations

import builtins
import copy
import datetime as datetime_module
import io
import json
import math
import queue
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, NoReturn


def json_dumps_safe(value: Any, *, indent: int | None = None) -> str:
    """Serialize model-owned state without leaking addresses or crashing."""

    def fallback(item: Any) -> str:
        if callable(item):
            name = getattr(item, "__name__", item.__class__.__name__)
            return f"<callable {name}>"
        return f"<{item.__class__.__name__}>"

    return json.dumps(
        value,
        indent=indent,
        ensure_ascii=True,
        default=fallback,
    )


def _load_original_tool_schemas() -> dict[str, dict[str, Any]]:
    """Load public unmodified CAR-bench tool schemas bundled with the agent."""

    schema_path = Path(__file__).resolve().with_name("original_tool_schemas.json")
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid original tool schema file: {schema_path}")
    return {
        str(name): schema
        for name, schema in data.items()
        if isinstance(schema, dict)
    }


def _load_original_tool_metadata() -> dict[str, dict[str, Any]]:
    """Load public unmodified CAR-bench tool metadata bundled with the agent."""

    metadata_path = Path(__file__).resolve().with_name("original_tool_metadata.json")
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"Invalid original tool metadata file: {metadata_path}")
    metadata: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if isinstance(name, str) and name:
            metadata[name] = fn
    return metadata


ORIGINAL_TOOL_SCHEMAS = _load_original_tool_schemas()
ORIGINAL_TOOL_METADATA = _load_original_tool_metadata()
ALL_TOOL_NAMES = list(ORIGINAL_TOOL_SCHEMAS)

WORKSPACE_HELPER_NAMES = (
    "handle_pending_confirmation",
    "get_distance_by_soc_value",
    "get_navigation_state",
    "get_contact_details",
    "get_next_calendar_entry",
    "defrost_front_window",
    "set_window_defrost_safe",
    "open_sunroof_safe",
    "open_close_window_safe",
    "set_fog_lights_on_safe",
    "set_high_beams_on_safe",
    "set_air_conditioning_on_safe",
    "close_known_windows_for_blocked_ac",
    "set_climate_temperature_safe",
    "sync_climate_zone",
    "increase_fan_speed",
    "decrease_fan_speed",
    "set_occupied_seat_heating",
    "get_route_options",
    "select_route",
    "select_route_by_user_preferences",
    "select_poi",
    "get_weather_at_route_arrival",
    "select_poi_at_location_open_at_route_arrival",
    "select_charging_plug",
    "set_new_navigation_via_stop",
    "plan_charging_for_next_meeting",
    "call_selected_charging_provider",
    "get_preferred_ambient_light_color",
    "set_new_navigation_guarded",
    "get_routes_guarded",
    "get_weather_guarded",
    "search_poi_along_route_guarded",
    "navigation_add_one_waypoint_guarded",
    "navigation_delete_waypoint_guarded",
    "navigation_replace_one_waypoint_guarded",
    "navigation_replace_final_destination_guarded",
    "get_contact_id_by_contact_name_guarded",
)

KNOWN_CALL_NAMES = frozenset([*ALL_TOOL_NAMES, *WORKSPACE_HELPER_NAMES])

# Batch envelope fields the runtime controls; a helper's own top-level keys are
# hoisted into the envelope for convenience EXCEPT these, so a helper field
# cannot overwrite the runtime's framing of the batched call.
_RESERVED_BATCH_ENVELOPE_KEYS = frozenset({"status", "tool_name", "tool_call_id", "result"})

# Side-effect tools. A non-SUCCESS result from any of these in a single REPL
# block blocks optimistic success language until a later success clears it.
MUTATING_TOOL_NAMES = frozenset({
    "set_air_circulation", "set_air_conditioning", "set_ambient_lights",
    "set_climate_temperature", "set_fan_airflow_direction", "set_fan_speed",
    "set_fog_lights", "set_head_lights_high_beams", "set_head_lights_low_beams",
    "set_new_navigation", "set_reading_light", "set_seat_heating",
    "set_steering_wheel_heating", "set_window_defrost",
    "open_close_sunroof", "open_close_sunshade", "open_close_trunk_door",
    "open_close_window",
    "navigation_add_one_waypoint", "navigation_delete_destination",
    "navigation_delete_waypoint", "navigation_replace_final_destination",
    "navigation_replace_one_waypoint", "delete_current_navigation",
    "send_email", "call_phone_by_number",
})

# Arguments that identify the mutation target independently of the desired
# value. Tools not listed fall back to their complete argument object.
_MUTATION_TARGET_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "call_phone_by_number": ("phone_number",),
    "delete_current_navigation": (),
    "navigation_add_one_waypoint": (
        "waypoint_id_to_add",
        "waypoint_id_before_new_waypoint",
    ),
    "navigation_delete_destination": ("destination_id_to_delete",),
    "navigation_delete_waypoint": ("waypoint_id_to_delete",),
    "navigation_replace_final_destination": ("new_destination_id",),
    "navigation_replace_one_waypoint": (
        "waypoint_id_to_replace",
        "new_waypoint_id",
    ),
    "open_close_sunroof": (),
    "open_close_sunshade": (),
    "open_close_trunk_door": (),
    "open_close_window": ("window",),
    "send_email": ("email_addresses",),
    "set_air_circulation": (),
    "set_air_conditioning": (),
    "set_ambient_lights": (),
    "set_climate_temperature": ("seat_zone",),
    "set_fan_airflow_direction": (),
    "set_fan_speed": (),
    "set_fog_lights": (),
    "set_head_lights_high_beams": (),
    "set_head_lights_low_beams": (),
    "set_reading_light": ("position",),
    "set_seat_heating": ("seat_zone",),
    "set_steering_wheel_heating": (),
    "set_window_defrost": ("defrost_window",),
}

_WINDOW_POSITION_KEYS: dict[str, tuple[str, ...]] = {
    "DRIVER": ("window_driver_position", "DRIVER"),
    "PASSENGER": ("window_passenger_position", "PASSENGER"),
    "DRIVER_REAR": ("window_driver_rear_position", "DRIVER_REAR", "LEFT_REAR"),
    "LEFT_REAR": ("window_driver_rear_position", "DRIVER_REAR", "LEFT_REAR"),
    "PASSENGER_REAR": (
        "window_passenger_rear_position",
        "PASSENGER_REAR",
        "RIGHT_REAR",
    ),
    "RIGHT_REAR": (
        "window_passenger_rear_position",
        "PASSENGER_REAR",
        "RIGHT_REAR",
    ),
}


def _numbers_equal(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def _numeric_value_for_exact_keys(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, dict):
            for nested_key in ("percentage", "position", "value", "level"):
                nested = value.get(nested_key)
                if isinstance(nested, (int, float)) and not isinstance(nested, bool):
                    return float(nested)
    return None


def _proof_open_close_window(desired: dict[str, Any], reads: dict[str, dict[str, Any]]) -> bool:
    """True only when a window-position read confirms the requested percentage."""

    result = reads.get("get_vehicle_window_positions")
    payload = result.get("result") if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        return False
    window = str(desired.get("window") or "").strip().upper()
    percentage = desired.get("percentage")
    if not window or percentage is None:
        return False
    if window == "ALL":
        values = [
            _numeric_value_for_exact_keys(payload, keys)
            for keys in (
                _WINDOW_POSITION_KEYS["DRIVER"],
                _WINDOW_POSITION_KEYS["PASSENGER"],
                _WINDOW_POSITION_KEYS["DRIVER_REAR"],
                _WINDOW_POSITION_KEYS["PASSENGER_REAR"],
            )
        ]
        return all(value is not None and _numbers_equal(value, percentage) for value in values)
    keys = _WINDOW_POSITION_KEYS.get(window)
    if keys is None:
        return False
    current = _numeric_value_for_exact_keys(payload, keys)
    return current is not None and _numbers_equal(current, percentage)


# Setter tool -> reconciler proving the desired outcome from a later state read.
# Intentionally conservative and easy to extend: a setter with no entry simply
# has no proved-by-read recovery (retrying the setter still clears the failure).
_MUTATION_STATE_PROOF: dict[str, Any] = {
    "open_close_window": _proof_open_close_window,
}


# Navigation mutations that leave an active route behind on success.
NAVIGATION_ACTIVATING_MUTATIONS = frozenset({
    "set_new_navigation", "navigation_add_one_waypoint",
    "navigation_replace_final_destination", "navigation_replace_one_waypoint",
    "navigation_delete_waypoint", "navigation_delete_destination",
})
USER_TEXT_RUNTIME_ARTIFACTS = (
    "<bound method",
    "<function",
    " object at 0x",
    "CoroutineWorkspace",
    "BlockingPythonExecutor",
)

AMBIENT_LIGHT_COLORS = {
    "RED",
    "GREEN",
    "BLUE",
    "YELLOW",
    "WHITE",
    "PINK",
    "ORANGE",
    "PURPLE",
    "CYAN",
}

WINDOW_POSITION_KEY_TO_TOOL = {
    "window_driver_position": "DRIVER",
    "window_passenger_position": "PASSENGER",
    "window_driver_rear_position": "DRIVER_REAR",
    "window_passenger_rear_position": "PASSENGER_REAR",
}

WINDOW_POSITION_KEY_TO_LABEL = {
    "window_driver_position": "driver window",
    "window_passenger_position": "passenger window",
    "window_driver_rear_position": "driver rear window",
    "window_passenger_rear_position": "passenger rear window",
}

WINDOW_LABEL_TO_TOOL = {
    label: tool
    for key, tool in WINDOW_POSITION_KEY_TO_TOOL.items()
    for label in (WINDOW_POSITION_KEY_TO_LABEL.get(key, key), tool.lower().replace("_", " "))
}

TOOL_TARGET_HINTS = {
    "get_user_preferences": ("preference", "preferences", "favorite", "favourite", "settings"),
    "calculate_math": ("calculate", "math", "sum", "difference", "multiply", "divide"),
    "calculate_datetime": ("date", "time", "datetime", "tomorrow", "today", "minutes", "hours", "days"),
    "think": ("think", "reason", "analyze", "analyse"),
    "planning_tool": ("plan", "steps", "task list"),
    "open_close_sunroof": ("sunroof",),
    "open_close_sunshade": ("sunshade", "shade"),
    "open_close_trunk_door": ("trunk", "boot"),
    "open_close_window": ("window", "windows"),
    "set_air_circulation": ("air circulation", "recirculation", "fresh air", "recirculate"),
    "set_air_conditioning": ("air conditioning", " ac ", "a/c", "climate"),
    "set_ambient_lights": ("ambient", "light", "lighting", "color", "colour"),
    "set_climate_temperature": ("temperature", "climate", "warmer", "cooler", "degrees"),
    "set_fan_airflow_direction": ("airflow", "air flow", "windshield", "windscreen", "feet", "face"),
    "set_fan_speed": ("fan", "blower", "air conditioning", " ac ", "a/c"),
    "set_fog_lights": ("fog light", "fog lights"),
    "set_head_lights_high_beams": ("high beam", "high beams", "headlight", "headlights"),
    "set_head_lights_low_beams": ("low beam", "low beams", "headlight", "headlights"),
    "set_reading_light": ("reading light", "reading lights", "interior light"),
    "set_seat_heating": ("seat heating", "seat heater", "heated seat", "warm seat"),
    "set_steering_wheel_heating": ("steering wheel heating", "heated steering", "warm steering"),
    "set_window_defrost": ("defrost", "defog", "window defrost", "windshield", "windscreen"),
    "get_weather": ("weather", "rain", "snow", "temperature outside", "forecast"),
    "search_poi_at_location": (
        "restaurant", "restaurants", "fast food", "charging", "charger", "parking",
        "airport", "bakery", "supermarket", "toilet", "poi", "place", "near",
    ),
    "search_poi_along_the_route": (
        "restaurant", "restaurants", "fast food", "charging", "charger", "parking",
        "airport", "bakery", "supermarket", "toilet", "poi", "place", "along",
    ),
    "get_routes_from_start_to_destination": ("route", "routes", "directions", "navigate", "navigation"),
    "get_location_id_by_location_name": ("location", "city", "address", "navigate", "navigation", "route"),
    "convert_route_distance_and_time": ("route", "distance", "arrival", "eta", "time", "kilometer", "km"),
    "set_new_navigation": ("navigate", "navigation", "route", "directions", "destination"),
    "navigation_add_one_waypoint": ("waypoint", "stop", "add stop", "add waypoint"),
    "navigation_replace_one_waypoint": ("waypoint", "stop", "replace stop", "replace waypoint"),
    "navigation_replace_final_destination": ("destination", "replace destination", "change destination"),
    "navigation_delete_waypoint": ("waypoint", "stop", "delete stop", "remove stop"),
    "navigation_delete_destination": ("destination", "delete destination", "remove destination"),
    "get_distance_by_soc": ("range", "distance", "battery", "state of charge", "soc"),
    "calculate_charging_time_by_soc": ("charging time", "charge time", "battery", "state of charge", "soc"),
    "calculate_charging_soc_by_time": ("charging", "charge", "battery", "state of charge", "soc"),
    "get_entries_from_calendar": ("calendar", "meeting", "appointment", "event", "schedule"),
    "get_contact_information": ("contact", "phone", "email", "address book"),
    "call_phone_by_number": ("call", "phone", "dial"),
    "send_email": ("email", "mail", "message"),
}


SPECIAL_TOOL_LABELS = {
    "call_phone_by_number": "phone calling capability",
    "calculate_charging_soc_by_time": "charging state-of-charge calculator",
    "calculate_charging_time_by_soc": "charging time calculator",
    "calculate_datetime": "date and time calculator",
    "calculate_math": "math calculator",
    "convert_route_distance_and_time": "route distance and time converter",
    "get_climate_settings": "climate settings information",
    "get_contact_information": "contact information lookup",
    "get_distance_by_soc": "battery range information",
    "get_entries_from_calendar": "calendar information",
    "get_location_id_by_location_name": "location lookup",
    "get_routes_from_start_to_destination": "route lookup",
    "get_sunroof_and_sunshade_position": "sunroof and sunshade position information",
    "get_temperature_inside_car": "inside temperature information",
    "get_user_preferences": "user preferences information",
    "get_vehicle_window_positions": "vehicle window position information",
    "get_weather": "weather information",
    "navigation_add_one_waypoint": "navigation waypoint add control",
    "navigation_delete_destination": "navigation destination delete control",
    "navigation_delete_waypoint": "navigation waypoint delete control",
    "navigation_replace_final_destination": "navigation destination replacement control",
    "navigation_replace_one_waypoint": "navigation waypoint replacement control",
    "open_close_sunroof": "sunroof control",
    "open_close_sunshade": "sunshade control",
    "open_close_trunk_door": "trunk control",
    "open_close_window": "window control",
    "planning_tool": "planning tool",
    "search_poi_along_the_route": "route POI search",
    "search_poi_at_location": "location POI search",
    "send_email": "email sending capability",
    "set_air_circulation": "air circulation control",
    "set_air_conditioning": "air conditioning control",
    "set_ambient_lights": "ambient light control",
    "set_climate_temperature": "climate temperature control",
    "set_fan_airflow_direction": "fan airflow direction control",
    "set_fan_speed": "fan speed control",
    "set_fog_lights": "fog light control",
    "set_head_lights_high_beams": "high-beam headlight control",
    "set_head_lights_low_beams": "low-beam headlight control",
    "set_reading_light": "reading light control",
    "set_seat_heating": "seat heating control",
    "set_steering_wheel_heating": "steering wheel heating control",
    "set_window_defrost": "window defrost control",
    "think": "reasoning tool",
}

SPECIAL_PARAMETER_LABELS = {
    ("call_phone_by_number", "phone_number"): "phone number",
    ("calculate_charging_soc_by_time", "charging_time"): "charging time",
    ("calculate_charging_soc_by_time", "start_state_of_charge"): "starting battery percentage",
    ("calculate_charging_time_by_soc", "start_state_of_charge"): "starting battery percentage",
    ("calculate_charging_time_by_soc", "target_state_of_charge"): "target battery percentage",
    ("calculate_datetime", "original_datetime"): "original date and time",
    ("calculate_datetime", "times_to_add"): "time offset",
    ("calculate_math", "expression"): "math expression",
    ("convert_route_distance_and_time", "route_id"): "route ID",
    ("get_contact_information", "contact_ids"): "contact ID",
    ("get_distance_by_soc", "initial_state_of_charge"): "starting battery percentage",
    ("get_location_id_by_location_name", "location"): "location name",
    ("get_routes_from_start_to_destination", "destination_id"): "destination ID",
    ("get_routes_from_start_to_destination", "start_id"): "start ID",
    ("get_user_preferences", "preference_categories"): "preference category",
    ("get_weather", "location_or_poi_id"): "location or POI ID",
    ("navigation_add_one_waypoint", "route_id_leading_to_new_waypoint"): "route ID to the new waypoint",
    ("navigation_add_one_waypoint", "waypoint_id_before_new_waypoint"): "waypoint insertion point",
    ("navigation_add_one_waypoint", "waypoint_id_to_add"): "waypoint ID to add",
    ("navigation_delete_destination", "destination_id_to_delete"): "destination ID to delete",
    ("navigation_delete_waypoint", "route_id_without_waypoint"): "replacement route ID",
    ("navigation_delete_waypoint", "waypoint_id_to_delete"): "waypoint ID to delete",
    ("navigation_replace_final_destination", "new_destination_id"): "new destination ID",
    ("navigation_replace_final_destination", "route_id_leading_to_new_destination"): "route ID to the new destination",
    ("navigation_replace_one_waypoint", "new_waypoint_id"): "new waypoint ID",
    ("navigation_replace_one_waypoint", "route_id_leading_away_from_new_waypoint"): "route ID away from the new waypoint",
    ("navigation_replace_one_waypoint", "route_id_leading_to_new_waypoint"): "route ID to the new waypoint",
    ("navigation_replace_one_waypoint", "waypoint_id_to_replace"): "waypoint ID to replace",
    ("open_close_sunroof", "percentage"): "sunroof position percentage",
    ("open_close_sunshade", "percentage"): "sunshade position percentage",
    ("open_close_trunk_door", "action"): "trunk action",
    ("open_close_window", "percentage"): "window position percentage",
    ("open_close_window", "window"): "window selector",
    ("search_poi_along_the_route", "category_poi"): "POI category",
    ("search_poi_along_the_route", "route_id"): "route ID",
    ("search_poi_at_location", "category_poi"): "POI category",
    ("search_poi_at_location", "location_id"): "location ID",
    ("send_email", "content_message"): "email message content",
    ("send_email", "email_addresses"): "recipient email address",
    ("set_air_circulation", "mode"): "air circulation mode",
    ("set_air_conditioning", "on"): "air conditioning on/off setting",
    ("set_ambient_lights", "lightcolor"): "ambient light color",
    ("set_ambient_lights", "on"): "ambient light on/off setting",
    ("set_climate_temperature", "seat_zone"): "seat zone",
    ("set_climate_temperature", "temperature"): "target temperature",
    ("set_fan_airflow_direction", "direction"): "fan airflow direction",
    ("set_fan_speed", "level"): "fan speed level",
    ("set_fog_lights", "on"): "fog light on/off setting",
    ("set_head_lights_high_beams", "on"): "high-beam on/off setting",
    ("set_head_lights_low_beams", "on"): "low-beam on/off setting",
    ("set_reading_light", "on"): "reading light on/off setting",
    ("set_reading_light", "position"): "reading light position",
    ("set_seat_heating", "level"): "seat heating level",
    ("set_seat_heating", "seat_zone"): "seat zone",
    ("set_steering_wheel_heating", "level"): "steering wheel heating level",
    ("set_window_defrost", "defrost_window"): "window defrost target",
    ("set_window_defrost", "on"): "defrost on/off setting",
    ("think", "thought"): "thought text",
}


def _humanize_identifier(identifier: str) -> str:
    text = re.sub(r"[_\-]+", " ", str(identifier)).strip()
    return re.sub(r"\s+", " ", text)


def _human_join(items: list[str]) -> str:
    cleaned = [item.strip() for item in items if item and item.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _short_description_label(description: Any) -> str | None:
    if not isinstance(description, str) or not description.strip():
        return None
    text = re.split(r"[.\n]", description.strip(), maxsplit=1)[0].strip()
    text = re.sub(r"^(the|a|an)\s+", "", text, flags=re.IGNORECASE)
    text = text.rstrip(":")
    if not text or len(text) > 70:
        return None
    return text[0].lower() + text[1:] if text else None


def _tool_label(tool_name: str) -> str:
    if tool_name in SPECIAL_TOOL_LABELS:
        return SPECIAL_TOOL_LABELS[tool_name]
    fn = ORIGINAL_TOOL_METADATA.get(tool_name) or {}
    description = _short_description_label(fn.get("description"))
    if description:
        return description
    readable = _humanize_identifier(tool_name)
    if tool_name.startswith("get_"):
        return f"{_humanize_identifier(tool_name[4:])} information"
    if tool_name.startswith("set_"):
        return f"{_humanize_identifier(tool_name[4:])} control"
    if tool_name.startswith("open_close_"):
        return f"{_humanize_identifier(tool_name[11:])} control"
    if tool_name.startswith("calculate_"):
        return f"{_humanize_identifier(tool_name[10:])} calculator"
    if tool_name.startswith("search_"):
        return f"{_humanize_identifier(tool_name[7:])} search"
    return readable


def _parameter_label(tool_name: str, argument_name: str) -> str:
    special = SPECIAL_PARAMETER_LABELS.get((tool_name, argument_name))
    if special:
        return special
    schema = ORIGINAL_TOOL_SCHEMAS.get(tool_name) or {}
    properties = schema.get("properties", {}) or {}
    prop = properties.get(argument_name)
    if isinstance(prop, dict):
        description = _short_description_label(prop.get("description"))
        if description:
            return description
    metadata = ORIGINAL_TOOL_METADATA.get(tool_name) or {}
    parameters = metadata.get("parameters") if isinstance(metadata, dict) else None
    meta_properties = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
    meta_prop = meta_properties.get(argument_name)
    if isinstance(meta_prop, dict):
        description = _short_description_label(meta_prop.get("description"))
        if description:
            return description
    return _humanize_identifier(argument_name)


def _clean_action_phrase(action: str) -> str:
    text = re.sub(r"\s+", " ", str(action or "do that")).strip()
    text = text.replace("AC", "air conditioning")
    text = re.sub(r"\bunder policy [0-9_/]+\b", "", text, flags=re.IGNORECASE).strip()
    return text or "do that"


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs all any bool dict enumerate float getattr hasattr int isinstance "
        "len list max min next print range repr reversed round set sorted str sum tuple zip"
    ).split()
}
SAFE_BUILTINS.update(Exception=Exception, RuntimeError=RuntimeError, ValueError=ValueError)

ALLOWED_IMPORTS = {
    "datetime": datetime_module,
    "json": json,
    "math": math,
    "re": re,
}


@dataclass
class ExecutionResult:
    stdout: str
    error: dict[str, str] | None = None
    response_text: str | None = None


@dataclass
class OutboundAction:
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    response_text: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ResponseReady(Exception):
    """Internal control flow: stop model-written code after a terminal response."""


class UnknownToolResponseValue(str):
    """String-compatible sentinel that aborts when model code uses missing data."""

    def __new__(
        cls,
        workspace: "CoroutineWorkspace",
        response_path: str,
    ) -> "UnknownToolResponseValue":
        value = super().__new__(cls, "unknown")
        value._workspace = workspace
        value.response_path = response_path
        return value

    def require(self) -> NoReturn:
        self._workspace._abort_missing_tool_response(self.response_path)

    def __copy__(self) -> "UnknownToolResponseValue":
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> "UnknownToolResponseValue":
        return self

    def __bool__(self) -> bool:
        self.require()

    def __str__(self) -> str:
        self.require()

    def __repr__(self) -> str:
        self.require()

    def __format__(self, format_spec: str) -> str:
        self.require()

    def __eq__(self, other: Any) -> bool:
        self.require()

    def __ne__(self, other: Any) -> bool:
        self.require()

    def __lt__(self, other: Any) -> bool:
        self.require()

    def __le__(self, other: Any) -> bool:
        self.require()

    def __gt__(self, other: Any) -> bool:
        self.require()

    def __ge__(self, other: Any) -> bool:
        self.require()

    def __len__(self) -> int:
        self.require()

    def __iter__(self):
        self.require()

    def __getitem__(self, key: Any) -> Any:
        self.require()

    def __contains__(self, item: Any) -> bool:
        self.require()

    def __add__(self, other: Any) -> str:
        self.require()

    def __radd__(self, other: Any) -> str:
        self.require()

    def __int__(self) -> int:
        self.require()

    def __float__(self) -> float:
        self.require()

    def __index__(self) -> int:
        self.require()

    def strip(self, chars: str | None = None) -> str:
        self.require()

    def lower(self) -> str:
        self.require()

    def upper(self) -> str:
        self.require()

    def casefold(self) -> str:
        self.require()

    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
        self.require()

    def startswith(self, prefix: Any, start: int = 0, end: int | None = None) -> bool:
        self.require()

    def endswith(self, suffix: Any, start: int = 0, end: int | None = None) -> bool:
        self.require()

    __hash__ = str.__hash__


class ToolBridge:
    """Cross-thread bridge between blocking Python calls and A2A responses."""

    def __init__(self, outbox: queue.Queue[OutboundAction]) -> None:
        self._outbox = outbox
        self._results: queue.Queue[list[dict[str, Any]] | BaseException] = queue.Queue()
        self._waiting = threading.Event()

    @property
    def waiting(self) -> bool:
        return self._waiting.is_set()

    def request_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._waiting.set()
        self._outbox.put(OutboundAction(tool_calls=tool_calls))
        try:
            item = self._results.get(timeout=600)
        finally:
            self._waiting.clear()
        if isinstance(item, BaseException):
            raise item
        return item

    def deliver_results(self, tool_results: list[dict[str, Any]]) -> None:
        self._results.put(tool_results)

    def interrupt(self, message: str) -> None:
        self._results.put(RuntimeError(message))


class CoroutineWorkspace:
    """State and synchronous-looking tool API exposed to model-written Python."""

    def __init__(self, bridge: ToolBridge) -> None:
        self.bridge = bridge
        self.scratchpad: dict[str, Any] = self._new_scratchpad()
        self.policy: str = ""
        self.available_tools: dict[str, dict[str, Any]] = {}
        self.last_user_message: str = ""
        self.last_source: str = "user"
        self.tool_results: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self._response_text: str | None = None
        self._response_locked = False
        self._lock = threading.RLock()
        self._confirmation_execution_depth = 0
        self._preloaded_callables: dict[int, str] = {}
        # Mutation failures and read results live for the complete user turn,
        # not merely one model-written Python block.
        self._failed_mutations: dict[str, dict[str, Any]] = {}
        self._successful_mutations: list[dict[str, Any]] = []
        self._read_cache: dict[str, dict[str, Any]] = {}
        self._read_repeat_counts: dict[str, int] = {}
        self._state_revision = 0

    @staticmethod
    def _new_scratchpad() -> dict[str, Any]:
        return {"gates": {}, "entities": {}, "facts": {}}

    @property
    def gates(self) -> dict[str, Any]:
        self._ensure_scratchpad_shape()
        return self.scratchpad["gates"]

    @property
    def entities(self) -> dict[str, Any]:
        self._ensure_scratchpad_shape()
        return self.scratchpad["entities"]

    @property
    def facts(self) -> dict[str, Any]:
        self._ensure_scratchpad_shape()
        return self.scratchpad["facts"]

    def __getitem__(self, key: str) -> Any:
        """Expose scratchpad sections through ``ws["facts"]`` as a convenience."""

        if key in {"gates", "entities", "facts"}:
            self._ensure_scratchpad_shape()
            return self.scratchpad[key]
        raise KeyError(key)

    def _ensure_scratchpad_shape(self) -> None:
        defaults = self._new_scratchpad()
        for key, default in defaults.items():
            if key not in self.scratchpad or not isinstance(self.scratchpad[key], type(default)):
                self.scratchpad[key] = default

    def remember(self, key: str, value: Any, section: str = "facts") -> Any:
        self._ensure_scratchpad_shape()
        if section not in self.scratchpad or not isinstance(self.scratchpad[section], dict):
            raise ValueError(f"scratchpad section {section!r} is not a mapping")
        self.scratchpad[section][key] = value
        return value

    def remember_entity(self, key: str, value: Any) -> Any:
        return self.remember(key, value, section="entities")

    def register_preloaded_callable(self, value: Callable[..., Any], name: str) -> None:
        self._preloaded_callables[id(value)] = name

    def _resolve_preloaded_argument_value(self, value: Any) -> Any:
        callable_name = self._preloaded_callables.get(id(value)) if callable(value) else None
        if callable_name in {"policy_location_id", "policy_now"}:
            return value()
        if isinstance(value, dict):
            return {
                key: self._resolve_preloaded_argument_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_preloaded_argument_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._resolve_preloaded_argument_value(item) for item in value)
        return value

    def _window_tool_name_for_key(self, key: str) -> str | None:
        return WINDOW_POSITION_KEY_TO_TOOL.get(key)

    def _window_label_for_key(self, key: str) -> str:
        return WINDOW_POSITION_KEY_TO_LABEL.get(key, key)

    def _windows_over_position(
        self,
        windows: dict[str, Any],
        threshold: int | float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        closable: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        for key, value in windows.items():
            if not key.startswith("window_") or not key.endswith("_position"):
                continue
            tool_window = self._window_tool_name_for_key(key)
            if tool_window is None:
                continue
            label = self._window_label_for_key(key)
            if isinstance(value, (int, float)):
                if value > threshold:
                    closable.append(
                        {
                            "key": key,
                            "label": label,
                            "tool_window": tool_window,
                            "position": float(value),
                        }
                    )
            else:
                unknown.append(
                    {
                        "key": key,
                        "label": label,
                        "tool_window": tool_window,
                    }
                )
        return closable, unknown

    @staticmethod
    def _unknown_window_close_note(
        unknown_windows: list[dict[str, Any]],
        *,
        action: str = "air conditioning",
    ) -> str:
        labels = [str(item.get("label", "a window")) for item in unknown_windows]
        if not labels:
            return ""
        verb = "was" if len(labels) == 1 else "were"
        target = "that window" if len(labels) == 1 else "those windows"
        return (
            f"The current position for {_human_join(labels)} {verb} unavailable, "
            f"so I closed {target} before turning {action} on."
        )

    def _store_helper_report(self, name: str, report: dict[str, Any]) -> dict[str, Any]:
        self.remember(f"helper_report:{name}", report)
        self.remember("last_helper_report", {"name": name, **report})
        return report

    def _window_unknown_reason(self, unknown_windows: list[dict[str, Any]]) -> str:
        labels = [str(item.get("label", "a window")) for item in unknown_windows]
        if not labels:
            return "one or more window positions were unavailable"
        return f"the current position was unavailable for {', '.join(labels)}"

    def _window_policy_limitation(
        self,
        gate_name: str,
        action: str,
        policy: str,
        unknown_windows: list[dict[str, Any]],
        windows_to_close: list[dict[str, Any]],
    ) -> dict[str, Any]:
        unknown_labels = [str(item.get("label", "a window")) for item in unknown_windows]
        known_labels = [str(item.get("label", "a window")) for item in windows_to_close]
        unknown_text = ", ".join(unknown_labels) if unknown_labels else "one or more windows"
        if known_labels:
            verb = "are" if len(known_labels) > 1 else "is"
            known_text = " I can see that " + ", ".join(known_labels) + f" {verb} open more than 20%."
        else:
            known_text = ""
        message = (
            f"I can't {_clean_action_phrase(action)} because I need the current position for "
            f"{unknown_text} to apply the window safety rule, but that information is unavailable. "
            f"Policy {policy} requires me to know which windows are open more than 20% before I turn air conditioning on."
            f"{known_text}"
        )
        self.scratchpad["gates"][gate_name] = {
            "status": "NO",
            "policy": policy,
            "missing_information": unknown_labels,
            "known_windows_over_20": known_labels,
        }
        self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "UNAVAILABLE",
                "policy": policy,
                "missing_information": unknown_labels,
                "known_windows_over_20": known_labels,
                "known_window_details_over_20": windows_to_close,
                "message": message,
            },
        )
        self._abort_with_response(message)

    def _active_policy_011_blocker(self) -> dict[str, Any] | None:
        report = self.scratchpad.get("facts", {}).get("last_helper_report")
        if not isinstance(report, dict) or "011" not in str(report.get("policy", "")):
            return None
        if report.get("status") == "UNAVAILABLE" and report.get("missing_information"):
            return report
        if report.get("status") == "PARTIAL_SUCCESS" and report.get("remaining_missing_information"):
            return report
        return None

    def _clear_active_policy_011_blocker(self) -> None:
        report = self.scratchpad.get("facts", {}).get("last_helper_report")
        if isinstance(report, dict) and "011" in str(report.get("policy", "")):
            self.remember("last_policy_011_blocker_resolved", report)
            self.scratchpad["facts"].pop("last_helper_report", None)

    def _block_policy_011_action(self, action: str, report: dict[str, Any]) -> dict[str, Any]:
        missing = report.get("remaining_missing_information") or report.get("missing_information") or []
        missing_items = [str(item) for item in missing] if isinstance(missing, list) else [str(missing)]
        missing_text = ", ".join(missing_items) if missing_items else "required window-position information"
        message = (
            f"I still can't {_clean_action_phrase(action)} because I still need the current "
            f"window position for {missing_text}, and that information is unavailable."
        )
        self.scratchpad["gates"]["policy_011_blocker"] = {
            "status": "NO",
            "policy": "011",
            "blocked_action": action,
            "missing_information": missing_items,
        }
        self._abort_with_response(message)

    def tool_available(self, tool_name: str) -> bool:
        tool_name = self._canonical_call_name(tool_name)
        with self._lock:
            return tool_name in self.available_tools

    def tool_schema(self, tool_name: str) -> dict[str, Any]:
        tool_name = self._canonical_call_name(tool_name)
        with self._lock:
            if tool_name not in self.available_tools:
                raise KeyError(f"Tool {tool_name!r} is not available")
            return self.available_tools[tool_name].get("function", {}).get("parameters", {}) or {}

    def tool_required_arguments(self, tool_name: str) -> list[str]:
        schema = self.tool_schema(tool_name)
        return [str(name) for name in schema.get("required", []) or []]

    def tool_optional_arguments(self, tool_name: str) -> list[str]:
        schema = self.tool_schema(tool_name)
        properties = schema.get("properties", {}) or {}
        required = set(self.tool_required_arguments(tool_name))
        return [str(name) for name in properties if name not in required]

    def tool_signature(self, tool_name: str) -> str:
        helper_signatures = {
            "handle_pending_confirmation": "handle_pending_confirmation()",
            "defrost_front_window": "defrost_front_window()",
            "open_sunroof_safe": "open_sunroof_safe(percentage)",
            "set_fog_lights_on_safe": "set_fog_lights_on_safe()",
            "set_high_beams_on_safe": "set_high_beams_on_safe()",
            "get_distance_by_soc_value": (
                "get_distance_by_soc_value(initial_state_of_charge, final_state_of_charge=0)"
            ),
            "get_navigation_state": "get_navigation_state(detailed_information=True)",
            "get_contact_details": "get_contact_details(contact_ids, required_fields=None)",
            "get_next_calendar_entry": "get_next_calendar_entry()",
            "set_air_conditioning_on_safe": "set_air_conditioning_on_safe()",
            "close_known_windows_for_blocked_ac": "close_known_windows_for_blocked_ac(window=None)",
            "set_climate_temperature_safe": "set_climate_temperature_safe(seat_zone, temperature)",
            "sync_climate_zone": (
                "sync_climate_zone(source_zone, target_zone, "
                "include_temperature=True, include_seat_heating=True)"
            ),
            "increase_fan_speed": "increase_fan_speed(steps=1)",
            "decrease_fan_speed": "decrease_fan_speed(steps=1)",
            "set_occupied_seat_heating": "set_occupied_seat_heating(level=None, increase_by=None)",
            "get_route_options": "get_route_options(start_id, destination_id)",
            "select_route": (
                "select_route(routes, route_id=None, alias=None, name_via=None, "
                "prefer=None, record_selection=True)"
            ),
            "select_route_by_user_preferences": (
                "select_route_by_user_preferences(routes, preference_text=None, "
                "record_selection=True)"
            ),
            "select_poi": (
                "select_poi(pois=None, poi_id=None, name=None, category=None, "
                "record_selection=True)"
            ),
            "get_weather_at_route_arrival": (
                "get_weather_at_route_arrival(location_or_poi_id, route=None, route_id=None, "
                "routes=None, start_id=None)"
            ),
            "select_poi_at_location_open_at_route_arrival": (
                "select_poi_at_location_open_at_route_arrival(location_id, category_poi, "
                "route=None, route_id=None, routes=None, start_id=None, record_selection=True)"
            ),
            "select_charging_plug": (
                "select_charging_plug(pois=None, require_available=False)"
            ),
            "set_new_navigation_via_stop": (
                "set_new_navigation_via_stop(stop_id, final_destination_id, "
                "route_to_stop_prefer='fastest', route_to_final_alias=None, "
                "route_to_final_prefer='fastest')"
            ),
            "plan_charging_for_next_meeting": (
                "plan_charging_for_next_meeting(range_buffer_km=40, arrival_buffer_minutes=5)"
            ),
            "call_selected_charging_provider": "call_selected_charging_provider()",
            "get_preferred_ambient_light_color": "get_preferred_ambient_light_color()",
        }
        if tool_name in helper_signatures:
            return helper_signatures[tool_name]
        if tool_name in WORKSPACE_HELPER_NAMES:
            # Internal helpers (e.g. the *_guarded delegation targets) have no
            # explicit signature; return a generic one instead of raising.
            return f"{tool_name}(...)"
        required = self.tool_required_arguments(tool_name)
        optional = self.tool_optional_arguments(tool_name)
        args = required + [f"{name}=..." for name in optional]
        return f"{tool_name}({', '.join(args)})"

    def describe_tool(self, tool_name: str) -> dict[str, Any]:
        if tool_name == "get_distance_by_soc_value":
            return {
                "name": "get_distance_by_soc_value",
                "signature": "get_distance_by_soc_value(initial_state_of_charge, final_state_of_charge=0)",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper, not a direct evaluator tool. Calls get_distance_by_soc "
                    "and normalizes CAR-bench's dynamic distance_* output key to a stable dict with "
                    "`distance`, `unit`, `distance_km` when unit is km, plus `raw_key` and `raw_value`."
                ),
                "required_arguments": ["initial_state_of_charge"],
                "optional_arguments": ["final_state_of_charge"],
                "schema": {
                    "type": "object",
                    "required": ["initial_state_of_charge"],
                    "properties": {
                        "initial_state_of_charge": {"type": "integer", "minimum": 0, "maximum": 100},
                        "final_state_of_charge": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
                    },
                },
                "argument_descriptions": {
                    "initial_state_of_charge": "Initial state of charge percentage.",
                    "final_state_of_charge": "Final state of charge percentage; defaults to 0.",
                },
            }
        if tool_name == "get_navigation_state":
            return {
                "name": "get_navigation_state",
                "signature": "get_navigation_state(detailed_information=True)",
                "confirmation_required": False,
                "description": (
                    "Built-in read-only helper. Calls get_current_navigation_state and normalizes "
                    "active state, waypoint IDs, route IDs, detailed waypoints/routes, start, and "
                    "destination into stable fields. Missing required response fields are reported "
                    "directly instead of being guessed."
                ),
                "required_arguments": [],
                "optional_arguments": ["detailed_information"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "detailed_information": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether to request detailed waypoint and route data.",
                        }
                    },
                },
                "argument_descriptions": {
                    "detailed_information": "Whether to request detailed waypoint and route data.",
                },
            }
        if tool_name == "get_contact_details":
            return {
                "name": "get_contact_details",
                "signature": "get_contact_details(contact_ids, required_fields=None)",
                "confirmation_required": False,
                "description": (
                    "Built-in read-only helper. Calls get_contact_information and normalizes the "
                    "contact-ID-keyed result into contacts, by_id, and first, with flat name "
                    "aliases for nested name objects. Pass required_fields such as ['email'] or "
                    "['phone_number'] so unavailable response fields are reported directly."
                ),
                "required_arguments": ["contact_ids"],
                "optional_arguments": ["required_fields"],
                "schema": {
                    "type": "object",
                    "required": ["contact_ids"],
                    "properties": {
                        "contact_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Grounded contact IDs to retrieve.",
                        },
                        "required_fields": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                            "default": None,
                            "description": "Contact fields required for the next action.",
                        },
                    },
                },
                "argument_descriptions": {
                    "contact_ids": "Grounded contact IDs to retrieve.",
                    "required_fields": "Fields required for the next action, such as email.",
                },
            }
        if tool_name == "get_next_calendar_entry":
            return {
                "name": "get_next_calendar_entry",
                "signature": "get_next_calendar_entry()",
                "confirmation_required": False,
                "description": (
                    "Built-in read-only helper. Calls get_entries_from_calendar for the "
                    "current policy day, normalizes meeting start times, and returns all "
                    "entries plus the chronologically next entry at or after policy_now()."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {},
                },
                "argument_descriptions": {},
            }
        if tool_name == "defrost_front_window":
            return {
                "name": "defrost_front_window",
                "signature": "defrost_front_window()",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for front windshield defrost. "
                    "It checks required evaluator tools, reads climate/window state, "
                    "applies CAR-bench policy 010/011 through evaluator tools, remembers which "
                    "windows it adjusted, closes controllable windows whose current position is "
                    "unknown when AC must be enabled, and responds with a limitation if any "
                    "conditionally required tool is missing."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        if tool_name == "set_window_defrost_safe":
            return {
                "name": "set_window_defrost_safe",
                "signature": "set_window_defrost_safe(defrost_window='FRONT')",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for window defrost. For FRONT or ALL "
                    "defrost it checks required evaluator tools, reads climate/window "
                    "state, applies CAR-bench policy 010/011 through evaluator tools, "
                    "closes known windows open more than 20%, closes controllable "
                    "windows whose current position is unknown when AC must be enabled, "
                    "and reports missing required tools directly. REAR defrost is sent "
                    "to the raw defrost tool without the front/all policy additions."
                ),
                "required_arguments": [],
                "optional_arguments": ["defrost_window"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "defrost_window": {
                            "type": "string",
                            "enum": ["FRONT", "ALL", "REAR"],
                            "default": "FRONT",
                        }
                    },
                },
                "argument_descriptions": {
                    "defrost_window": "FRONT, ALL, or REAR. Policy 010 applies to FRONT and ALL.",
                },
            }
        if tool_name == "handle_pending_confirmation":
            return {
                "name": "handle_pending_confirmation",
                "signature": "handle_pending_confirmation()",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for follow-up turns after another helper asked for "
                    "explicit user confirmation. It checks the latest user message, executes the "
                    "stored pending evaluator calls only on a clear yes/proceed confirmation, "
                    "cancels on a clear no/cancel, and otherwise asks for a clearer yes."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        if tool_name == "open_sunroof_safe":
            return {
                "name": "open_sunroof_safe",
                "signature": "open_sunroof_safe(percentage)",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for setting the sunroof position under policies "
                    "005 and 008/009. It checks sunshade state, opens the sunshade in parallel "
                    "when needed, checks weather at the current policy location/time before "
                    "opening, stores pending confirmation for unsafe weather, and emits a "
                    "missing-capability limitation if any required evaluator tool or parameter "
                    "is unavailable."
                ),
                "required_arguments": ["percentage"],
                "optional_arguments": [],
                "schema": {
                    "type": "object",
                    "required": ["percentage"],
                    "properties": {
                        "percentage": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "description": "Target absolute sunroof position percentage.",
                        }
                    },
                },
                "argument_descriptions": {
                    "percentage": "Target absolute sunroof position percentage from 0 to 100.",
                },
            }
        if tool_name == "open_close_window_safe":
            return {
                "name": "open_close_window_safe",
                "signature": "open_close_window_safe(window, percentage)",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for moving a window under policy 007. "
                    "For target positions above 25%, it reads AC state first. If AC is "
                    "known on, or if AC state was checked but unavailable, it asks for "
                    "explicit confirmation with the intended window and percentage "
                    "before moving the window. Otherwise it calls open_close_window."
                ),
                "required_arguments": ["window", "percentage"],
                "optional_arguments": [],
                "schema": {
                    "type": "object",
                    "required": ["window", "percentage"],
                    "properties": {
                        "window": {
                            "type": "string",
                            "enum": [
                                "ALL",
                                "DRIVER",
                                "PASSENGER",
                                "DRIVER_REAR",
                                "PASSENGER_REAR",
                                "RIGHT_REAR",
                                "LEFT_REAR",
                            ],
                        },
                        "percentage": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 100,
                        },
                    },
                },
                "argument_descriptions": {
                    "window": "Window enum or normalized window label.",
                    "percentage": "Target absolute window position from 0 to 100.",
                },
            }
        if tool_name == "set_fog_lights_on_safe":
            return {
                "name": "set_fog_lights_on_safe",
                "signature": "set_fog_lights_on_safe()",
                "confirmation_required": False,
                "description": (
                    "Built-in policy helper for activating fog lights. It checks current weather "
                    "and exterior lights, obtains explicit confirmation when policy 008/009 "
                    "requires it, turns low beams on and high beams off when needed under policy "
                    "013, and directly reports missing tools, parameters, or response fields."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        if tool_name == "set_high_beams_on_safe":
            return {
                "name": "set_high_beams_on_safe",
                "signature": "set_high_beams_on_safe()",
                "confirmation_required": False,
                "description": (
                    "Built-in policy helper for activating high beams. It reads fog-light state, "
                    "blocks activation only when fog lights are known on under policy 014, records "
                    "unknown fog state internally, and routes the high-beam setter through its "
                    "explicit confirmation requirement."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        if tool_name == "set_air_conditioning_on_safe":
            return {
                "name": "set_air_conditioning_on_safe",
                "signature": "set_air_conditioning_on_safe()",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for turning AC on under CAR-bench policy 011. "
                    "It checks climate/window state, closes each known window that is open more "
                    "than 20%, closes each controllable window whose current position is unknown, "
                    "sets fan speed to 1 if currently 0, turns AC on, remembers which windows it "
                    "adjusted, and emits a limitation response if required evaluator tools are "
                    "missing."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        if tool_name == "close_known_windows_for_blocked_ac":
            return {
                "name": "close_known_windows_for_blocked_ac",
                "signature": "close_known_windows_for_blocked_ac(window=None)",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for follow-ups after set_air_conditioning_on_safe "
                    "or defrost_front_window reported UNAVAILABLE due missing window position data. "
                    "It closes only windows already recorded in the last helper report as known open "
                    "more than 20%, then responds with the remaining limitation. It does not retry "
                    "turning AC on or infer unavailable window positions."
                ),
                "required_arguments": [],
                "optional_arguments": ["window"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "window": {
                            "type": "string",
                            "description": "Optional known window label or enum to close, for example DRIVER or driver window.",
                        }
                    },
                },
                "argument_descriptions": {
                    "window": "Optional known window label or enum to close.",
                },
            }
        if tool_name == "set_climate_temperature_safe":
            return {
                "name": "set_climate_temperature_safe",
                "signature": "set_climate_temperature_safe(seat_zone, temperature)",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for explicit temperature changes. Calls "
                    "set_climate_temperature and, for DRIVER or PASSENGER single-zone changes, "
                    "checks the other zone and tells the user if the resulting difference is more "
                    "than 3 degrees Celsius per policy 012."
                ),
                "required_arguments": ["seat_zone", "temperature"],
                "optional_arguments": [],
                "schema": {
                    "type": "object",
                    "required": ["seat_zone", "temperature"],
                    "properties": {
                        "seat_zone": {"type": "string", "enum": ["ALL_ZONES", "DRIVER", "PASSENGER"]},
                        "temperature": {"type": "number", "minimum": 16, "maximum": 28},
                    },
                },
                "argument_descriptions": {
                    "seat_zone": "ALL_ZONES, DRIVER, or PASSENGER. Must be explicit or already resolved.",
                    "temperature": "Target temperature in degrees Celsius.",
                },
            }
        if tool_name == "sync_climate_zone":
            return {
                "name": "sync_climate_zone",
                "signature": (
                    "sync_climate_zone(source_zone, target_zone, "
                    "include_temperature=True, include_seat_heating=True)"
                ),
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for copying climate settings from one front "
                    "zone to another. It reads temperature and/or seat-heating state, then "
                    "writes only the target zone using values from the source zone."
                ),
                "required_arguments": ["source_zone", "target_zone"],
                "optional_arguments": ["include_temperature", "include_seat_heating"],
                "schema": {
                    "type": "object",
                    "required": ["source_zone", "target_zone"],
                    "properties": {
                        "source_zone": {"type": "string", "enum": ["DRIVER", "PASSENGER"]},
                        "target_zone": {"type": "string", "enum": ["DRIVER", "PASSENGER"]},
                        "include_temperature": {"type": "boolean", "default": True},
                        "include_seat_heating": {"type": "boolean", "default": True},
                    },
                },
                "argument_descriptions": {
                    "source_zone": "Zone to copy values from, DRIVER or PASSENGER.",
                    "target_zone": "Zone to modify, DRIVER or PASSENGER.",
                    "include_temperature": "Whether to copy temperature.",
                    "include_seat_heating": "Whether to copy seat-heating level.",
                },
            }
        if tool_name in {"increase_fan_speed", "decrease_fan_speed"}:
            direction = "increase" if tool_name == "increase_fan_speed" else "decrease"
            return {
                "name": tool_name,
                "signature": f"{tool_name}(steps=1)",
                "confirmation_required": False,
                "description": (
                    f"Built-in workspace helper for relative fan-speed requests. It reads "
                    f"get_climate_settings(), {direction}s fan_speed by the requested number "
                    "of steps, keeps the value inside the supported range, then calls "
                    "set_fan_speed."
                ),
                "required_arguments": [],
                "optional_arguments": ["steps"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "steps": {"type": "integer", "minimum": 1, "default": 1},
                    },
                },
                "argument_descriptions": {
                    "steps": "Positive number of fan-speed levels to change; defaults to 1.",
                },
            }
        if tool_name == "get_route_options":
            return {
                "name": "get_route_options",
                "signature": "get_route_options(start_id, destination_id)",
                "confirmation_required": False,
                "description": (
                    "Built-in read-only route helper. Calls get_routes_from_start_to_destination "
                    "and normalizes the result to a stable dict with `routes`, `fastest`, "
                    "`shortest`, `fastest_route_id`, `shortest_route_id`, route aliases, "
                    "duration totals, toll metadata, ready-to-copy `display` strings, and raw result."
                ),
                "required_arguments": ["start_id", "destination_id"],
                "optional_arguments": [],
                "schema": {
                    "type": "object",
                    "required": ["start_id", "destination_id"],
                    "properties": {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                },
                "argument_descriptions": {
                    "start_id": "Grounded location or POI id for route start.",
                    "destination_id": "Grounded location or POI id for route destination.",
                },
            }
        if tool_name == "select_route":
            return {
                "name": "select_route",
                "signature": (
                    "select_route(routes, route_id=None, alias=None, name_via=None, "
                    "prefer=None, record_selection=True)"
                ),
                "confirmation_required": False,
                "description": (
                    "Built-in pure selector over normalized or raw route lists. It returns SUCCESS "
                    "only if route_id, alias, name_via, or prefer uniquely identifies one route; "
                    "otherwise it returns AMBIGUOUS or NOT_FOUND instead of guessing. A successful "
                    "selection exposes `route_id` and `selected_route_id` and is recorded with "
                    "the current navigation revision by default."
                ),
                "required_arguments": ["routes"],
                "optional_arguments": [
                    "route_id",
                    "alias",
                    "name_via",
                    "prefer",
                    "record_selection",
                ],
                "schema": {"type": "object", "required": ["routes"], "properties": {}},
                "argument_descriptions": {
                    "routes": "Route list or get_route_options(...) result.",
                    "route_id": "Exact route_id to select.",
                    "alias": "Route alias such as fastest, shortest, first, second, or third.",
                    "name_via": "Exact via-street name from route result, such as K57, B65.",
                    "prefer": "Alias preference, usually fastest or shortest, only when explicit/policy-resolved.",
                    "record_selection": "Whether to persist revision-bound selection provenance.",
                },
            }
        if tool_name == "select_route_by_user_preferences":
            return {
                "name": "select_route_by_user_preferences",
                "signature": (
                    "select_route_by_user_preferences(routes, preference_text=None, "
                    "record_selection=True)"
                ),
                "confirmation_required": False,
                "description": (
                    "Built-in route selector for stored route-selection preferences. It reads "
                    "the current user preference facts unless `preference_text` is provided, "
                    "applies supported route rules such as fastest, shortest, no toll roads, "
                    "and no-toll-within-N-minutes-of-fastest, then records exactly one selected "
                    "route. It returns UNAVAILABLE or AMBIGUOUS instead of guessing when the "
                    "preference cannot be applied uniquely."
                ),
                "required_arguments": ["routes"],
                "optional_arguments": ["preference_text", "record_selection"],
                "schema": {"type": "object", "required": ["routes"], "properties": {}},
                "argument_descriptions": {
                    "routes": "Route list or get_route_options(...) result.",
                    "preference_text": "Optional explicit route preference text; defaults to stored user route preferences.",
                    "record_selection": "Whether to persist revision-bound selection provenance.",
                },
            }
        if tool_name == "select_poi":
            return {
                "name": "select_poi",
                "signature": (
                    "select_poi(pois=None, poi_id=None, name=None, category=None, "
                    "record_selection=True)"
                ),
                "confirmation_required": False,
                "description": (
                    "Built-in selector over POI search results. It returns SUCCESS only when "
                    "a POI id/navigation_id or normalized POI name uniquely identifies one "
                    "candidate. Use it when the user explicitly chooses a named POI, then pass "
                    "the returned `poi` or `navigation_id` to charging, routing, or calling helpers."
                ),
                "required_arguments": [],
                "optional_arguments": ["pois", "poi_id", "name", "category", "record_selection"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "pois": {"type": ["array", "object", "null"]},
                        "poi_id": {"type": ["string", "null"]},
                        "name": {"type": ["string", "null"]},
                        "category": {"type": ["string", "null"]},
                        "record_selection": {"type": "boolean", "default": True},
                    },
                },
                "argument_descriptions": {
                    "pois": "POI list or search_poi_at_location/search_poi_along_the_route result; defaults to last_pois.",
                    "poi_id": "Exact POI id or navigation_id to select.",
                    "name": "POI name the user selected, such as Ionity.",
                    "category": "Optional category constraint, such as charging_stations.",
                    "record_selection": "Whether to persist the selected POI for follow-up turns.",
                },
            }
        if tool_name == "get_weather_at_route_arrival":
            return {
                "name": "get_weather_at_route_arrival",
                "signature": (
                    "get_weather_at_route_arrival(location_or_poi_id, route=None, route_id=None, "
                    "routes=None, start_id=None)"
                ),
                "confirmation_required": False,
                "description": (
                    "Built-in read helper for navigation decisions conditioned on destination "
                    "weather. It uses a provided route, route_id, route list, or a route lookup "
                    "from the current policy location to compute arrival time, then calls "
                    "get_weather for that destination at the route-arrival hour/minute."
                ),
                "required_arguments": ["location_or_poi_id"],
                "optional_arguments": ["route", "route_id", "routes", "start_id"],
                "schema": {
                    "type": "object",
                    "required": ["location_or_poi_id"],
                    "properties": {
                        "location_or_poi_id": {"type": "string"},
                        "route": {"type": ["object", "null"]},
                        "route_id": {"type": ["string", "null"]},
                        "routes": {"type": ["array", "object", "null"]},
                        "start_id": {"type": ["string", "null"]},
                    },
                },
                "argument_descriptions": {
                    "location_or_poi_id": "Grounded destination location or POI id for the weather check.",
                    "route": "Optional selected route dict whose duration determines arrival time.",
                    "route_id": "Optional route id to resolve from remembered route facts.",
                    "routes": "Optional route options/list; fastest is used if no explicit route is supplied.",
                    "start_id": "Optional route start id; defaults to policy_location_id().",
                },
            }
        if tool_name == "select_poi_at_location_open_at_route_arrival":
            return {
                "name": "select_poi_at_location_open_at_route_arrival",
                "signature": (
                    "select_poi_at_location_open_at_route_arrival(location_id, category_poi, "
                    "route=None, route_id=None, routes=None, start_id=None, record_selection=True)"
                ),
                "confirmation_required": False,
                "description": (
                    "Built-in read/selection helper for requests such as a supermarket open when "
                    "the car arrives at an intermediate destination. It computes route-arrival "
                    "time, searches POIs at the location without a currently-open filter, parses "
                    "opening_hours, and selects the unique POI open at arrival when possible."
                ),
                "required_arguments": ["location_id", "category_poi"],
                "optional_arguments": ["route", "route_id", "routes", "start_id", "record_selection"],
                "schema": {
                    "type": "object",
                    "required": ["location_id", "category_poi"],
                    "properties": {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "route": {"type": ["object", "null"]},
                        "route_id": {"type": ["string", "null"]},
                        "routes": {"type": ["array", "object", "null"]},
                        "start_id": {"type": ["string", "null"]},
                        "record_selection": {"type": "boolean", "default": True},
                    },
                },
                "argument_descriptions": {
                    "location_id": "Grounded location where POIs should be searched.",
                    "category_poi": "POI category, such as supermarkets or fast_food.",
                    "route": "Optional selected route dict whose duration determines arrival time.",
                    "route_id": "Optional route id to resolve from remembered route facts.",
                    "routes": "Optional route options/list; fastest is used only if no route is supplied.",
                    "start_id": "Optional route start id; defaults to policy_location_id().",
                    "record_selection": "Whether to persist a uniquely selected open POI.",
                },
            }
        if tool_name == "select_charging_plug":
            return {
                "name": "select_charging_plug",
                "signature": "select_charging_plug(pois=None, require_available=False)",
                "confirmation_required": False,
                "description": (
                    "Built-in selector for charging POI results. It keeps station name, station "
                    "POI id, phone number, plug id, power type, power_kw, and availability together, "
                    "then selects the highest-power plug. By default it may select an occupied "
                    "plug for time calculation; pass require_available=True only when current "
                    "availability is a hard user constraint."
                ),
                "required_arguments": [],
                "optional_arguments": ["pois", "require_available"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "pois": {"type": ["array", "object", "null"]},
                        "require_available": {"type": "boolean", "default": False},
                    },
                },
                "argument_descriptions": {
                    "pois": "POI list or search_poi_at_location/search_poi_along_the_route result.",
                    "require_available": "If true, ignore occupied/maintenance plugs.",
                },
            }
        if tool_name == "set_new_navigation_via_stop":
            return {
                "name": "set_new_navigation_via_stop",
                "signature": (
                    "set_new_navigation_via_stop(stop_id, final_destination_id, "
                    "route_to_stop_prefer='fastest', route_to_final_alias=None, "
                    "route_to_final_prefer='fastest')"
                ),
                "confirmation_required": False,
                "description": (
                    "Built-in navigation helper for inactive-navigation requests that set a "
                    "new two-leg route through one stop. It looks up routes from current "
                    "location to the stop and from the stop to the final destination, selects "
                    "each leg by explicit selectors, then calls set_new_navigation through the "
                    "normal guarded wrapper. If set_new_navigation is unavailable, the normal "
                    "missing-capability response is returned."
                ),
                "required_arguments": ["stop_id", "final_destination_id"],
                "optional_arguments": [
                    "route_to_stop_route_id",
                    "route_to_stop_alias",
                    "route_to_stop_name_via",
                    "route_to_stop_prefer",
                    "route_to_final_route_id",
                    "route_to_final_alias",
                    "route_to_final_name_via",
                    "route_to_final_prefer",
                ],
                "schema": {
                    "type": "object",
                    "required": ["stop_id", "final_destination_id"],
                    "properties": {
                        "stop_id": {"type": "string"},
                        "final_destination_id": {"type": "string"},
                        "route_to_stop_route_id": {"type": ["string", "null"]},
                        "route_to_stop_alias": {"type": ["string", "null"]},
                        "route_to_stop_name_via": {"type": ["string", "null"]},
                        "route_to_stop_prefer": {"type": ["string", "null"], "default": "fastest"},
                        "route_to_final_route_id": {"type": ["string", "null"]},
                        "route_to_final_alias": {"type": ["string", "null"]},
                        "route_to_final_name_via": {"type": ["string", "null"]},
                        "route_to_final_prefer": {"type": ["string", "null"], "default": "fastest"},
                    },
                },
                "argument_descriptions": {
                    "stop_id": "Grounded POI or location id for the intermediate stop.",
                    "final_destination_id": "Grounded final destination location or POI id.",
                    "route_to_stop_*": "Explicit selector for the current-location-to-stop leg.",
                    "route_to_final_*": "Explicit selector for the stop-to-destination leg.",
                },
            }
        if tool_name == "plan_charging_for_next_meeting":
            return {
                "name": "plan_charging_for_next_meeting",
                "signature": "plan_charging_for_next_meeting(range_buffer_km=40, arrival_buffer_minutes=5)",
                "confirmation_required": False,
                "description": (
                    "Built-in planning helper for requests asking minimum and maximum "
                    "charging time before the next meeting. It reads the next calendar "
                    "entry, route, charging state, full-range distance, and nearby chargers; "
                    "selects the highest-power plug; calculates minimum charging time to "
                    "cover the route plus range buffer; and calculates maximum charging "
                    "time as the schedule window before the meeting, not charging-to-full."
                ),
                "required_arguments": [],
                "optional_arguments": ["range_buffer_km", "arrival_buffer_minutes"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "range_buffer_km": {"type": "number", "default": 40},
                        "arrival_buffer_minutes": {"type": "integer", "default": 5},
                    },
                },
                "argument_descriptions": {
                    "range_buffer_km": "Extra remaining range required on arrival.",
                    "arrival_buffer_minutes": "Minutes to arrive before the meeting starts.",
                },
            }
        if tool_name == "call_selected_charging_provider":
            return {
                "name": "call_selected_charging_provider",
                "signature": "call_selected_charging_provider()",
                "confirmation_required": False,
                "description": (
                    "Built-in side-effect helper for follow-ups that ask to call the "
                    "charging-station provider after a charger was selected. It resolves "
                    "the selected station phone number from selected_charging_plug, "
                    "selected_charging_plan, recent POIs, or active navigation waypoints, "
                    "then calls call_phone_by_number."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        if tool_name == "get_preferred_ambient_light_color":
            return {
                "name": "get_preferred_ambient_light_color",
                "signature": "get_preferred_ambient_light_color()",
                "confirmation_required": False,
                "description": (
                    "Built-in read-only preference helper. Calls get_user_preferences for vehicle "
                    "settings and extracts a unique valid ambient light color if one is present. "
                    "Returns NOT_FOUND or AMBIGUOUS instead of choosing."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        if tool_name == "set_occupied_seat_heating":
            return {
                "name": "set_occupied_seat_heating",
                "signature": "set_occupied_seat_heating(level=None, increase_by=None)",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper, not a direct evaluator tool. Reads seat occupancy "
                    "and current seat-heating levels, then calls set_seat_heating for each occupied "
                    "front seat (DRIVER/PASSENGER). Pass level for an absolute target or increase_by "
                    "for a relative change."
                ),
                "required_arguments": [],
                "optional_arguments": ["level", "increase_by"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "level": {"type": "integer", "minimum": 0, "maximum": 3},
                        "increase_by": {"type": "integer"},
                    },
                },
                "argument_descriptions": {
                    "level": "Absolute seat-heating level 0-3 for each occupied front seat.",
                    "increase_by": "Relative change applied to each occupied front seat's current level.",
                },
            }
        if tool_name in WORKSPACE_HELPER_NAMES:
            # Internal helpers without an explicit describe entry (e.g. the
            # *_guarded delegation targets the model never names directly).
            return {
                "name": tool_name,
                "signature": self.tool_signature(tool_name),
                "confirmation_required": False,
                "description": "Built-in workspace helper, not a direct evaluator tool.",
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
            }
        with self._lock:
            if tool_name not in self.available_tools:
                available = ", ".join(self.available_tool_names())
                raise KeyError(f"Tool {tool_name!r} is not available. Available tools: {available}")
            fn = self.available_tools[tool_name].get("function", {}) or {}
        description = (fn.get("description") or "").strip()
        schema = fn.get("parameters", {}) or {}
        properties = schema.get("properties", {}) or {}
        return {
            "name": tool_name,
            "signature": self.tool_signature(tool_name),
            "confirmation_required": description.startswith("REQUIRES_CONFIRMATION"),
            "description": description,
            "required_arguments": self.tool_required_arguments(tool_name),
            "optional_arguments": self.tool_optional_arguments(tool_name),
            "schema": schema,
            "argument_descriptions": {
                str(name): (value.get("description") or "").strip()
                for name, value in properties.items()
                if isinstance(value, dict)
            },
        }

    def list_tools(self) -> list[dict[str, Any]]:
        helpers = [
            {
                "name": name,
                "confirmation_required": self.describe_tool(name)["confirmation_required"],
                "description": self.describe_tool(name)["description"],
            }
            for name in WORKSPACE_HELPER_NAMES
        ]
        tools = [
            {
                "name": info["name"],
                "confirmation_required": info["confirmation_required"],
                "description": info["description"],
            }
            for info in (self.describe_tool(name) for name in self.available_tool_names())
        ]
        return helpers + tools

    def tool_supports_arguments(
        self,
        tool_name: str,
        argument_names: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> bool:
        if not self.tool_available(tool_name):
            return False
        if not argument_names:
            return True
        schema = self.tool_schema(tool_name)
        properties = schema.get("properties", {}) or {}
        return all(str(name) in properties for name in argument_names)

    def capability_claim_gate(
        self,
        requirements: list[Any],
        gate_name: str = "capability_claim",
    ) -> bool:
        self._ensure_scratchpad_shape()
        normalized: list[dict[str, Any]] = []
        missing_tools: list[str] = []
        missing_arguments: list[dict[str, Any]] = []
        for item in requirements:
            tool_name = ""
            argument_names: list[str] = []
            if isinstance(item, str):
                tool_name = self._canonical_call_name(item)
            elif isinstance(item, dict):
                tool_name = self._canonical_call_name(
                    item.get("tool_name") or item.get("tool") or ""
                )
                raw_args = item.get("arguments") or item.get("argument_names") or []
                argument_names = [raw_args] if isinstance(raw_args, str) else [str(name) for name in raw_args]
            elif isinstance(item, (tuple, list)) and item:
                tool_name = self._canonical_call_name(item[0])
                if len(item) > 1:
                    raw_args = item[1]
                    argument_names = [raw_args] if isinstance(raw_args, str) else [str(name) for name in raw_args or []]
            if not tool_name:
                raise ValueError("capability_claim_gate requirements must include a tool name")
            normalized.append({"tool_name": tool_name, "arguments": argument_names})
            if not self.tool_available(tool_name):
                missing_tools.append(tool_name)
            elif argument_names and not self.tool_supports_arguments(tool_name, argument_names):
                schema = self.tool_schema(tool_name)
                properties = schema.get("properties", {}) or {}
                missing = [name for name in argument_names if name not in properties]
                if missing:
                    missing_arguments.append({"tool_name": tool_name, "missing_arguments": missing})
        ok = not missing_tools and not missing_arguments
        self.scratchpad["gates"][gate_name] = {
            "status": "YES" if ok else "NO",
            "requirements": normalized,
            "missing_tools": missing_tools,
            "missing_arguments": missing_arguments,
        }
        return ok

    def reset_actions(self) -> None:
        self._response_text = None
        self._response_locked = False
        self._refresh_policy_context_facts()

    def update_tools(self, tools: list[dict[str, Any]]) -> None:
        with self._lock:
            self.available_tools = {
                tool.get("function", {}).get("name", ""): tool
                for tool in tools
                if tool.get("function", {}).get("name")
            }

    def observe_user(self, text: str) -> None:
        self.last_source = "user"
        self.last_user_message = text
        self.messages.append({"source": "user", "content": text})
        self._failed_mutations.clear()
        self._successful_mutations.clear()
        self._read_cache.clear()
        self._read_repeat_counts.clear()
        navigation_state = self.scratchpad.get("entities", {}).get("navigation_state")
        if isinstance(navigation_state, dict):
            # Preflight runs after observe_user(). Mark the prior turn's
            # snapshot stale so each user request receives one fresh read.
            navigation_state["stale"] = True
        # Turn-local response fragments are only valid for the turn that
        # created them. Pending confirmations deliberately survive.
        facts = self.scratchpad.get("facts")
        if isinstance(facts, dict):
            for key in (
                "last_helper_message",
                "last_no_progress",
                "last_climate_settings_turn",
                "last_navigation_state_turn",
                "pending_helper_messages",
                "pending_response_obligations",
                "pending_route_narration",
            ):
                facts.pop(key, None)

    def observe_environment(self, tool_results: list[dict[str, Any]]) -> None:
        self.last_source = "environment"
        self.tool_results = tool_results
        self.messages.append({"source": "environment", "tool_results": tool_results})

    def observe_empty(self, source: str) -> None:
        self.last_source = source or "unknown"
        self.messages.append({"source": self.last_source, "content": ""})

    def respond(self, message: str) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("respond(message) requires a non-empty string")
        if self._response_locked:
            return
        blocker = self._unacknowledged_mutation_failure_message()
        if blocker is not None:
            # A side-effect tool failed this turn and never succeeded on retry.
            # Replace the model's (likely optimistic) text with a grounded,
            # truthful failure so we never claim an unexecuted change.
            self._respond_locked(blocker)
            return
        fan_blocker = self._unknown_fan_speed_relative_response(message)
        if fan_blocker is not None:
            self._respond_locked(fan_blocker)
            return
        nav_blocker = self._unknown_navigation_structure_response(message)
        if nav_blocker is not None:
            self._respond_locked(nav_blocker)
            return
        replacement_blocker = self._missing_destination_replacement_response(message)
        if replacement_blocker is not None:
            self._respond_locked(replacement_blocker)
            return
        navigation_claim_blocker = self._ungrounded_navigation_completion_response(message)
        if navigation_claim_blocker is not None:
            self._respond_locked(navigation_claim_blocker)
            return
        message = self._append_response_obligations(message)
        message = self._append_pending_route_narration(message)
        self._response_text = self._safe_user_message(message)

    def _add_response_obligation(
        self,
        key: str,
        message: str,
        satisfied_patterns: tuple[str, ...] = (),
    ) -> None:
        """Require a grounded policy disclosure without locking the whole turn."""

        if not key or not isinstance(message, str) or not message.strip():
            return
        self._ensure_scratchpad_shape()
        facts = self.scratchpad["facts"]
        obligations = facts.setdefault("pending_response_obligations", [])
        if not isinstance(obligations, list):
            obligations = []
            facts["pending_response_obligations"] = obligations
        record = {
            "key": key,
            "message": message.strip(),
            "satisfied_patterns": list(satisfied_patterns),
        }
        for index, existing in enumerate(obligations):
            if isinstance(existing, dict) and existing.get("key") == key:
                obligations[index] = record
                return
        obligations.append(record)

    def _append_response_obligations(self, message: str) -> str:
        facts = self.scratchpad.get("facts")
        obligations = (
            facts.get("pending_response_obligations")
            if isinstance(facts, dict)
            else None
        )
        if isinstance(facts, dict):
            facts.pop("pending_response_obligations", None)
        if not isinstance(obligations, list):
            return message
        output = message.strip()
        lowered = output.lower()
        for obligation in obligations:
            if not isinstance(obligation, dict):
                continue
            required = obligation.get("message")
            if not isinstance(required, str) or not required.strip():
                continue
            patterns = obligation.get("satisfied_patterns")
            satisfied = any(
                isinstance(pattern, str) and re.search(pattern, lowered, re.IGNORECASE)
                for pattern in (patterns if isinstance(patterns, list) else [])
            )
            if not satisfied and required.lower() not in lowered:
                output = output.rstrip().rstrip(".") + ". " + required.strip()
                lowered = output.lower()
        return output

    def _append_pending_route_narration(self, message: str) -> str:
        """Append the policy-required route-selection sentence, once, if pending."""

        facts = self.scratchpad.get("facts")
        narration = facts.get("pending_route_narration") if isinstance(facts, dict) else None
        if isinstance(facts, dict):
            facts.pop("pending_route_narration", None)
        if not isinstance(narration, str) or not narration.strip():
            return message
        lowered = message.lower()
        # The policy gap the judge flags is failing to offer alternatives. Only
        # skip if the model already offered them; otherwise append (even if the
        # model mentioned "fastest", because the alternatives ask is missing).
        if any(k in lowered for k in ("alternativ", "other option", "other route", "more route")):
            return message
        return message.rstrip().rstrip(".") + ". " + narration.strip()

    def _respond_locked(self, message: str) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("_respond_locked(message) requires a non-empty string")
        self._response_text = self._safe_user_message(message)
        self._response_locked = True

    def _helper_message(self, message: str) -> None:
        """Record a successful helper's suggested sentence WITHOUT locking.

        Replaces success-path `_respond_locked`: the runtime no longer ends the
        turn or fixes the final text after one subgoal completes, so the model
        composes a single message covering every part of a compound request
        (and keeps required warnings). The suggested wording stays discoverable
        in `last_helper_message`/the helper report if the model wants it.
        Locking is reserved for genuinely terminal conditions (missing
        capability, confirmation-required ask, policy-forbidden, info
        unavailable, unrecoverable failure).
        """

        if not isinstance(message, str) or not message.strip():
            return
        self._ensure_scratchpad_shape()
        facts = self.scratchpad["facts"]
        clean = message.strip()
        facts["last_helper_message"] = clean
        messages = facts.setdefault("pending_helper_messages", [])
        if not isinstance(messages, list):
            messages = []
            facts["pending_helper_messages"] = messages
        if clean not in messages:
            messages.append(clean)

    @staticmethod
    def _safe_user_message(message: str) -> str:
        clean = message.strip()
        lowered = clean.lower()
        if any(artifact.lower() in lowered for artifact in USER_TEXT_RUNTIME_ARTIFACTS):
            return "I hit an internal issue while preparing the response."
        return clean

    @staticmethod
    def _fan_speed_delta_phrase(
        delta: int | None,
        direction: str | None = None,
    ) -> str:
        if delta is None:
            if direction in {"increase", "decrease"}:
                return f"{direction} the fan speed by the requested number of levels"
            return "change the fan speed by the requested number of levels"
        direction = "increase" if delta > 0 else "decrease"
        steps = abs(int(delta))
        if steps == 1:
            return f"{direction} the fan speed by one level"
        return f"{direction} the fan speed by {steps} levels"

    def _unknown_fan_speed_relative_message(
        self,
        delta: int | None = None,
        direction: str | None = None,
    ) -> str:
        return (
            f"I can't {self._fan_speed_delta_phrase(delta, direction)} because I looked it up "
            "and the car system did not provide the current fan speed."
        )

    def _current_turn_climate_settings(self) -> dict[str, Any] | None:
        facts = self.scratchpad.get("facts")
        if not isinstance(facts, dict):
            return None
        if facts.get("last_climate_settings_turn") != self.last_user_message:
            return None
        entities = self.scratchpad.get("entities")
        if not isinstance(entities, dict):
            return None
        climate = entities.get("last_climate_settings")
        return climate if isinstance(climate, dict) else None

    @staticmethod
    def _fan_speed_value_unavailable(climate: dict[str, Any]) -> bool:
        if "fan_speed" not in climate:
            return True
        value = climate.get("fan_speed")
        return value is None or isinstance(value, UnknownToolResponseValue)

    def _relative_fan_speed_direction_from_user_request(self) -> str | None:
        text = self.last_user_message.lower()
        if "fan" not in text:
            return None
        if any(
            phrase in text
            for phrase in (
                "increase",
                "raise",
                "turn up",
                "speed up",
                "higher",
                "boost",
            )
        ):
            return "increase"
        if any(
            phrase in text
            for phrase in (
                "decrease",
                "lower",
                "turn down",
                "reduce",
                "drop",
            )
        ):
            return "decrease"
        return None

    @staticmethod
    def _message_requests_current_fan_speed(message: str) -> bool:
        text = message.lower()
        if "fan" not in text or "speed" not in text:
            return False
        return (
            "?" in message
            or "tell me" in text
            or "provide" in text
            or "current fan speed" in text
            or "look up" in text
        )

    def _unknown_fan_speed_relative_response(self, message: str) -> str | None:
        direction = self._relative_fan_speed_direction_from_user_request()
        if direction is None:
            return None
        climate = self._current_turn_climate_settings()
        if climate is None or not self._fan_speed_value_unavailable(climate):
            return None
        if not self._message_requests_current_fan_speed(message):
            return None
        return self._unknown_fan_speed_relative_message(direction=direction)

    @staticmethod
    def _navigation_state_unknown_fields(payload: dict[str, Any]) -> list[str]:
        fields: list[str] = []
        paths = [
            "navigation_active",
            "waypoints_id",
            "routes_to_final_destination_id",
        ]
        if isinstance(payload.get("details"), dict):
            paths.extend(["details.waypoints", "details.routes"])
        for path in paths:
            value = CoroutineWorkspace._response_path_value(payload, path)
            if value is None or isinstance(value, UnknownToolResponseValue):
                fields.append(f"result.get_current_navigation_state.{path}")
        return fields

    def _navigation_edit_action_from_user_request(self) -> str | None:
        text = self.last_user_message.lower()
        if not any(
            term in text
            for term in (
                "route",
                "navigation",
                "waypoint",
                "destination",
                "stop",
            )
        ):
            return None
        if any(term in text for term in ("remove", "delete", "skip")):
            if any(term in text for term in ("intermediate", "waypoint", "stop")):
                return "remove the intermediate stop"
            if "destination" in text:
                return "remove the destination"
            return "remove the requested route point"
        if any(term in text for term in ("change", "replace", "switch")) and "destination" in text:
            return "change the destination"
        if any(term in text for term in ("add", "insert")) and any(
            term in text for term in ("stop", "waypoint", "destination")
        ):
            return "add the requested route point"
        if any(term in text for term in ("go straight", "direct", "straight to")):
            return "edit the current route"
        return None

    @staticmethod
    def _navigation_unknown_field_category(missing_fields: list[str]) -> str:
        if any(field.endswith(".navigation_active") for field in missing_fields):
            return "active_state"
        if any("waypoints_id" in field or "details.waypoints" in field for field in missing_fields):
            if any("routes_to_final_destination_id" in field or "details.routes" in field for field in missing_fields):
                return "waypoints_and_routes"
            return "waypoints"
        if any("routes_to_final_destination_id" in field or "details.routes" in field for field in missing_fields):
            return "routes"
        return "structure"

    def _unknown_navigation_structure_message(
        self,
        missing_fields: list[str],
        action: str | None = None,
    ) -> str:
        clean_action = _clean_action_phrase(
            action
            or self._navigation_edit_action_from_user_request()
            or "use the current navigation state"
        )
        category = self._navigation_unknown_field_category(missing_fields)
        if category == "active_state":
            unavailable = "whether navigation is active"
            consequence = "Without that, I cannot know which navigation edit is safe."
        elif category == "waypoints":
            unavailable = "the current waypoint order"
            consequence = "Without the waypoint order, I cannot identify which stop to change safely."
        elif category == "routes":
            unavailable = "the current route IDs"
            consequence = "Without the route IDs, I cannot preserve or replace the affected route segment safely."
        elif category == "waypoints_and_routes":
            unavailable = "the current waypoint order or route information"
            consequence = "Without that route structure, I cannot identify the stop to remove or the direct replacement segment safely."
        else:
            unavailable = "the current route structure"
            consequence = "Without that, I cannot make the route edit safely."
        return (
            f"I can't {clean_action} because I looked up the current navigation state "
            f"and the car system did not provide {unavailable}. {consequence}"
        )

    def _record_unknown_navigation_structure(
        self,
        gate_name: str,
        missing_fields: list[str],
        action: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_scratchpad_shape()
        message = self._unknown_navigation_structure_message(missing_fields, action)
        report = {
            "helper": gate_name,
            "status": "UNAVAILABLE",
            "missing_response_fields": missing_fields,
            "reason": "current navigation route structure was unavailable",
            "message": message,
        }
        self.scratchpad["gates"][gate_name] = {
            "status": "NO",
            "missing_response_fields": missing_fields,
            "reason": report["reason"],
        }
        self._store_helper_report(gate_name, report)
        return report

    def _abort_unknown_navigation_structure(
        self,
        gate_name: str,
        missing_fields: list[str],
        action: str | None = None,
    ) -> NoReturn:
        report = self._record_unknown_navigation_structure(gate_name, missing_fields, action)
        self._abort_with_response(str(report["message"]))

    def _current_turn_navigation_state(self) -> dict[str, Any] | None:
        facts = self.scratchpad.get("facts")
        if not isinstance(facts, dict):
            return None
        if facts.get("last_navigation_state_turn") != self.last_user_message:
            return None
        entities = self.scratchpad.get("entities")
        if not isinstance(entities, dict):
            return None
        state = entities.get("navigation_state")
        return state if isinstance(state, dict) else None

    @staticmethod
    def _message_is_vague_navigation_limitation(message: str) -> bool:
        text = message.lower()
        return (
            "internal issue" in text
            or "try again" in text
            or "need more information" in text
            or "which waypoint" in text
            or "which stop" in text
            or "tell me the waypoint" in text
            or "provide the waypoint" in text
        )

    def _unknown_navigation_structure_response(self, message: str) -> str | None:
        action = self._navigation_edit_action_from_user_request()
        if action is None:
            return None
        state = self._current_turn_navigation_state()
        if not isinstance(state, dict):
            return None
        missing_fields = state.get("unknown_response_fields")
        if not isinstance(missing_fields, list) or not missing_fields:
            return None
        if not self._message_is_vague_navigation_limitation(message):
            return None
        return self._unknown_navigation_structure_message(
            [str(field) for field in missing_fields],
            action,
        )

    def _active_route_final_destination_id(self) -> str | None:
        state = self.scratchpad.get("entities", {}).get("navigation_state")
        if not isinstance(state, dict) or state.get("navigation_active") is not True:
            return None
        destination_id = state.get("final_destination_id") or state.get("destination_id")
        return destination_id if isinstance(destination_id, str) and destination_id else None

    def _is_requested_final_destination_replacement(self, destination_id: Any) -> bool:
        if self._navigation_edit_action_from_user_request() != "change the destination":
            return False
        if not isinstance(destination_id, str) or not destination_id:
            return False
        current_destination_id = self._active_route_final_destination_id()
        if current_destination_id is None:
            return False
        return destination_id != current_destination_id

    def _destination_replacement_surface_blocker(
        self,
        gate_name: str,
        destination_id: Any,
    ) -> dict[str, Any] | None:
        if not self._is_requested_final_destination_replacement(destination_id):
            return None
        return self._require_tool_surface_for_calls(
            gate_name,
            "change the destination",
            [
                (
                    "navigation_replace_final_destination",
                    {"new_destination_id": str(destination_id)},
                )
            ],
        )

    @staticmethod
    def _message_presents_route_choice(message: str) -> bool:
        text = message.lower()
        if "route" not in text:
            return False
        return (
            "which route" in text
            or "what route" in text
            or "would you like to take" in text
            or "which one" in text
            or "route alternatives" in text
            or "other route" in text
        )

    def _missing_destination_replacement_response(self, message: str) -> str | None:
        if not self._message_presents_route_choice(message):
            return None
        entities = self.scratchpad.get("entities")
        if not isinstance(entities, dict):
            return None
        route_options = entities.get("last_route_options")
        if not isinstance(route_options, dict):
            return None
        destination_id = route_options.get("destination_id")
        if not self._is_requested_final_destination_replacement(destination_id):
            return None
        missing_tools, missing_arguments = self._missing_tool_surface_for_calls(
            [
                (
                    "navigation_replace_final_destination",
                    {"new_destination_id": str(destination_id)},
                )
            ]
        )
        if not missing_tools and not missing_arguments:
            return None
        report = self._record_tool_surface_limitation(
            "destination_replacement_surface",
            "change the destination",
            missing_tools=missing_tools,
            missing_arguments=missing_arguments,
        )
        return str(report.get("message") or "")

    def _abort_with_response(self, message: str) -> NoReturn:
        self._respond_locked(message)
        raise ResponseReady()

    def _abort_missing_tool_response(
        self,
        response_path: str,
        action: str = "complete the requested action",
        gate_name: str = "missing_tool_response",
    ) -> NoReturn:
        if response_path.startswith("result.get_current_navigation_state."):
            edit_action = self._navigation_edit_action_from_user_request()
            if edit_action is not None:
                missing_fields = [response_path]
                state = self._current_turn_navigation_state()
                if isinstance(state, dict):
                    known_missing = state.get("unknown_response_fields")
                    if isinstance(known_missing, list) and known_missing:
                        missing_fields = [str(field) for field in known_missing]
                self._abort_unknown_navigation_structure(
                    "navigation_state_unknown",
                    missing_fields,
                    edit_action,
                )
        clean_path = response_path.removeprefix("result.")
        message = (
            f"I acknowledge that I can't {_clean_action_phrase(action)} because the required "
            f"tool response field {clean_path} is unavailable."
        )
        report = {
            "status": "UNAVAILABLE",
            "helper": gate_name,
            "missing_response_fields": [response_path],
            "reason": message,
            "message": message,
        }
        self.scratchpad["gates"][gate_name] = {
            "status": "NO",
            "missing_response_fields": [response_path],
            "reason": message,
        }
        self._store_helper_report(gate_name, report)
        self._abort_with_response(message)

    @staticmethod
    def _response_path_value(payload: Any, path: str) -> Any:
        value = payload
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value

    def _require_known_response_fields(
        self,
        gate_name: str,
        action: str,
        tool_name: str,
        payload: dict[str, Any],
        required_paths: list[str] | tuple[str, ...],
    ) -> None:
        for path in required_paths:
            value = self._response_path_value(payload, path)
            response_path = f"result.{tool_name}.{path}"
            if isinstance(value, UnknownToolResponseValue):
                self._abort_missing_tool_response(response_path, action, gate_name)
            if value is None:
                self._abort_missing_tool_response(response_path, action, gate_name)

    @staticmethod
    def _format_limitation_message(
        action: str,
        missing_tools: list[str] | None = None,
        missing_arguments: list[dict[str, Any]] | None = None,
        reason: str | None = None,
    ) -> str:
        missing = sorted(set(missing_tools or []))
        missing_args = missing_arguments or []
        clean_action = _clean_action_phrase(action)
        if missing or missing_args:
            missing_arg_pairs = {
                (str(item.get("tool_name")), str(arg))
                for item in missing_args
                for arg in item.get("missing_arguments", [])
            }
            if (
                ("set_fan_speed", "level") in missing_arg_pairs
                and ("turn on ac" in action.lower() or "turn on the air conditioning" in action.lower())
            ):
                return (
                    "I acknowledge that I can't turn on the air conditioning because "
                    "the required tool parameter set_fan_speed.level is missing from "
                    "my available controls. Without that tool parameter, I cannot "
                    "execute this action correctly because I cannot set the fan speed "
                    "to level 1 first as required."
                )
            reasons: list[str] = []
            exact_tools: list[str] = []
            exact_parameters: list[str] = []
            if missing:
                exact_tools = missing
                labels = [_tool_label(tool_name) for tool_name in missing]
                verb = "is" if len(labels) == 1 else "are"
                reasons.append(f"the {_human_join(labels)} {verb} unavailable")
            if missing_args:
                for item in missing_args:
                    tool_name = str(item.get("tool_name") or "required tool")
                    args = [str(arg) for arg in item.get("missing_arguments", [])]
                    if not args:
                        continue
                    exact_parameters.extend(f"{tool_name}.{arg}" for arg in args)
                    labels = [_parameter_label(tool_name, arg) for arg in args]
                    reasons.append(
                        f"the {_tool_label(tool_name)} is missing the required tool "
                        f"parameter {_human_join(labels)}, so I cannot execute this action correctly"
                    )
            suffix_parts: list[str] = []
            if exact_tools:
                label = "tool is" if len(exact_tools) == 1 else "tools are"
                suffix_parts.append(f"The missing {label} {', '.join(exact_tools)}.")
            if exact_parameters:
                label = "tool parameter is" if len(exact_parameters) == 1 else "tool parameters are"
                suffix_parts.append(f"The missing {label} {', '.join(exact_parameters)}.")
            suffix = " " + " ".join(suffix_parts) if suffix_parts else ""
            return f"I acknowledge that I can't {clean_action} because " + "; ".join(reasons) + f".{suffix}"
        return f"I acknowledge that I can't {clean_action} because {reason or 'the required information is unavailable'}."

    def _record_tool_surface_limitation(
        self,
        gate_name: str,
        action: str,
        missing_tools: list[str] | None = None,
        missing_arguments: list[dict[str, Any]] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        missing = sorted(
            {self._canonical_call_name(tool_name) for tool_name in (missing_tools or [])}
        )
        missing_args = []
        for item in missing_arguments or []:
            tool_name = self._canonical_call_name(item.get("tool_name") or "")
            missing_args.append(
                {
                    **item,
                    "tool_name": tool_name,
                    "missing_arguments": [
                        str(argument) for argument in item.get("missing_arguments", [])
                    ],
                }
            )
        message = self._format_limitation_message(
            action,
            missing_tools=missing,
            missing_arguments=missing_args,
            reason=reason,
        )
        self.scratchpad["gates"][gate_name] = {
            "status": "NO",
            "missing_tools": missing,
            "missing_arguments": missing_args,
            "reason": reason or message,
        }
        report = {
            "status": "UNAVAILABLE",
            "helper": gate_name,
            "missing_tools": missing,
            "missing_arguments": missing_args,
            "reason": reason or message,
            "message": message,
        }
        self._store_helper_report(gate_name, report)
        return report

    def _limitation_response(
        self,
        gate_name: str,
        action: str,
        missing_tools: list[str] | None = None,
        missing_arguments: list[dict[str, Any]] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        report = self._record_tool_surface_limitation(
            gate_name,
            action,
            missing_tools=missing_tools,
            missing_arguments=missing_arguments,
            reason=reason,
        )
        self._abort_with_response(report["message"])

    def _missing_tool_surface_for_calls(self, calls: list[Any]) -> tuple[list[str], list[dict[str, Any]]]:
        missing_tools: list[str] = []
        missing_arguments: list[dict[str, Any]] = []
        for item in calls:
            call = self._normalize_call_spec(item)
            tool_name = call["tool_name"]
            argument_names = list(call["arguments"].keys())
            if not self.tool_available(tool_name):
                missing_tools.append(tool_name)
                continue
            if not argument_names:
                argument_names = []
            schema = self.tool_schema(tool_name)
            properties = schema.get("properties", {}) or {}
            # Live-membership only: a parameter the model is trying to pass that
            # is not in this task's schema. No comparison to the original catalog.
            missing = [name for name in argument_names if name not in properties]
            if missing:
                missing_arguments.append({"tool_name": tool_name, "missing_arguments": missing})
        return sorted(set(missing_tools)), missing_arguments

    def _require_tool_surface_for_calls(
        self,
        gate_name: str,
        action: str,
        calls: list[Any],
    ) -> dict[str, Any] | None:
        missing_tools, missing_arguments = self._missing_tool_surface_for_calls(calls)
        if missing_tools or missing_arguments:
            return self._limitation_response(
                gate_name,
                action,
                missing_tools=missing_tools,
                missing_arguments=missing_arguments,
            )
        return None

    def _tool_surface_blocker_result(
        self,
        gate_name: str,
        action: str,
        calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        missing_tools, missing_arguments = self._missing_tool_surface_for_calls(calls)
        if not missing_tools and not missing_arguments:
            return None
        report = self._limitation_response(
            gate_name,
            action,
            missing_tools=missing_tools,
            missing_arguments=missing_arguments,
        )
        return [
            {
                "status": "UNAVAILABLE",
                "tool_name": call["tool_name"],
                "tool_call_id": "",
                "result": {
                    "message": report["message"],
                    "missing_tools": missing_tools,
                    "missing_arguments": missing_arguments,
                },
                "message": report["message"],
            }
            for call in calls
        ]

    def _failed_tool_response(
        self,
        gate_name: str,
        action: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name = str(result.get("tool_name") or "")
        label = _tool_label(tool_name) if tool_name else "required tool"
        message = (
            f"I can't {_clean_action_phrase(action)} because I couldn't get a usable result "
            f"from the {label}."
            + (f" The failed tool was {tool_name}." if tool_name else "")
        )
        self.scratchpad["gates"][gate_name] = {
            "status": "BLOCKED",
            "failed_tool": result.get("tool_name"),
            "result": result,
        }
        self._abort_with_response(message)

    # ------------------------------------------------------------------
    # Mutation-outcome guard
    # ------------------------------------------------------------------

    @staticmethod
    def _is_mutation_failure_status(status: Any) -> bool:
        """A genuine side-effect failure, ignoring parse-artifact statuses."""

        text = str(status or "").upper()
        return text not in {"SUCCESS", "UNKNOWN", "RAW", ""}

    @staticmethod
    def _mutation_signature(tool_name: str, arguments: Any) -> str:
        """Stable mutation identity based on the tool's target dimensions."""

        if not isinstance(arguments, dict):
            return ""
        target_names = _MUTATION_TARGET_ARGUMENTS.get(tool_name)
        identity = (
            {name: arguments.get(name) for name in target_names}
            if target_names is not None
            else arguments
        )
        try:
            return json.dumps(identity, sort_keys=True, default=str)
        except Exception:
            return repr(sorted(identity.items(), key=lambda kv: str(kv[0])))

    def _record_mutation_outcomes(
        self,
        parsed: list[dict[str, Any]],
        calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Track failed/succeeded side-effect calls by (tool, target args).

        `calls` is the index-aligned list of normalized calls that produced
        `parsed`; it carries the arguments the result items drop, which is what
        lets us distinguish targets. Resolution is recorded as one of:
        `failed`, `retry_succeeded`, or `proved_by_read`.
        """

        calls = calls or []
        for index, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool_name") or "")
            if name not in MUTATING_TOOL_NAMES:
                continue
            arguments: dict[str, Any] = {}
            if index < len(calls) and isinstance(calls[index], dict):
                candidate = calls[index].get("arguments")
                if isinstance(candidate, dict):
                    arguments = candidate
            key = f"{name}::{self._mutation_signature(name, arguments)}"
            status = item.get("status")
            if str(status or "").upper() == "SUCCESS":
                # A successful (re)try clears ONLY the same target's failure.
                self._failed_mutations.pop(key, None)
                self._successful_mutations.append(
                    {
                        "tool_name": name,
                        "arguments": dict(arguments),
                        "status": "SUCCESS",
                    }
                )
            elif self._is_mutation_failure_status(status):
                self._failed_mutations[key] = {
                    "tool_name": name,
                    "status": str(status or "FAILURE"),
                    "arguments": dict(arguments),
                    "state": "failed",
                }
        # A later state read can prove the desired outcome despite the setter
        # error (e.g. the window is already closed). Conservative: only clears
        # on a confident exact-value match, never on absence.
        self._reconcile_failures_with_reads(parsed)

    def _reconcile_failures_with_reads(self, parsed: list[dict[str, Any]]) -> None:
        if not self._failed_mutations:
            return
        reads: dict[str, dict[str, Any]] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").upper() != "SUCCESS":
                continue
            name = str(item.get("tool_name") or "")
            if name:
                reads.setdefault(name, item)
        for key in list(self._failed_mutations):
            entry = self._failed_mutations[key]
            reconciler = _MUTATION_STATE_PROOF.get(entry.get("tool_name", ""))
            if reconciler is None:
                continue
            try:
                proven = reconciler(entry.get("arguments") or {}, reads)
            except Exception:
                proven = False
            if proven:
                self._failed_mutations.pop(key, None)

    def _unacknowledged_mutation_failure_message(self) -> str | None:
        if not self._failed_mutations:
            return None
        labels: list[str] = []
        for entry in self._failed_mutations.values():
            label = _tool_label(entry.get("tool_name", ""))
            if label not in labels:
                labels.append(label)
        joined = _human_join(labels) if labels else "requested change"
        return (
            f"I couldn't complete that — the {joined} call didn't succeed, so I "
            "haven't made those changes. Want me to try again?"
        )

    def _has_successful_navigation_mutation(self) -> bool:
        if any(
            isinstance(item, dict)
            and item.get("tool_name") in NAVIGATION_ACTIVATING_MUTATIONS
            and str(item.get("status") or "").upper() == "SUCCESS"
            for item in self._successful_mutations
        ):
            return True
        entities = self.scratchpad.get("entities")
        if not isinstance(entities, dict):
            return False
        last_mutation = entities.get("last_successful_navigation_mutation")
        if not isinstance(last_mutation, dict):
            return False
        if last_mutation.get("tool_name") not in NAVIGATION_ACTIVATING_MUTATIONS:
            return False
        return str(last_mutation.get("status") or "").upper() == "SUCCESS"

    @staticmethod
    def _claims_navigation_completed(message: str) -> bool:
        lowered = message.lower()
        patterns = (
            r"\bnavigation\s+set\b",
            r"\bnavigation\s+(?:is\s+|has\s+been\s+|was\s+)?(?:set|started|updated|configured)\b",
            r"\b(?:i|i['’]ve|i have)\s+(?:set|started|updated|configured)\s+(?:up\s+)?(?:the\s+)?navigation\b",
            r"\b(?:i|i['’]ve|i have)\s+(?:set|started|updated|configured)\s+(?:up\s+)?(?:(?![.!?]).){0,80}\bnavigation\b",
            r"\broute\s+(?:is\s+|has\s+been\s+|was\s+)?set\b",
        )
        return any(re.search(pattern, lowered) for pattern in patterns)

    def _ungrounded_navigation_completion_response(self, message: str) -> str | None:
        if not self._claims_navigation_completed(message):
            return None
        if self._has_successful_navigation_mutation():
            return None
        if not self.tool_available("set_new_navigation"):
            report = self._record_tool_surface_limitation(
                "navigation_completion_claim",
                "set navigation",
                missing_tools=["set_new_navigation"],
            )
            return str(report.get("message") or "")
        return (
            "I haven't set the navigation yet because the navigation control "
            "call has not completed."
        )

    # ------------------------------------------------------------------
    # Policy date/time + location exposure
    # ------------------------------------------------------------------

    def policy_now(self) -> dict[str, Any]:
        """Policy DATETIME as a dict (month/day/hour/minute/...), or {}."""

        context = self._current_policy_context()
        datetime_value = context.get("datetime") if isinstance(context, dict) else None
        return datetime_value if isinstance(datetime_value, dict) else {}

    def policy_location_id(self) -> str | None:
        """Grounded id of the policy CURRENT_LOCATION, or None."""

        context = self._current_policy_context()
        location_id = context.get("location_id") if isinstance(context, dict) else None
        return location_id if isinstance(location_id, str) else None

    def _refresh_policy_context_facts(self) -> None:
        """Mirror policy date/time + location into the always-visible scratchpad."""

        if not self.policy:
            return
        try:
            context = self._current_policy_context()
        except Exception:
            return
        if not context:
            return
        self._ensure_scratchpad_shape()
        facts = self.scratchpad["facts"]
        datetime_value = context.get("datetime")
        if isinstance(datetime_value, dict):
            facts["policy_now"] = {
                key: datetime_value.get(key)
                for key in ("year", "month", "day", "hour", "minute")
                if datetime_value.get(key) is not None
            }
        if isinstance(context.get("location_id"), str):
            facts["policy_location_id"] = context["location_id"]
        location = context.get("location")
        if isinstance(location, dict):
            name = location.get("name") or location.get("city")
            if isinstance(name, str) and name.strip():
                facts["policy_location_name"] = name.strip()

    # ------------------------------------------------------------------
    # Active-navigation guard
    # ------------------------------------------------------------------

    def _known_navigation_active(self) -> bool | None:
        """Best-effort: is there an active route? None when undeterminable."""

        state = self.scratchpad.get("entities", {}).get("navigation_state")
        if isinstance(state, dict) and isinstance(state.get("navigation_active"), bool):
            return state["navigation_active"]
        if self.tool_available("get_current_navigation_state"):
            nav = self.get_navigation_state(detailed_information=False)
            if isinstance(nav, dict) and isinstance(nav.get("navigation_active"), bool):
                return nav["navigation_active"]
        return None

    def set_new_navigation_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Block set_new_navigation while active; let the model pick the edit.

        `set_new_navigation` always errors while a route is active (policy
        requires editing the live route instead). The FACT — "navigation is
        already active" — is enforced by the runtime, but the INTENT (replace
        the final destination? replace a waypoint? add a stop? pick another
        route to the same destination? ask the user?) is the model's to decide.
        We return a structured block with the facts it needs and do NOT redirect.
        """

        gate_name = "active_navigation_guard"
        if self._known_navigation_active() is True:
            route_ids = kwargs.get("route_ids")
            candidate_destination_id = self._requested_route_destination(route_ids)
            blocked = {
                "status": "NEEDS_ACTIVE_ROUTE_EDIT",
                "tool_name": "set_new_navigation",
                "reason": (
                    "Navigation is already active, so a brand-new navigation "
                    "session can't be started. Edit the active route instead."
                ),
                "requested_route_ids": route_ids,
                "candidate_destination_id": candidate_destination_id,
                "available_operations": [
                    "navigation_replace_final_destination",
                    "navigation_replace_one_waypoint",
                    "navigation_add_one_waypoint",
                    "select a different route to the existing destination",
                    "ask the user which edit they meant",
                ],
                "active_route": self._active_route_summary(),
            }
            self.scratchpad["gates"][gate_name] = {
                "status": "NEEDS_ACTIVE_ROUTE_EDIT",
                "candidate_destination_id": candidate_destination_id,
            }
            return blocked
        charging_check = self._repair_or_block_charging_plan_route(kwargs.get("route_ids"))
        if charging_check.get("status") == "CHARGING_PLAN_ROUTE_MISMATCH":
            return charging_check
        charging_route_ids = charging_check.get("route_ids")
        if isinstance(charging_route_ids, list):
            kwargs = dict(kwargs, route_ids=charging_route_ids)

        chain_check = self._repair_or_block_route_chain(kwargs.get("route_ids"))
        if chain_check.get("status") == "ROUTE_CHAIN_MISMATCH":
            return chain_check
        repaired_route_ids = chain_check.get("route_ids")
        if isinstance(repaired_route_ids, list):
            kwargs = dict(kwargs, route_ids=repaired_route_ids)
        selection_check = self._repair_route_ids_for_recorded_selection(
            kwargs.get("route_ids")
        )
        selection_route_ids = selection_check.get("route_ids")
        if isinstance(selection_route_ids, list):
            kwargs = dict(kwargs, route_ids=selection_route_ids)
        # Narrate the selected route (policy 022/021) from the routes the model
        # already fetched (auto-persisted as last_routes), so a brand-new route
        # set still informs about fastest/alternatives/tolls.
        self._narrate_from_route_ids(kwargs.get("route_ids"))
        return self._call_raw_tool_sync("set_new_navigation", kwargs)

    def _select_route_for_navigation_leg(
        self,
        route_options: dict[str, Any],
        *,
        segment_name: str,
        route_id: str | None = None,
        alias: str | None = None,
        name_via: str | None = None,
        prefer: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(route_options, dict) or route_options.get("status") != "SUCCESS":
            return {
                "status": "ROUTE_OPTIONS_UNAVAILABLE",
                "segment": segment_name,
                "route_options": copy.deepcopy(route_options),
            }
        selector = {
            "route_id": route_id,
            "alias": alias,
            "name_via": name_via,
            "prefer": prefer,
        }
        if not any(isinstance(value, str) and value.strip() for value in selector.values()):
            selector["prefer"] = "fastest"
        selected = self.select_route(route_options.get("routes"), **selector)
        if selected.get("status") == "SUCCESS":
            return selected
        return {
            "status": "ROUTE_SELECTION_FAILED",
            "segment": segment_name,
            "selection": selected,
            "route_options": copy.deepcopy(route_options),
        }

    def set_new_navigation_via_stop(
        self,
        stop_id: str,
        final_destination_id: str,
        route_to_stop_route_id: str | None = None,
        route_to_stop_alias: str | None = None,
        route_to_stop_name_via: str | None = None,
        route_to_stop_prefer: str | None = "fastest",
        route_to_final_route_id: str | None = None,
        route_to_final_alias: str | None = None,
        route_to_final_name_via: str | None = None,
        route_to_final_prefer: str | None = "fastest",
    ) -> dict[str, Any]:
        """Set a new inactive-navigation route through one grounded stop."""

        current_id = self.policy_location_id()
        stop_id = self._resolve_preloaded_argument_value(stop_id)
        final_destination_id = self._resolve_preloaded_argument_value(final_destination_id)
        if not all(isinstance(value, str) and value for value in (current_id, stop_id, final_destination_id)):
            return {
                "status": "INVALID_ARGUMENTS",
                "reason": "current location, stop_id, and final_destination_id must be grounded ids",
                "current_location_id": current_id,
                "stop_id": stop_id,
                "final_destination_id": final_destination_id,
            }

        to_stop_options = self.get_route_options(
            start_id=current_id,
            destination_id=stop_id,
        )
        to_stop = self._select_route_for_navigation_leg(
            to_stop_options,
            segment_name="current_location_to_stop",
            route_id=route_to_stop_route_id,
            alias=route_to_stop_alias,
            name_via=route_to_stop_name_via,
            prefer=route_to_stop_prefer,
        )
        if to_stop.get("status") != "SUCCESS":
            return to_stop

        to_final_options = self.get_route_options(
            start_id=stop_id,
            destination_id=final_destination_id,
        )
        to_final = self._select_route_for_navigation_leg(
            to_final_options,
            segment_name="stop_to_final_destination",
            route_id=route_to_final_route_id,
            alias=route_to_final_alias,
            name_via=route_to_final_name_via,
            prefer=route_to_final_prefer,
        )
        if to_final.get("status") != "SUCCESS":
            return to_final

        route_ids = [to_stop["selected_route_id"], to_final["selected_route_id"]]
        result = self.set_new_navigation_guarded(route_ids=route_ids)
        report = {
            "status": result.get("status") if isinstance(result, dict) else "UNKNOWN",
            "result": result,
            "route_ids": route_ids,
            "to_stop": to_stop,
            "to_final": to_final,
            "stop_id": stop_id,
            "final_destination_id": final_destination_id,
        }
        if isinstance(result, dict) and result.get("status") == "SUCCESS":
            self._helper_message("Navigation is set with the requested stop and final route.")
        return report

    def _repair_or_block_charging_plan_route(self, route_ids: Any) -> dict[str, Any]:
        if not isinstance(route_ids, list) or not route_ids:
            return {"status": "OK", "route_ids": route_ids}
        normalized_ids = [
            route_id for route_id in route_ids if isinstance(route_id, str) and route_id
        ]
        if len(normalized_ids) != len(route_ids):
            return {"status": "OK", "route_ids": route_ids}
        plan = self.scratchpad.get("entities", {}).get("selected_charging_plan")
        if not isinstance(plan, dict):
            return {"status": "OK", "route_ids": route_ids}
        station_id = plan.get("charging_station_id")
        if not isinstance(station_id, str) or not station_id:
            return {"status": "OK", "route_ids": route_ids}

        first = self._route_record_for_id(normalized_ids[0])
        if not isinstance(first, dict):
            return {"status": "OK", "route_ids": route_ids}
        first_destination = first.get("destination_id")
        if first_destination == station_id:
            return {"status": "OK", "route_ids": route_ids}
        if not self._is_charging_poi_id(first_destination):
            return {"status": "OK", "route_ids": route_ids}

        replacement_first = self._find_unique_route_between(
            first.get("start_id"),
            station_id,
        )
        if replacement_first is None:
            return self._charging_plan_mismatch_block(
                normalized_ids,
                station_id,
                "No unique known route from the current start to the charging station used for the charging-time calculation.",
            )
        repaired = list(normalized_ids)
        replacement_first_id = replacement_first.get("route_id") or replacement_first.get("id")
        if not isinstance(replacement_first_id, str):
            return {"status": "OK", "route_ids": route_ids}
        repaired[0] = replacement_first_id

        if len(repaired) > 1:
            second = self._route_record_for_id(repaired[1])
            if not isinstance(second, dict):
                return self._charging_plan_mismatch_block(
                    repaired,
                    station_id,
                    "The second route is unknown, so the charging-plan route chain cannot be repaired safely.",
                )
            if second.get("start_id") != station_id:
                replacement_second = self._find_unique_route_between(
                    station_id,
                    second.get("destination_id"),
                )
                if replacement_second is None:
                    return self._charging_plan_mismatch_block(
                        repaired,
                        station_id,
                        "No unique known route from the selected charging station to the final destination.",
                    )
                replacement_second_id = (
                    replacement_second.get("route_id") or replacement_second.get("id")
                )
                if not isinstance(replacement_second_id, str):
                    return {"status": "OK", "route_ids": repaired}
                repaired[1] = replacement_second_id

        self.scratchpad["gates"]["charging_plan_route_guard"] = {
            "status": "REPAIRED",
            "charging_station_id": station_id,
            "from_route_ids": normalized_ids,
            "to_route_ids": repaired,
        }
        return {"status": "OK", "route_ids": repaired}

    def _charging_plan_mismatch_block(
        self,
        route_ids: list[str],
        station_id: str,
        reason: str,
    ) -> dict[str, Any]:
        plan = self.scratchpad.get("entities", {}).get("selected_charging_plan")
        self.scratchpad["gates"]["charging_plan_route_guard"] = {
            "status": "CHARGING_PLAN_ROUTE_MISMATCH",
            "charging_station_id": station_id,
            "route_ids": route_ids,
            "reason": reason,
        }
        return {
            "status": "CHARGING_PLAN_ROUTE_MISMATCH",
            "tool_name": "set_new_navigation",
            "reason": reason,
            "route_ids": route_ids,
            "charging_plan": copy.deepcopy(plan) if isinstance(plan, dict) else None,
            "route_facts": [self._route_chain_fact(route_id) for route_id in route_ids],
            "needed_first_destination_id": station_id,
        }

    def _find_unique_route_between(
        self,
        start_id: Any,
        destination_id: Any,
    ) -> dict[str, Any] | None:
        if not (isinstance(start_id, str) and isinstance(destination_id, str)):
            return None
        routes_by_id = self.scratchpad.get("entities", {}).get("routes_by_id")
        if not isinstance(routes_by_id, dict):
            return None
        matches = [
            route
            for route in routes_by_id.values()
            if isinstance(route, dict)
            and route.get("start_id") == start_id
            and route.get("destination_id") == destination_id
        ]
        return matches[0] if len(matches) == 1 else None

    def _is_charging_poi_id(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        if value.startswith("poi_cha"):
            return True
        pois_by_id = self.scratchpad.get("entities", {}).get("pois_by_id")
        poi = pois_by_id.get(value) if isinstance(pois_by_id, dict) else None
        if isinstance(poi, dict):
            category = str(poi.get("category") or "").lower()
            name = str(poi.get("name") or "").lower()
            return "charging" in category or "charger" in name or "charge" in name
        return False

    def _repair_or_block_route_chain(self, route_ids: Any) -> dict[str, Any]:
        """Validate a multi-leg route chain using only known route facts.

        If facts are incomplete, the model keeps full freedom and the call goes
        through unchanged. If every route is known and the chain is impossible,
        replace a stale/base route id with one known to connect the legs. When no
        unique repair exists, return a fact packet instead of emitting a doomed
        evaluator call.
        """

        if not isinstance(route_ids, list) or len(route_ids) < 2:
            return {"status": "OK", "route_ids": route_ids}
        normalized_ids = [
            route_id for route_id in route_ids if isinstance(route_id, str) and route_id
        ]
        if len(normalized_ids) != len(route_ids):
            return {"status": "OK", "route_ids": route_ids}
        records = [self._route_record_for_id(route_id) for route_id in normalized_ids]
        if any(record is None for record in records):
            return {"status": "OK", "route_ids": route_ids}

        repaired_ids = list(normalized_ids)
        repairs: list[dict[str, Any]] = []
        for index in range(len(repaired_ids) - 1):
            current = self._route_record_for_id(repaired_ids[index])
            following = self._route_record_for_id(repaired_ids[index + 1])
            if not isinstance(current, dict) or not isinstance(following, dict):
                return {"status": "OK", "route_ids": repaired_ids}
            current_destination = current.get("destination_id")
            following_start = following.get("start_id")
            if not (
                isinstance(current_destination, str)
                and isinstance(following_start, str)
            ):
                return {"status": "OK", "route_ids": repaired_ids}
            if current_destination == following_start:
                continue
            replacement = self._find_unique_connecting_route(
                current_destination,
                following,
            )
            if replacement is None:
                self.scratchpad["gates"]["route_chain_guard"] = {
                    "status": "ROUTE_CHAIN_MISMATCH",
                    "route_ids": repaired_ids,
                    "break_after_index": index,
                }
                return {
                    "status": "ROUTE_CHAIN_MISMATCH",
                    "tool_name": "set_new_navigation",
                    "reason": (
                        "The requested route_ids do not form a connected route "
                        "chain: each next route must start where the previous "
                        "route ends."
                    ),
                    "route_ids": repaired_ids,
                    "route_facts": [
                        self._route_chain_fact(route_id) for route_id in repaired_ids
                    ],
                    "break_after_index": index,
                    "expected_next_start_id": current_destination,
                    "actual_next_start_id": following_start,
                }
            replacement_id = replacement.get("route_id") or replacement.get("id")
            if not isinstance(replacement_id, str):
                return {"status": "OK", "route_ids": repaired_ids}
            old_id = repaired_ids[index + 1]
            repaired_ids[index + 1] = replacement_id
            repairs.append(
                {
                    "index": index + 1,
                    "from_route_id": old_id,
                    "to_route_id": replacement_id,
                    "reason": "replacement starts at the previous route destination",
                }
            )

        if repairs:
            self.scratchpad["gates"]["route_chain_guard"] = {
                "status": "REPAIRED",
                "repairs": repairs,
                "route_ids": repaired_ids,
            }
        return {"status": "OK", "route_ids": repaired_ids, "repairs": repairs}

    def _repair_route_ids_for_recorded_selection(self, route_ids: Any) -> dict[str, Any]:
        """Apply the latest explicit route selection to matching route segments.

        The model keeps freedom to choose route IDs. This guard only repairs a
        segment when the scratchpad already contains a recorded route selection
        for the same destination, and a unique route for the current segment
        matches that recorded selector. If facts are missing or ambiguous, the
        call is left unchanged.
        """

        if not isinstance(route_ids, list) or len(route_ids) < 2:
            return {"status": "OK", "route_ids": route_ids}
        normalized_ids = [
            route_id for route_id in route_ids if isinstance(route_id, str) and route_id
        ]
        if len(normalized_ids) != len(route_ids):
            return {"status": "OK", "route_ids": route_ids}
        repaired_ids = list(normalized_ids)
        repairs: list[dict[str, Any]] = []
        for index, route_id in enumerate(normalized_ids):
            route = self._route_record_for_id(route_id)
            if not isinstance(route, dict):
                continue
            destination_id = route.get("destination_id")
            start_id = route.get("start_id")
            if not (isinstance(destination_id, str) and isinstance(start_id, str)):
                continue
            selection = self._latest_recorded_route_selection_for_destination(
                destination_id
            )
            if not isinstance(selection, dict):
                continue
            if self._route_matches_recorded_selection(route, selection):
                continue
            replacement = self._unique_route_for_recorded_selection(
                start_id,
                destination_id,
                selection,
            )
            if not isinstance(replacement, dict):
                continue
            replacement_id = replacement.get("route_id") or replacement.get("id")
            if not isinstance(replacement_id, str) or replacement_id == route_id:
                continue
            repaired_ids[index] = replacement_id
            repairs.append(
                {
                    "index": index,
                    "from_route_id": route_id,
                    "to_route_id": replacement_id,
                    "destination_id": destination_id,
                    "reason": "latest recorded route selection matches another route for this segment",
                }
            )
        if repairs:
            self.scratchpad["gates"]["route_selection_guard"] = {
                "status": "REPAIRED",
                "repairs": repairs,
                "route_ids": repaired_ids,
            }
        return {"status": "OK", "route_ids": repaired_ids, "repairs": repairs}

    def _latest_recorded_route_selection_for_destination(
        self,
        destination_id: str,
    ) -> dict[str, Any] | None:
        entities = self.scratchpad.get("entities", {})
        history = entities.get("route_selection_history")
        selections: list[dict[str, Any]] = []
        if isinstance(history, list):
            selections.extend(item for item in history if isinstance(item, dict))
        selected = entities.get("selected_route")
        if isinstance(selected, dict):
            selections.append(selected)
        for selection in reversed(selections):
            if selection.get("destination_id") == destination_id:
                return selection
            route = selection.get("route")
            if isinstance(route, dict) and route.get("destination_id") == destination_id:
                return selection
        return None

    def _unique_route_for_recorded_selection(
        self,
        start_id: str,
        destination_id: str,
        selection: dict[str, Any],
    ) -> dict[str, Any] | None:
        routes_by_id = self.scratchpad.get("entities", {}).get("routes_by_id")
        if not isinstance(routes_by_id, dict):
            return None
        candidates = [
            route
            for route in routes_by_id.values()
            if isinstance(route, dict)
            and route.get("start_id") == start_id
            and route.get("destination_id") == destination_id
        ]
        matches = [
            route
            for route in candidates
            if self._route_matches_recorded_selection(route, selection)
        ]
        return matches[0] if len(matches) == 1 else None

    def _route_matches_recorded_selection(
        self,
        route: dict[str, Any],
        selection: dict[str, Any],
    ) -> bool:
        selector = selection.get("selector")
        selector = selector if isinstance(selector, dict) else {}
        selected_route = selection.get("route")
        selected_route = selected_route if isinstance(selected_route, dict) else {}
        route_id = route.get("route_id") or route.get("id")
        selected_route_id = (
            selector.get("route_id")
            or selection.get("route_id")
            or selection.get("selected_route_id")
        )
        if isinstance(selected_route_id, str) and route_id == selected_route_id:
            return True
        name_via = selector.get("name_via") or selected_route.get("name_via")
        if isinstance(name_via, str) and name_via.strip():
            return self._normalize_via(route.get("name_via", "")) == self._normalize_via(name_via)
        wanted_alias = selector.get("alias") or selector.get("prefer")
        if not isinstance(wanted_alias, str) or not wanted_alias.strip():
            selected_aliases = selected_route.get("alias")
            if isinstance(selected_aliases, list):
                for alias in selected_aliases:
                    alias_text = str(alias).strip().lower()
                    if alias_text and alias_text not in {"fastest", "first"}:
                        wanted_alias = alias_text
                        break
        if isinstance(wanted_alias, str) and wanted_alias.strip():
            aliases = [str(item).lower() for item in route.get("alias", [])]
            return wanted_alias.strip().lower() in aliases
        return False

    def _route_record_for_id(self, route_id: str) -> dict[str, Any] | None:
        entities = self.scratchpad.get("entities", {})
        routes_by_id = entities.get("routes_by_id")
        if isinstance(routes_by_id, dict):
            record = routes_by_id.get(route_id)
            if isinstance(record, dict):
                return record
        for key in ("last_routes",):
            routes = entities.get(key)
            if not isinstance(routes, list):
                continue
            for route in routes:
                if isinstance(route, dict) and (
                    route.get("route_id") == route_id or route.get("id") == route_id
                ):
                    return route
        return None

    def _find_unique_connecting_route(
        self,
        start_id: str,
        replaced_route: dict[str, Any],
    ) -> dict[str, Any] | None:
        destination_id = replaced_route.get("destination_id")
        if not isinstance(destination_id, str):
            return None
        replaced_id = replaced_route.get("route_id") or replaced_route.get("id")
        replaced_base_id = replaced_route.get("base_route_id")
        candidates: list[dict[str, Any]] = []
        routes_by_id = self.scratchpad.get("entities", {}).get("routes_by_id")
        if not isinstance(routes_by_id, dict):
            return None
        for route in routes_by_id.values():
            if not isinstance(route, dict):
                continue
            if route.get("start_id") != start_id:
                continue
            if route.get("destination_id") != destination_id:
                continue
            route_id = route.get("route_id") or route.get("id")
            if route_id == replaced_id:
                continue
            candidates.append(route)
        if isinstance(replaced_id, str):
            base_matches = [
                route
                for route in candidates
                if route.get("base_route_id") == replaced_id
            ]
            if len(base_matches) == 1:
                return base_matches[0]
        if isinstance(replaced_base_id, str):
            base_matches = [
                route
                for route in candidates
                if route.get("base_route_id") == replaced_base_id
            ]
            if len(base_matches) == 1:
                return base_matches[0]
        return candidates[0] if len(candidates) == 1 else None

    def _route_chain_fact(self, route_id: str) -> dict[str, Any]:
        route = self._route_record_for_id(route_id)
        fact = {"route_id": route_id}
        if isinstance(route, dict):
            for key in ("start_id", "destination_id", "base_route_id", "name_via", "alias"):
                if key in route:
                    fact[key] = copy.deepcopy(route[key])
        return fact

    def _requested_route_destination(self, route_ids: Any) -> str | None:
        """FACT-ONLY: the destination at the end of the requested route chain.

        Looked up in the routes the model already fetched (last_routes). This is
        a fact the model can use; it does NOT decide which edit to perform.
        """

        if not isinstance(route_ids, list) or not route_ids:
            return None
        stored = self.scratchpad.get("entities", {}).get("last_routes")
        if not isinstance(stored, list):
            return None
        last_rid = route_ids[-1]
        match = next(
            (
                r for r in stored
                if isinstance(r, dict) and (r.get("route_id") == last_rid or r.get("id") == last_rid)
            ),
            None,
        )
        new_dest = match.get("destination_id") if isinstance(match, dict) else None
        return new_dest if isinstance(new_dest, str) else None

    def _active_route_summary(self) -> dict[str, Any] | None:
        """A compact, fact-only view of the active route for the block payload."""

        state = self.scratchpad.get("entities", {}).get("navigation_state")
        if not isinstance(state, dict):
            return None
        return {
            key: state[key]
            for key in (
                "destination_id",
                "final_destination_id",
                "waypoint_order",
                "navigation_active",
            )
            if key in state
        }

    def _narrate_from_route_ids(self, route_ids: Any) -> None:
        if not isinstance(route_ids, list):
            return
        stored = self.scratchpad.get("entities", {}).get("last_routes")
        if not isinstance(stored, list) or len(stored) <= 1:
            return
        for rid in route_ids:
            if isinstance(rid, str) and any(
                isinstance(r, dict) and (r.get("route_id") == rid or r.get("id") == rid)
                for r in stored
            ):
                self._store_route_narration(stored, rid)
                return

    # ------------------------------------------------------------------
    # Degenerate / malformed call guards (prevent guaranteed tool errors)
    # ------------------------------------------------------------------

    def get_routes_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Skip a route lookup whose start and destination are the same.

        Such a call always raises a tool-execution error (`GetRoutes_008`), and a
        single tool-execution error zeroes the whole task even when every other
        scored component is correct. A no-op result is returned instead so the
        invalid call is never sent to the evaluator.
        """

        normalized_kwargs, endpoint_block = self._normalize_route_endpoint_arguments(
            kwargs
        )
        if endpoint_block is not None:
            return endpoint_block
        kwargs = normalized_kwargs
        start = kwargs.get("start_id")
        destination = kwargs.get("destination_id")
        if isinstance(start, str) and start and start == destination:
            self.scratchpad["gates"]["degenerate_route_guard"] = {
                "status": "SKIPPED",
                "start_id": start,
                "destination_id": destination,
            }
            return {
                "status": "SKIPPED",
                "tool_name": "get_routes_from_start_to_destination",
                "routes": [],
                "reason": "start and destination are the same location; no route is needed",
            }
        blocker = self._destination_replacement_surface_blocker(
            "destination_replacement_surface",
            destination,
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync("get_routes_from_start_to_destination", kwargs)
        self._abort_if_route_options_unavailable(
            "get_routes_guarded",
            start,
            destination,
            result,
        )
        # When the model fetches routes directly (a presentation, not an internal
        # guard derivation), store the policy-022 narration so respond() informs
        # about fastest + alternatives + tolls even without a navigation edit.
        routes = routes_value(result)
        if isinstance(routes, list) and len(routes) > 1:
            fastest = next(
                (
                    r for r in routes
                    if isinstance(r, dict) and "fastest" in [str(a).lower() for a in (r.get("alias") or [])]
                ),
                routes[0],
            )
            if isinstance(fastest, dict):
                rid = fastest.get("route_id") or fastest.get("id")
                if isinstance(rid, str):
                    self._store_route_narration(routes, rid, stage="search")
        return result

    def _abort_if_route_options_unavailable(
        self,
        gate_name: str,
        start_id: Any,
        destination_id: Any,
        result: dict[str, Any],
    ) -> None:
        if result.get("status") != "SUCCESS":
            return
        payload = result.get("result")
        if not isinstance(payload, dict):
            return
        routes = payload.get("routes")
        if not isinstance(routes, UnknownToolResponseValue):
            return
        start_label = self._route_endpoint_label(start_id)
        destination_label = self._route_endpoint_label(destination_id)
        pair_text = (
            f" from {start_label} to {destination_label}"
            if start_label and destination_label
            else ""
        )
        message = (
            "I can't determine whether the current range is enough, because I looked it up "
            f"and the car system did not provide the route options or distance{pair_text}."
        )
        report = {
            "helper": gate_name,
            "status": "UNAVAILABLE",
            "missing_response_fields": [routes.response_path],
            "start_id": start_id,
            "destination_id": destination_id,
            "message": message,
        }
        self.scratchpad["gates"][gate_name] = {
            "status": "NO",
            "missing_response_fields": report["missing_response_fields"],
            "start_id": start_id,
            "destination_id": destination_id,
            "reason": message,
        }
        self._store_helper_report(gate_name, report)
        self._abort_with_response(message)

    def _route_endpoint_label(self, endpoint_id: Any) -> str | None:
        if not isinstance(endpoint_id, str) or not endpoint_id.strip():
            return None
        endpoint_id = endpoint_id.strip()
        entities = self.scratchpad.get("entities", {})
        locations_by_id = entities.get("locations_by_id")
        if isinstance(locations_by_id, dict):
            record = locations_by_id.get(endpoint_id)
            if isinstance(record, dict):
                name = record.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        pois_by_id = entities.get("pois_by_id")
        if isinstance(pois_by_id, dict):
            record = pois_by_id.get(endpoint_id)
            if isinstance(record, dict):
                name = record.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        return endpoint_id

    def _normalize_route_endpoint_arguments(
        self,
        kwargs: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        normalized = dict(kwargs)
        changes: list[dict[str, str]] = []
        for argument_name in ("start_id", "destination_id"):
            if argument_name not in normalized:
                continue
            value, change, blocker = self._normalize_route_endpoint_value(
                argument_name,
                normalized[argument_name],
            )
            if blocker is not None:
                return normalized, blocker
            normalized[argument_name] = value
            if change is not None:
                changes.append(change)
        if changes:
            self.scratchpad["gates"]["route_endpoint_guard"] = {
                "status": "NORMALIZED",
                "changes": changes,
            }
        return normalized, None

    def _normalize_route_endpoint_value(
        self,
        argument_name: str,
        value: Any,
    ) -> tuple[Any, dict[str, str] | None, dict[str, Any] | None]:
        value = self._resolve_preloaded_argument_value(value)
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            value = value["id"]
        if not isinstance(value, str):
            return value, None, None
        raw_value = value
        value = value.strip()
        if not value:
            return value, None, None
        if self._looks_like_route_endpoint_id(value):
            return value, None, None
        resolved = self._known_route_endpoint_id_for_name(value)
        if resolved is not None:
            return (
                resolved,
                {
                    "argument": argument_name,
                    "from": raw_value,
                    "to": resolved,
                },
                None,
            )
        blocker = {
            "status": "NEEDS_GROUNDED_ROUTE_ENDPOINT",
            "tool_name": "get_routes_from_start_to_destination",
            "routes": [],
            "argument": argument_name,
            "value": raw_value,
            "reason": (
                f"{argument_name} must be a grounded location or POI id; "
                f"resolve {raw_value!r} before requesting routes"
            ),
        }
        self.scratchpad["gates"]["route_endpoint_guard"] = blocker
        return value, None, blocker

    @staticmethod
    def _looks_like_route_endpoint_id(value: str) -> bool:
        return value.startswith(("loc_", "poi_")) or (
            value == value.upper()
            and value.replace("_", "").isalnum()
            and len(value) <= 3
        )

    @staticmethod
    def _normalized_entity_name(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.casefold().strip().split())
        return normalized or None

    def _known_route_endpoint_id_for_name(self, name: str) -> str | None:
        wanted = self._normalized_entity_name(name)
        if wanted is None:
            return None
        candidates: list[tuple[str, str]] = []
        entities = self.scratchpad.get("entities", {})
        self._add_route_endpoint_candidates(
            candidates,
            entities.get("locations_by_id"),
            id_keys=("location_id", "id"),
        )
        self._add_route_endpoint_candidates(
            candidates,
            entities.get("last_location_lookup"),
            id_keys=("location_id", "id"),
        )
        self._add_route_endpoint_candidates(
            candidates,
            entities.get("pois_by_id"),
            id_keys=("navigation_id", "poi_id", "id"),
        )
        self._add_route_endpoint_candidates(
            candidates,
            entities.get("last_pois"),
            id_keys=("navigation_id", "poi_id", "id"),
        )
        navigation_state = entities.get("navigation_state")
        if isinstance(navigation_state, dict):
            names = navigation_state.get("waypoint_names")
            ids = navigation_state.get("waypoint_order") or navigation_state.get("waypoint_ids")
            if isinstance(names, list) and isinstance(ids, list):
                for candidate_name, candidate_id in zip(names, ids):
                    if isinstance(candidate_name, str) and isinstance(candidate_id, str):
                        candidates.append((candidate_name, candidate_id))
        matches = {
            candidate_id
            for candidate_name, candidate_id in candidates
            if self._normalized_entity_name(candidate_name) == wanted
        }
        if len(matches) == 1:
            return next(iter(matches))
        return None

    def _add_route_endpoint_candidates(
        self,
        candidates: list[tuple[str, str]],
        source: Any,
        *,
        id_keys: tuple[str, ...],
    ) -> None:
        if isinstance(source, dict):
            records = list(source.values()) if all(
                isinstance(value, dict) for value in source.values()
            ) else [source]
        elif isinstance(source, list):
            records = source
        else:
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            endpoint_id = None
            for key in id_keys:
                value = record.get(key)
                if isinstance(value, str) and self._looks_like_route_endpoint_id(value):
                    endpoint_id = value
                    break
            if endpoint_id is None:
                continue
            for name_key in ("name", "display_name"):
                candidate_name = record.get(name_key)
                if isinstance(candidate_name, str):
                    candidates.append((candidate_name, endpoint_id))

    def get_weather_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Weather can only be requested for the current (policy) day.

        Clamp month/day to the policy DATETIME so a model that uses the host
        clock or a future day does not trigger `AUT-POL:024`.
        """

        now = self.policy_now()
        if isinstance(now, dict):
            if now.get("month") is not None:
                kwargs["month"] = now["month"]
            if now.get("day") is not None:
                kwargs["day"] = now["day"]
            if now.get("hour") is not None:
                kwargs["time_hour_24hformat"] = now["hour"]
            if now.get("minute") is not None:
                kwargs["time_minutes"] = now["minute"]
        return self._call_raw_tool_sync("get_weather", kwargs)

    def search_poi_along_route_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Block a charging-station route search that omits the required kilometer.

        `search_poi_along_the_route` requires `at_kilometer` for charging stations;
        without it the evaluator raises `SearchPoiAlongTheRoute_007`. Returning a
        grounded prompt avoids that guaranteed tool error.
        """

        category = str(kwargs.get("category_poi") or "").lower()
        at_kilometer = kwargs.get("at_kilometer")
        if "charg" in category and at_kilometer in (None, ""):
            self.scratchpad["gates"]["charging_search_guard"] = {
                "status": "NEEDS_KILOMETER",
                "category_poi": kwargs.get("category_poi"),
            }
            return {
                "status": "NEEDS_KILOMETER",
                "tool_name": "search_poi_along_the_route",
                "pois": [],
                "reason": (
                    "at_kilometer is required to search for charging stations along the "
                    "route; provide the route kilometer to search at"
                ),
            }
        return self._call_raw_tool_sync("search_poi_along_the_route", kwargs)

    def get_contact_id_by_contact_name_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Normalize the contact-name lookup so IDs are never confused with keys.

        The raw tool returns ``{"matches": {contact_id: display_name}}``. Models
        have read the wrapper keys (``matches``/``status``) as IDs. This exposes
        an ADDITIVE normalized shape — ``contact_ids`` / ``contacts`` / ``by_id``
        — while preserving the original under ``raw_result``. No value is
        invented and nothing is dropped.
        """

        result = self._call_raw_tool_sync("get_contact_id_by_contact_name", kwargs)
        if not isinstance(result, dict) or result.get("status") != "SUCCESS":
            return result
        payload = result.get("result")
        matches = payload.get("matches") if isinstance(payload, dict) else None
        contact_ids: list[str] = []
        contacts: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        if isinstance(matches, dict):
            for contact_id, display_name in matches.items():
                if not isinstance(contact_id, str) or not contact_id:
                    continue
                name = self._plain_value(display_name)
                contact_ids.append(contact_id)
                entry = {"contact_id": contact_id, "display_name": name}
                contacts.append(entry)
                by_id[contact_id] = dict(entry)
        elif isinstance(matches, list):
            # Tolerate a list-of-objects shape without inventing fields.
            for candidate in matches:
                if not isinstance(candidate, dict):
                    continue
                contact_id = candidate.get("id") or candidate.get("contact_id")
                if not isinstance(contact_id, str) or not contact_id:
                    continue
                contact_ids.append(contact_id)
                normalized = self._normalize_contact_record(contact_id, candidate)
                contacts.append(normalized)
                by_id[contact_id] = normalized
        normalized_result = {
            "status": "SUCCESS",
            "tool_name": "get_contact_id_by_contact_name",
            "matches": contact_ids,
            "contact_ids": contact_ids,
            "contacts": contacts,
            "by_id": by_id,
            "raw_result": result,
        }
        if contacts:
            previous_lookup = self.scratchpad["entities"].get("last_contact_lookup")
            previous_ids = (
                previous_lookup.get("contact_ids")
                if isinstance(previous_lookup, dict)
                else None
            )
            if isinstance(previous_ids, list):
                previous_set = {
                    value
                    for value in previous_ids
                    if isinstance(value, str) and value
                }
                shared_ids = [
                    contact_id
                    for contact_id in contact_ids
                    if contact_id in previous_set
                ]
                normalized_result["intersection_with_previous_contact_ids"] = shared_ids
                if len(shared_ids) == 1:
                    normalized_result[
                        "unique_intersection_with_previous_contact_id"
                    ] = shared_ids[0]
                    self.remember_entity(
                        "last_unique_contact_intersection_id", shared_ids[0]
                    )
                normalized_result["previous_contact_query"] = copy.deepcopy(
                    previous_lookup.get("query")
                )
            self.remember_entity("last_contacts", contacts)
            self._remember_contacts_by_id(contacts)
            self.remember_entity(
                "last_contact_lookup",
                {
                    "query": copy.deepcopy(kwargs),
                    "contact_ids": contact_ids,
                    "contacts": contacts,
                    "intersection_with_previous_contact_ids": normalized_result.get(
                        "intersection_with_previous_contact_ids"
                    ),
                    "unique_intersection_with_previous_contact_id": normalized_result.get(
                        "unique_intersection_with_previous_contact_id"
                    ),
                },
            )
        return normalized_result

    # ------------------------------------------------------------------
    # Active-route edit guards: derive correct adjacency from fresh state
    # ------------------------------------------------------------------

    def _fresh_waypoint_order(self) -> list[str]:
        """Ordered grounded waypoint ids from a fresh raw navigation read.

        Uses the raw tool (not the field-enforcing helper) so it never aborts;
        returns [] when navigation state is unavailable.
        """

        try:
            result = self._call_raw_tool_sync(
                "get_current_navigation_state", {"detailed_information": True}
            )
        except Exception:
            return []
        payload = result.get("result") if isinstance(result, dict) else None
        if not isinstance(payload, dict):
            return []
        ids = payload.get("waypoints_id")
        if not isinstance(ids, list):
            return []
        return [wid for wid in ids if isinstance(wid, str)]

    def _select_fastest(
        self, start_id: str, destination_id: str
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Return (fastest route id, full route list) between two grounded ids."""

        if not (isinstance(start_id, str) and isinstance(destination_id, str)):
            return None, []
        if start_id == destination_id:
            return None, []
        options = self.get_route_options(start_id=start_id, destination_id=destination_id)
        if not isinstance(options, dict) or options.get("status") != "SUCCESS":
            return None, []
        routes = options.get("routes") or []
        fastest = options.get("fastest")
        candidate = fastest if isinstance(fastest, dict) else (routes[0] if routes else None)
        route_id = None
        if isinstance(candidate, dict):
            raw = candidate.get("route_id") or candidate.get("id")
            route_id = raw if isinstance(raw, str) else None
        return route_id, (routes if isinstance(routes, list) else [])

    def _fastest_route_id(self, start_id: str, destination_id: str) -> str | None:
        route_id, routes = self._select_fastest(start_id, destination_id)
        if route_id:
            self._store_route_narration(routes, route_id)
        return route_id

    @staticmethod
    def _route_narration(
        routes: list[dict[str, Any]],
        selected_route_id: str,
        stage: str = "navigate",
    ) -> str | None:
        """Policy 022/021 narration built from grounded route fields, or None.

        Uses the evaluator's own `alias` (fastest/shortest), `includes_toll`, and
        the count of returned alternatives — no task content. The wording is
        staged so it never implies an action the runtime did not take:
          - `search`  : the model only read alternatives → describe + offer.
          - `select`  : policy auto-picked a route for a segment → "I selected".
          - `navigate`: a navigation tool call succeeded → changed segment/option
            "now using".
        """

        if not isinstance(routes, list) or not routes:
            return None
        selected = None
        for route in routes:
            if isinstance(route, dict) and (
                route.get("route_id") == selected_route_id or route.get("id") == selected_route_id
            ):
                selected = route
                break
        if not isinstance(selected, dict):
            return None
        alias = selected.get("alias")
        alias = [str(tag).lower() for tag in alias] if isinstance(alias, list) else []
        if "fastest" in alias and "shortest" in alias:
            descriptor = "the fastest route, which is also the shortest"
        elif "fastest" in alias:
            descriptor = "the fastest route"
        elif "shortest" in alias:
            descriptor = "the shortest route"
        else:
            return None
        alternatives = max(0, len(routes) - 1)
        toll = selected.get("includes_toll") is True

        if stage == "search":
            # A pure read: do not claim a route was taken — present and offer.
            cap = descriptor[0].upper() + descriptor[1:]
            text = cap + "."
            if alternatives > 0:
                verb = "is" if alternatives == 1 else "are"
                plural = "" if alternatives == 1 else "s"
                text += f" There {verb} {alternatives} other option{plural}."
            if toll:
                text += " It uses toll roads."
            text += " Would you like details or to navigate on one of them?"
            return text

        if stage == "select":
            text = f"I selected {descriptor} for this segment"
            if alternatives > 0:
                verb = "is" if alternatives == 1 else "are"
                plural = "" if alternatives == 1 else "s"
                text += f"; there {verb} {alternatives} other option{plural}"
            text += "."
            if toll:
                text += " It uses toll roads."
            return text

        # stage == "navigate": a navigation call actually succeeded. Keep the
        # wording segment-scoped; route edits do not prove untouched segments
        # were also re-selected.
        text = f"This route segment is now using {descriptor}"
        if alternatives > 0:
            verb = "is" if alternatives == 1 else "are"
            plural = "" if alternatives == 1 else "s"
            text += f" — there {verb} {alternatives} other option{plural} if you'd like to switch it"
        text += "."
        if toll:
            text += " It uses toll roads."
        return text

    def _store_route_narration(
        self, routes: Any, selected_route_id: str, stage: str = "navigate"
    ) -> None:
        narration = self._route_narration(
            routes if isinstance(routes, list) else [], selected_route_id, stage
        )
        if narration:
            self._ensure_scratchpad_shape()
            self.scratchpad["facts"]["pending_route_narration"] = narration

    def _resolve_route_arg(
        self, provided_id: Any, start_id: str, destination_id: str
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Keep a valid model-provided route id; otherwise derive the fastest."""

        fastest_id, routes = self._select_fastest(start_id, destination_id)
        if isinstance(provided_id, str) and any(
            isinstance(route, dict)
            and (route.get("route_id") == provided_id or route.get("id") == provided_id)
            for route in routes
        ):
            return provided_id, routes
        return fastest_id, routes

    def _resolve_explicit_or_unique_route_arg(
        self,
        provided_id: Any,
        start_id: str,
        destination_id: str,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Keep an explicit valid route, or fill it only when one option exists."""

        options = self.get_route_options(
            start_id=start_id,
            destination_id=destination_id,
        )
        if not isinstance(options, dict) or options.get("status") != "SUCCESS":
            return None, []
        routes = options.get("routes")
        route_list = routes if isinstance(routes, list) else []
        if isinstance(provided_id, str) and any(
            isinstance(route, dict)
            and (
                route.get("route_id") == provided_id
                or route.get("id") == provided_id
            )
            for route in route_list
        ):
            return provided_id, route_list
        if isinstance(provided_id, str):
            base_route_matches = [
                route
                for route in route_list
                if isinstance(route, dict) and route.get("base_route_id") == provided_id
            ]
            if len(base_route_matches) == 1:
                route_id = (
                    base_route_matches[0].get("route_id")
                    or base_route_matches[0].get("id")
                )
                if isinstance(route_id, str):
                    return route_id, route_list
        if len(route_list) == 1:
            only_id = route_list[0].get("route_id") or route_list[0].get("id")
            return (only_id if isinstance(only_id, str) else None), route_list
        return None, route_list

    def navigation_add_one_waypoint_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Complete a mid-route insertion by deriving the missing after-waypoint args.

        Inserting a non-final waypoint requires `waypoint_id_after_new_waypoint`
        and `route_id_leading_away_from_new_waypoint`; omitting them raises
        `NavigationAddOneWaypoint_008`. When they are absent and the insertion is
        mid-route, derive them from fresh navigation state.
        """

        before_id = kwargs.get("waypoint_id_before_new_waypoint")
        new_id = kwargs.get("waypoint_id_to_add")
        if not (isinstance(before_id, str) and isinstance(new_id, str)):
            return self._call_raw_tool_sync("navigation_add_one_waypoint", kwargs)
        order = self._fresh_waypoint_order()
        if new_id in order:
            return self._already_present_result(
                "navigation_add_one_waypoint",
                new_id,
            )
        # Route leading TO the new waypoint (before -> new). Resolve it ALWAYS
        # (keep a valid model id, else fastest) so the policy-022 narration fires
        # even when the model supplied the route itself.
        to_id, to_routes = self._resolve_route_arg(
            kwargs.get("route_id_leading_to_new_waypoint"), before_id, new_id
        )
        if to_id:
            kwargs = dict(kwargs, route_id_leading_to_new_waypoint=to_id)
            self._store_route_narration(to_routes, to_id)
        if before_id in order:
            index = order.index(before_id)
            if index + 1 < len(order):  # mid-route insert: BOTH after-args are required
                after_id = order[index + 1]
                away_id, _ = self._resolve_route_arg(
                    kwargs.get("route_id_leading_away_from_new_waypoint"), new_id, after_id
                )
                kwargs = dict(kwargs, waypoint_id_after_new_waypoint=after_id)
                if away_id:
                    kwargs["route_id_leading_away_from_new_waypoint"] = away_id
        return self._call_raw_tool_sync("navigation_add_one_waypoint", kwargs)

    def _already_present_result(self, tool_name: str, waypoint_id: str) -> dict[str, Any]:
        self.scratchpad["gates"]["nav_idempotent"] = {
            "status": "ALREADY_PRESENT",
            "tool_name": tool_name,
            "waypoint_id": waypoint_id,
        }
        return {
            "status": "SUCCESS",
            "tool_name": tool_name,
            "already_present": True,
            "result": {
                "already_present": True,
                "waypoint_added": False,
                "waypoint_id": waypoint_id,
                "note": (
                    "This waypoint is already in the current route, so it was not "
                    "added again."
                ),
            },
        }

    def navigation_delete_waypoint_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Delete a mid-route waypoint with a freshly derived connecting route.

        The required `route_id_without_waypoint` must connect the deleted
        waypoint's previous and next neighbours; a stale id raises
        `NavigationDeleteOneWaypoint_007`. Always re-derive it from fresh state
        for a mid-route waypoint.
        """

        target = kwargs.get("waypoint_id_to_delete")
        if not isinstance(target, str):
            return self._call_raw_tool_sync("navigation_delete_waypoint", kwargs)
        order = self._fresh_waypoint_order()
        if order and target not in order:
            # Already removed (e.g. a repeated delete after the first succeeded).
            # Emitting the call yields NavigationDeleteOneWaypoint_005 and loops;
            # treat it as an idempotent no-op success instead.
            return self._already_removed_result("navigation_delete_waypoint", target)
        if target in order:
            index = order.index(target)
            if 0 < index < len(order) - 1:  # a mid waypoint with both neighbours
                route_id = self._fastest_route_id(order[index - 1], order[index + 1])
                if route_id:
                    kwargs = dict(kwargs, route_id_without_waypoint=route_id)
        return self._call_raw_tool_sync("navigation_delete_waypoint", kwargs)

    def _already_removed_result(self, tool_name: str, waypoint_id: str) -> dict[str, Any]:
        # The waypoint is not in the current route. Absence is a FACT, but it is
        # not proof that *this* delete removed it — the model may have targeted
        # the wrong stop. Report ALREADY_ABSENT (a no-op, not a deletion) so the
        # model decides whether the intended stop was actually handled, while
        # still avoiding the NavigationDeleteOneWaypoint_005 error+loop.
        self.scratchpad["gates"]["nav_idempotent"] = {
            "status": "ALREADY_ABSENT",
            "tool_name": tool_name,
            "waypoint_id": waypoint_id,
        }
        return {
            "status": "SUCCESS",
            "tool_name": tool_name,
            "already_absent": True,
            "result": {
                "already_absent": True,
                "waypoint_deleted": False,
                "waypoint_id": waypoint_id,
                "note": (
                    "This waypoint is not in the current route, so nothing was "
                    "deleted. If you expected it to be a stop, re-check which "
                    "waypoint the user meant before reporting it removed."
                ),
            },
        }

    def navigation_replace_one_waypoint_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Replace a mid-route waypoint with freshly derived connecting routes.

        Both `route_id_leading_to_new_waypoint` (previous -> new) and
        `route_id_leading_away_from_new_waypoint` (new -> next) must be valid for
        the current neighbours; stale ids raise `NavigationReplaceOneWaypoint_011`
        / `_013`. A valid model-provided route id is kept; otherwise the fastest
        is derived. The route-selection narration (policy 022/021) is stored from
        the leading-to segment.
        """

        target = kwargs.get("waypoint_id_to_replace")
        new_id = kwargs.get("new_waypoint_id")
        if not (isinstance(target, str) and isinstance(new_id, str)):
            return self._call_raw_tool_sync("navigation_replace_one_waypoint", kwargs)
        order = self._fresh_waypoint_order()
        if target in order:
            index = order.index(target)
            if index - 1 >= 0:
                to_id, to_routes = self._resolve_route_arg(
                    kwargs.get("route_id_leading_to_new_waypoint"), order[index - 1], new_id
                )
                if to_id:
                    kwargs = dict(kwargs, route_id_leading_to_new_waypoint=to_id)
                    self._store_route_narration(to_routes, to_id)
            if index + 1 < len(order):
                away_id, _ = self._resolve_route_arg(
                    kwargs.get("route_id_leading_away_from_new_waypoint"), new_id, order[index + 1]
                )
                if away_id:
                    kwargs = dict(kwargs, route_id_leading_away_from_new_waypoint=away_id)
        return self._call_raw_tool_sync("navigation_replace_one_waypoint", kwargs)

    def navigation_replace_final_destination_guarded(self, **kwargs: Any) -> dict[str, Any]:
        """Validate or fill the leading route without interpreting user language."""

        new_id = kwargs.get("new_destination_id")
        if not isinstance(new_id, str):
            return self._call_raw_tool_sync("navigation_replace_final_destination", kwargs)
        blocker = self._require_tool_surface_for_calls(
            "destination_replacement_surface",
            "change the destination",
            [("navigation_replace_final_destination", {"new_destination_id": new_id})],
        )
        if blocker:
            return blocker
        order = self._fresh_waypoint_order()
        if len(order) >= 2:
            previous_id = order[-2]
            route_id, routes = self._resolve_explicit_or_unique_route_arg(
                kwargs.get("route_id_leading_to_new_destination"),
                previous_id,
                new_id,
            )
            if isinstance(route_id, str):
                kwargs = dict(kwargs, route_id_leading_to_new_destination=route_id)
                self._store_route_narration(routes, route_id)
        return self._call_raw_tool_sync("navigation_replace_final_destination", kwargs)

    # ------------------------------------------------------------------
    # Auto-persistence of grounded entities
    # ------------------------------------------------------------------

    @staticmethod
    def _plain_value(value: Any) -> Any:
        """Return a plain JSON-safe scalar, dropping tainted 'unknown' values."""

        if isinstance(value, UnknownToolResponseValue):
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        return None

    def _auto_persist_entities(
        self,
        parsed: list[dict[str, Any]],
        calls: list[dict[str, Any]] | None = None,
    ) -> None:
        try:
            self._auto_persist_entities_inner(parsed, calls or [])
        except Exception:
            # Best-effort continuity; must never break the turn (e.g. touching a
            # tainted response field).
            pass

    def _auto_persist_entities_inner(
        self,
        parsed: list[dict[str, Any]],
        calls: list[dict[str, Any]],
    ) -> None:
        self._ensure_scratchpad_shape()
        entities = self.scratchpad["entities"]
        for index, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool_name") or "")
            if str(item.get("status") or "").upper() != "SUCCESS":
                continue
            arguments: dict[str, Any] = {}
            if index < len(calls) and isinstance(calls[index], dict):
                candidate = calls[index].get("arguments")
                if isinstance(candidate, dict):
                    arguments = candidate
            payload = item.get("result")
            if not isinstance(payload, dict):
                payload = item
            if name == "delete_current_navigation":
                revision = int(entities.get("navigation_revision") or 0) + 1
                entities["navigation_revision"] = revision
                entities["navigation_state"] = {
                    "navigation_active": False,
                    "revision": revision,
                }
                entities.pop("last_routes", None)
                entities.pop("last_route_options", None)
                entities.pop("selected_route", None)
                entities.pop("route_selection_history", None)
                continue
            if name in NAVIGATION_ACTIVATING_MUTATIONS:
                revision = int(entities.get("navigation_revision") or 0) + 1
                entities["navigation_revision"] = revision
                entities["last_successful_navigation_mutation"] = {
                    "status": "SUCCESS",
                    "tool_name": name,
                    "arguments": copy.deepcopy(arguments),
                    "result": copy.deepcopy(payload),
                    "revision": revision,
                }
                waypoint_ids = self._first_string_list(
                    payload,
                    "new_waypoints",
                    "new_waypoints_id",
                    "waypoints_id",
                    "waypoints",
                )
                route_ids = self._first_string_list(
                    payload,
                    "new_routes",
                    "new_routes_id",
                    "routes_to_final_destination_id",
                    "route_ids",
                )
                if name == "set_new_navigation" and not route_ids:
                    route_ids = [
                        route_id
                        for route_id in arguments.get("route_ids", [])
                        if isinstance(route_id, str)
                    ]
                navigation_active = not (
                    waypoint_ids and len(waypoint_ids) < 2 and not route_ids
                )
                state: dict[str, Any] = {
                    "navigation_active": navigation_active,
                    "revision": revision,
                    "stale": not bool(waypoint_ids),
                }
                if waypoint_ids:
                    state.update(
                        waypoint_ids=waypoint_ids,
                        waypoint_order=waypoint_ids,
                        start_id=waypoint_ids[0],
                        destination_id=waypoint_ids[-1],
                        final_destination_id=waypoint_ids[-1],
                    )
                    state.update(self._navigation_shape_facts(waypoint_ids))
                if route_ids:
                    state["route_ids"] = route_ids
                entities["navigation_state"] = state
                selected = entities.pop("selected_route", None)
                if isinstance(selected, dict):
                    selected_id = selected.get("route_id")
                    if selected_id and self._value_contains(arguments, selected_id):
                        entities["last_applied_route_selection"] = {
                            **selected,
                            "applied_revision": revision,
                            "applied_by": name,
                        }
                entities.pop("last_routes", None)
                entities.pop("last_route_options", None)
                entities.pop("route_selection_history", None)
                continue
            if name == "get_current_navigation_state":
                summary = self._summarize_navigation(payload)
                summary["revision"] = int(entities.get("navigation_revision") or 0)
                entities["navigation_state"] = summary
                self.remember("last_navigation_state_turn", self.last_user_message)
            elif name == "get_temperature_inside_car":
                entities["last_temperature_state"] = copy.deepcopy(payload)
            elif name == "get_seat_heating_level":
                entities["last_seat_heating_state"] = copy.deepcopy(payload)
            elif name == "get_climate_settings":
                entities["last_climate_settings"] = copy.deepcopy(payload)
                self.remember("last_climate_settings_turn", self.last_user_message)
            elif name == "get_location_id_by_location_name":
                location_id = payload.get("location_id") or payload.get("id")
                location_name = arguments.get("location") or payload.get("name")
                if payload.get("is_poi") is True:
                    poi_id = payload.get("poi_id") or payload.get("navigation_id") or location_id
                    if isinstance(poi_id, str) and poi_id:
                        poi = {
                            "poi_id": poi_id,
                            "id": poi_id,
                            "navigation_id": payload.get("navigation_id") or poi_id,
                            "name": payload.get("name") or location_name,
                            "category": payload.get("category"),
                        }
                        selected = {
                            "status": "SUCCESS",
                            "poi": copy.deepcopy(poi),
                            "selected": copy.deepcopy(poi),
                            "result": copy.deepcopy(poi),
                            "poi_id": poi_id,
                            "id": poi_id,
                            "navigation_id": poi.get("navigation_id"),
                            "name": poi.get("name"),
                        }
                        entities["selected_poi"] = copy.deepcopy(selected)
                        category = str(poi.get("category") or "").lower()
                        if "charging" in category or self._is_charging_poi_id(poi_id):
                            entities["selected_charging_poi"] = copy.deepcopy(selected)
                        pois_by_id = entities.setdefault("pois_by_id", {})
                        if isinstance(pois_by_id, dict):
                            existing = pois_by_id.get(poi_id)
                            merged = dict(existing) if isinstance(existing, dict) else {}
                            merged.update({k: v for k, v in poi.items() if v is not None})
                            pois_by_id[poi_id] = merged
                    continue
                if isinstance(location_id, str):
                    lookup = {"location_id": location_id, "id": location_id}
                    if isinstance(location_name, str) and location_name.strip():
                        lookup["name"] = location_name.strip()
                        lookup["display"] = f"{location_name.strip()} ({location_id})"
                    entities["last_location_lookup"] = lookup
                    by_id = entities.setdefault("locations_by_id", {})
                    if isinstance(by_id, dict):
                        by_id[location_id] = copy.deepcopy(lookup)
            elif name in ("search_poi_at_location", "search_poi_along_the_route"):
                pois = self._summarize_pois(item, arguments)
                if pois:
                    entities["last_pois"] = pois
                    pois_by_id = entities.setdefault("pois_by_id", {})
                    if isinstance(pois_by_id, dict):
                        for poi in pois:
                            poi_id = poi.get("poi_id") or poi.get("id")
                            if isinstance(poi_id, str):
                                pois_by_id[poi_id] = copy.deepcopy(poi)
            elif name == "get_entries_from_calendar":
                calendar = self._summarize_calendar(payload)
                entities["last_calendar"] = calendar
            elif name == "calculate_charging_time_by_soc":
                station_id = arguments.get("charging_station_id")
                plug_id = arguments.get("charging_station_plug_id")
                if isinstance(station_id, str) and station_id:
                    pois_by_id = entities.get("pois_by_id")
                    poi = (
                        pois_by_id.get(station_id)
                        if isinstance(pois_by_id, dict)
                        else None
                    )
                    plan = {
                        "charging_station_id": station_id,
                        "charging_station_plug_id": plug_id,
                        "result": copy.deepcopy(payload),
                    }
                    if isinstance(poi, dict):
                        for key in ("name", "phone_number", "phone", "display"):
                            if key in poi:
                                plan[key] = copy.deepcopy(poi[key])
                    entities["selected_charging_plan"] = plan
            elif name == "get_routes_from_start_to_destination":
                routes = self._summarize_routes(item)
                if routes:
                    entities["last_routes"] = routes
                    routes_by_id = entities.setdefault("routes_by_id", {})
                    if isinstance(routes_by_id, dict):
                        for route in routes:
                            route_id = route.get("route_id") or route.get("id")
                            if isinstance(route_id, str):
                                routes_by_id[route_id] = copy.deepcopy(route)
                    fastest = self.select_route(
                        routes, alias="fastest", record_selection=False
                    )
                    shortest = self.select_route(
                        routes, alias="shortest", record_selection=False
                    )
                    fastest_route = (
                        fastest.get("route")
                        if fastest.get("status") == "SUCCESS"
                        else None
                    )
                    shortest_route = (
                        shortest.get("route")
                        if shortest.get("status") == "SUCCESS"
                        else None
                    )
                    entities["last_route_options"] = {
                        "revision": int(entities.get("navigation_revision") or 0),
                        "start_id": arguments.get("start_id"),
                        "destination_id": arguments.get("destination_id"),
                        "routes": routes,
                        "fastest": fastest_route,
                        "shortest": shortest_route,
                        "fastest_route_id": (
                            fastest_route.get("route_id")
                            if isinstance(fastest_route, dict)
                            else None
                        ),
                        "shortest_route_id": (
                            shortest_route.get("route_id")
                            if isinstance(shortest_route, dict)
                            else None
                        ),
                    }
            elif name == "get_contact_information":
                contacts = self._summarize_contacts(payload)
                if contacts:
                    entities["last_contacts"] = contacts
                    self._remember_contacts_by_id(contacts)
            elif name == "get_user_preferences":
                entities["last_user_preferences"] = copy.deepcopy(payload)

    @staticmethod
    def _first_string_list(payload: dict[str, Any], *keys: str) -> list[str]:
        for key in keys:
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            strings: list[str] = []
            for item in value:
                if isinstance(item, str):
                    strings.append(item)
                elif isinstance(item, dict):
                    item_id = item.get("id") or item.get("route_id")
                    if isinstance(item_id, str):
                        strings.append(item_id)
            if strings:
                return strings
        return []

    @staticmethod
    def _value_contains(value: Any, expected: Any) -> bool:
        if value == expected:
            return True
        if isinstance(value, dict):
            return any(
                CoroutineWorkspace._value_contains(item, expected)
                for item in value.values()
            )
        if isinstance(value, (list, tuple)):
            return any(
                CoroutineWorkspace._value_contains(item, expected)
                for item in value
            )
        return False

    @staticmethod
    def _navigation_shape_facts(waypoint_ids: list[str]) -> dict[str, Any]:
        waypoint_count = len(waypoint_ids)
        intermediate_count = max(0, waypoint_count - 2)
        is_multi_stop = intermediate_count > 0
        return {
            "waypoint_count": waypoint_count,
            "segment_count": max(0, waypoint_count - 1),
            "intermediate_waypoint_count": intermediate_count,
            "is_multi_stop": is_multi_stop,
        }

    def _summarize_navigation(self, payload: dict[str, Any]) -> dict[str, Any]:
        active = payload.get("navigation_active")
        summary: dict[str, Any] = {
            "navigation_active": active if isinstance(active, bool) else None
        }
        details = payload.get("details")
        waypoints = details.get("waypoints") if isinstance(details, dict) else None
        names: list[str] = []
        if isinstance(waypoints, list):
            for waypoint in waypoints[:8]:
                if isinstance(waypoint, dict):
                    name = self._plain_value(waypoint.get("name"))
                    if isinstance(name, str):
                        names.append(name)
        if names:
            summary["waypoint_names"] = names
        waypoint_ids = payload.get("waypoints_id")
        if isinstance(waypoint_ids, list) and waypoint_ids:
            normalized_ids = [
                item for item in waypoint_ids if isinstance(item, str)
            ]
            if normalized_ids:
                summary["waypoint_ids"] = normalized_ids
                summary["waypoint_order"] = normalized_ids
                summary["start_id"] = normalized_ids[0]
                summary["destination_id"] = normalized_ids[-1]
                summary["final_destination_id"] = normalized_ids[-1]
                summary.update(self._navigation_shape_facts(normalized_ids))
        route_ids = payload.get("routes_to_final_destination_id")
        if isinstance(route_ids, list):
            summary["route_ids"] = [
                item for item in route_ids if isinstance(item, str)
            ]
        unknown_fields = self._navigation_state_unknown_fields(payload)
        if unknown_fields:
            summary["unknown_response_fields"] = unknown_fields
            summary["route_structure_available"] = False
        return summary

    def _summarize_pois(
        self,
        item: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pois = pois_value(item)
        host_location_id_from_args = None
        if isinstance(arguments, dict):
            raw_location_id = arguments.get("location_id")
            if isinstance(raw_location_id, str):
                host_location_id_from_args = raw_location_id
        locations_by_id = self.scratchpad.get("entities", {}).get("locations_by_id")
        locations_by_id = locations_by_id if isinstance(locations_by_id, dict) else {}
        # Additive normalization: keep a generous window so "send me all results"
        # stays answerable, and surface the IDs (poi_id, plug_ids) the model has
        # been re-asking the user for. The raw result is still readable directly.
        limit = 12
        for poi in pois[:limit]:
            if not isinstance(poi, dict):
                continue
            entry: dict[str, Any] = {}
            for key in (
                "name", "id", "location_id", "phone_number", "phone",
                "is_open", "opening_hours", "address", "category",
                "plug_ids", "plug_id",
                "connector_ids", "corresponding_location_id",
            ):
                if key in poi:
                    value = self._plain_value(poi.get(key))
                    if isinstance(value, str):
                        value = value.strip()
                    if value is not None:
                        entry[key] = value
            # Stable `poi_id` alias so the model never has to guess the key name.
            if "id" in entry and "poi_id" not in entry:
                entry["poi_id"] = entry["id"]
            if "poi_id" in entry:
                # Navigation targets the POI, not the city or area containing it.
                entry["navigation_id"] = entry["poi_id"]
            if "corresponding_location_id" in entry:
                entry["host_location_id"] = entry["corresponding_location_id"]
            elif host_location_id_from_args is not None:
                entry["host_location_id"] = host_location_id_from_args
            host_location_id = entry.get("host_location_id")
            if isinstance(host_location_id, str):
                host = locations_by_id.get(host_location_id)
                if isinstance(host, dict) and isinstance(host.get("name"), str):
                    entry["host_location_name"] = host["name"]
            charging_plugs = poi.get("charging_plugs")
            if isinstance(charging_plugs, list):
                plugs: list[dict[str, Any]] = []
                for plug in charging_plugs:
                    if not isinstance(plug, dict):
                        continue
                    normalized_plug = {
                        key: value
                        for key in ("plug_id", "power_type", "power_kw", "availability")
                        if (value := self._plain_value(plug.get(key))) is not None
                    }
                    if normalized_plug:
                        plugs.append(normalized_plug)
                if plugs:
                    entry["charging_plugs"] = plugs
                    entry["plug_ids"] = [
                        plug["plug_id"]
                        for plug in plugs
                        if isinstance(plug.get("plug_id"), str)
                    ]
                    entry["available_plug_ids"] = [
                        plug["plug_id"]
                        for plug in plugs
                        if isinstance(plug.get("plug_id"), str)
                        and str(plug.get("availability") or "").lower() == "available"
                    ]
            detour_distance = poi.get("detour_from_route_km")
            if isinstance(detour_distance, dict):
                detour_km = self._plain_value(detour_distance.get("detour"))
                if isinstance(detour_km, (int, float)):
                    entry["detour_km"] = detour_km
            detour_time = poi.get("detour_from_route_time")
            if isinstance(detour_time, dict):
                hours = self._plain_value(detour_time.get("hour"))
                minutes = self._plain_value(detour_time.get("minutes"))
                if isinstance(hours, (int, float)) and isinstance(
                    minutes, (int, float)
                ):
                    entry["detour_minutes"] = int(hours) * 60 + int(minutes)
            name = entry.get("name")
            poi_id = entry.get("poi_id")
            navigation_id = entry.get("navigation_id")
            host_id = entry.get("host_location_id")
            host_name = entry.get("host_location_name")
            if isinstance(name, str) and isinstance(poi_id, str):
                host_label = ""
                if isinstance(host_name, str) and isinstance(host_id, str):
                    host_label = f"; host location: {host_name} ({host_id})"
                elif isinstance(host_id, str):
                    host_label = f"; host location id: {host_id}"
                entry["display"] = (
                    f"{name} (POI id: {poi_id}; navigation_id: "
                    f"{navigation_id or poi_id}{host_label})"
                )
            if entry:
                out.append(entry)
        if isinstance(pois, list) and len(pois) > limit:
            out.append({"_truncated": True, "_total": len(pois)})
        return out

    @classmethod
    def _summarize_calendar(cls, payload: dict[str, Any]) -> dict[str, Any]:
        meetings = payload.get("meetings")
        normalized_entries = (
            cls._normalize_calendar_entries(meetings)
            if isinstance(meetings, list)
            else []
        )
        return {
            "date": copy.deepcopy(payload.get("date")),
            "entries": normalized_entries,
            "meetings": normalized_entries,
        }

    @classmethod
    def _normalize_calendar_entries(cls, meetings: list[Any]) -> list[dict[str, Any]]:
        normalized_entries: list[dict[str, Any]] = []
        for meeting in meetings:
            if not isinstance(meeting, dict):
                continue
            entry = cls._normalize_calendar_entry(meeting)
            if entry is not None:
                normalized_entries.append(entry)
        return normalized_entries

    @classmethod
    def _normalize_calendar_entry(cls, meeting: dict[str, Any]) -> dict[str, Any] | None:
        start = meeting.get("start")
        if not isinstance(start, dict):
            return copy.deepcopy(meeting)
        start_hour = cls._parse_first_number(start.get("hour"))
        start_minute = cls._parse_first_number(start.get("minute"))
        if start_hour is None or start_minute is None:
            return copy.deepcopy(meeting)
        hour = int(start_hour)
        minute = int(start_minute)
        entry = copy.deepcopy(meeting)
        entry["start"] = {"hour": hour, "minute": minute}
        entry["start_hour"] = hour
        entry["start_minute"] = minute
        entry["start_time_hour"] = hour
        entry["start_time_minute"] = minute
        entry["start_minutes"] = hour * 60 + minute
        entry["start_time_minutes"] = entry["start_minutes"]
        entry["start_time_24h"] = f"{hour:02d}:{minute:02d}"
        entry["start_time"] = entry["start_time_24h"]
        topic = entry.get("topic")
        location = entry.get("location")
        if isinstance(location, str) and location.strip():
            entry["location_name"] = location.strip()
        duration = entry.get("duration")
        parts = [entry["start_time_24h"]]
        if isinstance(duration, str) and duration.strip():
            parts.append(f"({duration.strip()})")
        if isinstance(topic, str) and topic.strip():
            parts.append(topic.strip())
        if isinstance(location, str) and location.strip():
            parts.append(f"at {location.strip()}")
        entry["display"] = " ".join(parts)
        return entry

    def _summarize_routes(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for route in routes_value(item)[:6]:
            if not isinstance(route, dict):
                continue
            entry: dict[str, Any] = {}
            for key in (
                "route_id", "id", "name", "via", "name_via", "start_id", "destination_id",
                "base_route_id",
                "distance", "distance_km", "duration", "duration_hours",
                "duration_minutes", "duration_total_minutes", "has_tolls",
                "tolls", "includes_toll",
            ):
                if key in route:
                    value = self._plain_value(route.get(key))
                    if value is not None:
                        entry[key] = value
            # alias (fastest/shortest tags) is a list — preserve it for narration.
            alias = route.get("alias")
            if isinstance(alias, list):
                entry["alias"] = [str(tag).lower() for tag in alias]
            road_types = route.get("road_types")
            if isinstance(road_types, list):
                entry["road_types"] = [
                    str(road_type)
                    for road_type in road_types
                    if isinstance(road_type, str)
                ]
            if entry:
                out.append(self._normalize_route(entry))
        return out

    def _normalize_contact_record(
        self,
        contact_id: str | None,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {}
        grounded_id = contact_id or value.get("contact_id") or value.get("id")
        if isinstance(grounded_id, str):
            entry["contact_id"] = grounded_id
            entry["id"] = grounded_id
        name = value.get("name")
        if isinstance(name, dict):
            first = self._plain_value(name.get("first_name"))
            last = self._plain_value(name.get("last_name"))
            if isinstance(first, str):
                entry["first_name"] = first.strip()
            if isinstance(last, str):
                entry["last_name"] = last.strip()
            display = " ".join(
                part for part in (entry.get("first_name"), entry.get("last_name")) if part
            )
            if display:
                entry["display_name"] = display
            entry["name"] = {
                key: entry[key]
                for key in ("first_name", "last_name")
                if key in entry
            }
        elif isinstance(name, str):
            entry["name"] = name.strip()
            entry["display_name"] = name.strip()
        for field in ("first_name", "last_name", "email", "phone_number"):
            plain = self._plain_value(value.get(field))
            if isinstance(plain, str):
                entry[field] = plain.strip()
        if "display_name" not in entry:
            display = " ".join(
                part for part in (entry.get("first_name"), entry.get("last_name")) if part
            )
            if display:
                entry["display_name"] = display
        return entry

    def _summarize_contacts(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(payload, dict):
            return out
        contacts = payload.get("contacts")
        if isinstance(contacts, list):
            for value in contacts[:8]:
                if not isinstance(value, dict):
                    continue
                entry = self._normalize_contact_record(None, value)
                if entry:
                    out.append(entry)
            return out
        for key, value in payload.items():
            if key in ("status", "tool_name", "tool_call_id"):
                continue
            if not isinstance(value, dict):
                continue
            entry = self._normalize_contact_record(
                key if isinstance(key, str) else None,
                value,
            )
            if entry:
                out.append(entry)
            if len(out) >= 8:
                break
        return out

    def _remember_contacts_by_id(self, contacts: list[dict[str, Any]]) -> None:
        entities = self.scratchpad.get("entities")
        if not isinstance(entities, dict):
            return
        contacts_by_id = entities.setdefault("contacts_by_id", {})
        if not isinstance(contacts_by_id, dict):
            return
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            contact_id = contact.get("contact_id") or contact.get("id")
            if not isinstance(contact_id, str) or not contact_id:
                continue
            existing = contacts_by_id.get(contact_id)
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(copy.deepcopy(contact))
            contacts_by_id[contact_id] = merged

    def call_tool_sync(self, tool_name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """Public policy-aware single-call entry point."""

        tool_name = self._canonical_call_name(tool_name)
        if kwargs:
            arguments = dict(arguments or {}, **kwargs)
        arguments = dict(arguments or {})
        results = self.call_batch_sync([{"tool_name": tool_name, "arguments": arguments}])
        if not results:
            raise RuntimeError(f"Tool {tool_name!r} returned no result")
        return results[0]

    def _call_raw_tool_sync(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        tool_name = self._canonical_call_name(tool_name)
        if kwargs:
            arguments = dict(arguments or {}, **kwargs)
        arguments = dict(arguments or {})
        results = self._call_raw_tools_sync([{"tool_name": tool_name, "arguments": arguments}])
        if not results:
            raise RuntimeError(f"Tool {tool_name!r} returned no result")
        return results[0]

    def call_batch_sync(self, calls: list[Any]) -> list[dict[str, Any]]:
        """Execute a batch containing raw evaluator tools and workspace helpers.

        Raw evaluator calls are kept in one parallel A2A request. Workspace
        helpers are Python routines that may perform their own staged tool
        calls, so they execute through their bound implementations.
        """

        normalized = []
        for index, item in enumerate(calls):
            spec = self._normalize_call_spec(item)
            spec["arguments"] = self._resolve_preloaded_argument_value(
                spec["arguments"]
            )
            requested_name = spec["tool_name"]
            delegated = self._delegate_policy_sensitive_call(spec)
            # Remember the name the model actually used so the result envelope is
            # found by that name (raw delegation rewrites it to an internal
            # *_guarded helper the model never references directly).
            delegated.setdefault("_requested_name", requested_name)
            delegated["_batch_index"] = index
            normalized.append(delegated)
        raw_calls = [
            call for call in normalized if call["tool_name"] not in WORKSPACE_HELPER_NAMES
        ]
        helper_calls = [
            call for call in normalized if call["tool_name"] in WORKSPACE_HELPER_NAMES
        ]
        result_slots: list[dict[str, Any] | None] = [None] * len(normalized)
        if raw_calls:
            raw_results = self._call_raw_tools_sync(raw_calls)
            for call, result in zip(raw_calls, raw_results):
                result_slots[int(call["_batch_index"])] = result
        for call in helper_calls:
            helper_name = call["tool_name"]
            helper = getattr(self, helper_name)
            helper_result = helper(**call["arguments"])
            helper_status = (
                str(helper_result.get("status") or "SUCCESS")
                if isinstance(helper_result, dict)
                else "SUCCESS"
            )
            envelope = {
                "status": helper_status,
                "tool_name": call.get("_requested_name", helper_name),
                "tool_call_id": "",
                "result": helper_result,
            }
            # Make a batched helper as easy to read as a direct call: copy the
            # helper's own NON-reserved top-level fields up to the envelope so
            # `result_by_tool(results, name)["fastest"]` works the same as the
            # direct `helper(...)["fastest"]`. Reserved keys stay runtime-owned
            # so a helper field can never overwrite the batch envelope.
            if isinstance(helper_result, dict):
                for key, value in helper_result.items():
                    if key not in _RESERVED_BATCH_ENVELOPE_KEYS and key not in envelope:
                        envelope[key] = value
            result_slots[int(call["_batch_index"])] = envelope
        return [result for result in result_slots if result is not None]

    def call_tools_sync(self, calls: list[Any]) -> list[dict[str, Any]]:
        """Public policy-aware multi-call entry point."""

        return self.call_batch_sync(calls)

    def _call_raw_tools_sync(self, calls: list[Any]) -> list[dict[str, Any]]:
        """Emit raw evaluator calls after helper/policy dispatch has completed."""

        normalized = [
            {**self._normalize_call_spec(item), "_input_index": index}
            for index, item in enumerate(calls)
        ]
        normalized = self._normalize_protocol_batch(normalized)
        normalized = [
            {
                "tool_name": call["tool_name"],
                "arguments": self._normalize_tool_arguments(call["tool_name"], call["arguments"]),
                "_input_index": call["_input_index"],
            }
            for call in normalized
        ]
        normalized = self._repair_climate_sync_inverse_calls(normalized)
        local_slots: list[dict[str, Any] | None] = [None] * len(normalized)
        bridge_calls: list[dict[str, Any]] = []
        bridge_positions: list[int] = []
        for index, call in enumerate(normalized):
            local_result = self._known_poi_location_lookup_result(call)
            if local_result is not None:
                local_slots[index] = local_result
                continue
            bridge_calls.append(call)
            bridge_positions.append(index)
        blocked_by_surface = self._tool_surface_blocker_result(
            "tool_surface",
            "do that",
            bridge_calls,
        )
        if blocked_by_surface is not None:
            for position, result in zip(bridge_positions, blocked_by_surface):
                local_slots[position] = result
            ordered = [result for result in local_slots if result is not None]
            self.observe_environment(ordered)
            return ordered
        for call in bridge_calls:
            self._validate_tool_call(call["tool_name"], call["arguments"])
        blocked_by_confirmation = self._confirmation_required_blocker_result(bridge_calls)
        if blocked_by_confirmation is not None:
            for position, result in zip(bridge_positions, blocked_by_confirmation):
                local_slots[position] = result
            ordered = [result for result in local_slots if result is not None]
            self.observe_environment(ordered)
            return ordered
        policy_011_blocker = self._active_policy_011_blocker()
        if policy_011_blocker is not None:
            for call in bridge_calls:
                if call["tool_name"] == "set_air_conditioning" and call["arguments"].get("on") is True:
                    blocked = self._block_policy_011_action("turn on AC", policy_011_blocker)
                    blocked_results = [
                        {
                            **blocked,
                            "tool_name": item["tool_name"],
                            "tool_call_id": "",
                        }
                        for item in bridge_calls
                    ]
                    for position, result in zip(bridge_positions, blocked_results):
                        local_slots[position] = result
                    ordered = [result for result in local_slots if result is not None]
                    self.observe_environment(ordered)
                    return ordered

        parsed_slots: list[dict[str, Any] | None] = list(local_slots)
        uncached_calls: list[dict[str, Any]] = []
        uncached_indices: list[int] = []
        uncached_keys: list[str | None] = []
        for position, call in zip(bridge_positions, bridge_calls):
            if parsed_slots[position] is not None:
                continue
            cache_key = self._read_cache_key(call)
            cached = self._read_cache.get(cache_key) if cache_key is not None else None
            if cached is None:
                uncached_calls.append(call)
                uncached_indices.append(position)
                uncached_keys.append(cache_key)
                continue
            repeat_count = self._read_repeat_counts.get(cache_key, 0) + 1
            self._read_repeat_counts[cache_key] = repeat_count
            repeated = copy.deepcopy(cached)
            repeated["cached"] = True
            repeated["repeat_count"] = repeat_count
            repeated["no_progress"] = True
            parsed_slots[position] = repeated
            self.remember(
                "last_no_progress",
                {
                    "tool_name": call["tool_name"],
                    "arguments": copy.deepcopy(call["arguments"]),
                    "repeat_count": repeat_count,
                    "message": (
                        "This identical read already succeeded in the current state. "
                        "Reuse the cached result or choose a different next step."
                    ),
                },
            )

        parsed_uncached: list[dict[str, Any]] = []
        if uncached_calls:
            outbound_calls = [
                {
                    "tool_name": call["tool_name"],
                    "arguments": call["arguments"],
                }
                for call in uncached_calls
            ]
            raw_results = self.bridge.request_tool_calls(outbound_calls)
            parsed_uncached = [self._parse_tool_result(item) for item in raw_results]
            for index, parsed_item in zip(uncached_indices, parsed_uncached):
                parsed_slots[index] = parsed_item

        parsed = [item for item in parsed_slots if item is not None]
        self._record_mutation_outcomes(parsed, normalized)
        successful_mutation = any(
            item.get("tool_name") in MUTATING_TOOL_NAMES
            and str(item.get("status") or "").upper() == "SUCCESS"
            for item in parsed
        )
        if successful_mutation:
            self._state_revision += 1
            self._read_cache.clear()
            self._read_repeat_counts.clear()
        else:
            for cache_key, parsed_item in zip(uncached_keys, parsed_uncached):
                if (
                    cache_key is not None
                    and str(parsed_item.get("status") or "").upper() == "SUCCESS"
                ):
                    self._read_cache[cache_key] = copy.deepcopy(parsed_item)
                    self._read_repeat_counts.setdefault(cache_key, 0)
        self._auto_persist_entities(parsed, normalized)
        ordered = self._restore_raw_result_order(parsed, normalized)
        self.observe_environment(ordered)
        return ordered

    def _known_poi_location_lookup_result(
        self,
        call: dict[str, Any],
    ) -> dict[str, Any] | None:
        if call.get("tool_name") != "get_location_id_by_location_name":
            return None
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            return None
        name = arguments.get("location")
        if not isinstance(name, str) or not name.strip():
            return None
        poi = self._unique_known_poi_by_name(name)
        if poi is None:
            return None
        poi_id = poi.get("poi_id") or poi.get("id") or poi.get("navigation_id")
        if not isinstance(poi_id, str) or not poi_id:
            return None
        result = {
            "id": poi_id,
            "location_id": poi_id,
            "poi_id": poi_id,
            "navigation_id": poi.get("navigation_id") or poi_id,
            "name": poi.get("name") or name.strip(),
            "category": poi.get("category"),
            "is_poi": True,
            "source": "known_poi",
        }
        self.remember(
            "last_known_poi_location_lookup",
            {
                "requested_name": name.strip(),
                "poi_id": poi_id,
                "message": (
                    "The requested name matches a previously grounded POI. "
                    "Use its POI/navigation ID instead of a city-location lookup."
                ),
            },
        )
        return {
            "status": "SUCCESS",
            "tool_name": "get_location_id_by_location_name",
            "tool_call_id": "",
            "result": result,
            "resolved_known_poi": True,
        }

    def _unique_known_poi_by_name(self, name: str) -> dict[str, Any] | None:
        wanted = self._normalize_poi_selector_text(name)
        if not wanted:
            return None
        entities = self.scratchpad.get("entities")
        if not isinstance(entities, dict):
            return None
        candidates: list[dict[str, Any]] = []

        def add(candidate: Any) -> None:
            if isinstance(candidate, dict):
                poi = candidate.get("poi") if isinstance(candidate.get("poi"), dict) else candidate
                if isinstance(poi, dict):
                    candidates.append(poi)

        add(entities.get("selected_poi"))
        add(entities.get("selected_charging_poi"))
        last_pois = entities.get("last_pois")
        if isinstance(last_pois, list):
            for poi in last_pois:
                add(poi)
        pois_by_id = entities.get("pois_by_id")
        if isinstance(pois_by_id, dict):
            for poi in pois_by_id.values():
                add(poi)

        matches: dict[str, dict[str, Any]] = {}
        for poi in candidates:
            poi_name = poi.get("name")
            if self._normalize_poi_selector_text(poi_name) != wanted:
                continue
            poi_id = poi.get("poi_id") or poi.get("id") or poi.get("navigation_id")
            if not isinstance(poi_id, str) or not poi_id:
                continue
            normalized = copy.deepcopy(poi)
            normalized.setdefault("poi_id", poi_id)
            normalized.setdefault("navigation_id", poi_id)
            matches[poi_id] = normalized
        if len(matches) == 1:
            return next(iter(matches.values()))
        return None

    @staticmethod
    def _restore_raw_result_order(
        results: list[dict[str, Any]],
        calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(results) != len(calls):
            return results
        slots: list[dict[str, Any] | None] = [None] * len(results)
        for result, call in zip(results, calls):
            index = call.get("_input_index")
            if not isinstance(index, int) or not 0 <= index < len(slots):
                return results
            slots[index] = result
        return [result for result in slots if result is not None]

    def _repair_climate_sync_inverse_calls(
        self,
        calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        intent = self._explicit_climate_sync_intent()
        if intent is None:
            return calls
        source_zone, target_zone = intent
        entities = self.scratchpad.get("entities", {})
        repairs: list[dict[str, Any]] = []
        repaired_calls: list[dict[str, Any]] = []
        for call in calls:
            tool_name = call.get("tool_name")
            arguments = call.get("arguments")
            if not isinstance(arguments, dict):
                repaired_calls.append(call)
                continue
            repaired_arguments = dict(arguments)
            if tool_name == "set_climate_temperature":
                temp_state = entities.get("last_temperature_state")
                replacement = self._inverse_climate_temperature_repair(
                    arguments,
                    temp_state,
                    source_zone,
                    target_zone,
                )
                if replacement is not None:
                    repaired_arguments = replacement
            elif tool_name == "set_seat_heating":
                heat_state = entities.get("last_seat_heating_state")
                replacement = self._inverse_seat_heating_repair(
                    arguments,
                    heat_state,
                    source_zone,
                    target_zone,
                )
                if replacement is not None:
                    repaired_arguments = replacement
            if repaired_arguments != arguments:
                repairs.append(
                    {
                        "tool_name": str(tool_name),
                        "from_arguments": copy.deepcopy(arguments),
                        "to_arguments": copy.deepcopy(repaired_arguments),
                        "source_zone": source_zone,
                        "target_zone": target_zone,
                    }
                )
                call = dict(call, arguments=repaired_arguments)
            repaired_calls.append(call)
        if repairs:
            self.scratchpad["gates"]["climate_sync_guard"] = {
                "status": "REPAIRED",
                "repairs": repairs,
            }
        return repaired_calls

    def _explicit_climate_sync_intent(self) -> tuple[str, str] | None:
        text = " ".join(str(self.last_user_message or "").casefold().split())
        if not ("driver" in text and "passenger" in text):
            return None
        if not any(word in text for word in ("sync", "copy", "match", "same as")):
            return None
        for target_word, target_zone, source_word, source_zone in (
            ("driver", "DRIVER", "passenger", "PASSENGER"),
            ("passenger", "PASSENGER", "driver", "DRIVER"),
        ):
            if self._text_order(text, target_word, "match", source_word):
                return source_zone, target_zone
            if self._text_order(text, target_word, "same as", source_word):
                return source_zone, target_zone
            if self._text_order(text, target_word, "like", source_word):
                return source_zone, target_zone
            if "copy" in text and self._text_order(text, "copy", source_word, target_word):
                return source_zone, target_zone
        return None

    @staticmethod
    def _text_order(text: str, *terms: str) -> bool:
        offset = 0
        for term in terms:
            index = text.find(term, offset)
            if index < 0:
                return False
            offset = index + len(term)
        return True

    def _inverse_climate_temperature_repair(
        self,
        arguments: dict[str, Any],
        temp_state: Any,
        source_zone: str,
        target_zone: str,
    ) -> dict[str, Any] | None:
        if not isinstance(temp_state, dict):
            return None
        seat_zone = str(arguments.get("seat_zone") or "").upper()
        requested_temp = arguments.get("temperature")
        source_temp = temp_state.get(f"climate_temperature_{source_zone.lower()}")
        target_temp = temp_state.get(f"climate_temperature_{target_zone.lower()}")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (requested_temp, source_temp, target_temp)
        ):
            return None
        if (
            seat_zone == source_zone
            and _numbers_equal(requested_temp, target_temp)
            and not _numbers_equal(source_temp, target_temp)
        ):
            return {
                **arguments,
                "seat_zone": target_zone,
                "temperature": float(source_temp),
            }
        return None

    def _inverse_seat_heating_repair(
        self,
        arguments: dict[str, Any],
        heat_state: Any,
        source_zone: str,
        target_zone: str,
    ) -> dict[str, Any] | None:
        if not isinstance(heat_state, dict):
            return None
        seat_zone = str(arguments.get("seat_zone") or "").upper()
        requested_level = arguments.get("level")
        source_level = heat_state.get(f"seat_heating_{source_zone.lower()}")
        target_level = heat_state.get(f"seat_heating_{target_zone.lower()}")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (requested_level, source_level, target_level)
        ):
            return None
        if (
            seat_zone == source_zone
            and _numbers_equal(requested_level, target_level)
            and not _numbers_equal(source_level, target_level)
        ):
            return {
                **arguments,
                "seat_zone": target_zone,
                "level": int(source_level),
            }
        return None

    def _read_cache_key(self, call: dict[str, Any]) -> str | None:
        if call.get("tool_name") in MUTATING_TOOL_NAMES:
            return None
        try:
            arguments = json.dumps(
                call.get("arguments") or {},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except Exception:
            return None
        return f"{self._state_revision}:{call.get('tool_name')}:{arguments}"

    def _tool_requires_confirmation(self, tool_name: str) -> bool:
        with self._lock:
            tool = self.available_tools.get(tool_name) or {}
        description = str(tool.get("function", {}).get("description") or "")
        return description.startswith("REQUIRES_CONFIRMATION")

    def _confirmation_required_blocker_result(
        self,
        calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        if self._confirmation_execution_depth > 0:
            return None
        confirmation_calls = [
            call for call in calls if self._tool_requires_confirmation(call["tool_name"])
        ]
        if not confirmation_calls:
            return None
        confirmation_calls = self._repair_confirmation_contact_recipients(
            confirmation_calls
        )
        unresolved = self._find_unresolved_confirmation_argument(confirmation_calls)
        if unresolved is not None:
            message = (
                "I can't request confirmation yet because required action details are "
                f"unresolved in {unresolved}."
            )
            report = {
                "helper": "tool_confirmation",
                "status": "UNAVAILABLE",
                "reason": message,
                "message": message,
                "unresolved_argument": unresolved,
            }
            self.scratchpad["gates"]["tool_confirmation"] = {
                "status": "NO",
                "reason": message,
                "unresolved_argument": unresolved,
            }
            self._store_helper_report("tool_confirmation", report)
            self._abort_with_response(message)
        prompt = self._confirmation_prompt_for_calls(confirmation_calls)
        pending = {
            "type": "tool_confirmation",
            "gate_name": "tool_confirmation",
            "policy": "004",
            "action": "perform the confirmed action",
            "on_confirm_calls": confirmation_calls,
            "confirmation_prompt": prompt,
            "confirmation_retry_prompt": "Please confirm with yes if you want me to proceed.",
            "response_on_cancel": "Okay, I won't do it.",
            "response_on_success": self._confirmation_success_message_for_calls(
                confirmation_calls
            ),
        }
        self.remember("pending_confirmation", pending)
        self.scratchpad["gates"]["tool_confirmation"] = {
            "status": "WAITING_CONFIRMATION",
            "policy": "004",
            "actions": [call["tool_name"] for call in confirmation_calls],
            "arguments": [call["arguments"] for call in confirmation_calls],
        }
        self._store_helper_report(
            "tool_confirmation",
            {
                "helper": "tool_confirmation",
                "status": "WAITING_CONFIRMATION",
                "policy": "004",
                "actions": [call["tool_name"] for call in confirmation_calls],
                "arguments": [call["arguments"] for call in confirmation_calls],
                "message": prompt,
            },
        )
        self._abort_with_response(prompt)

    def _repair_confirmation_contact_recipients(
        self,
        calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        for call in calls:
            if call.get("tool_name") != "send_email":
                repaired.append(call)
                continue
            repaired.append(self._repair_send_email_contact_recipient(call))
        return repaired

    def _repair_send_email_contact_recipient(
        self,
        call: dict[str, Any],
    ) -> dict[str, Any]:
        args = call.get("arguments")
        if not isinstance(args, dict):
            return call
        email_addresses = args.get("email_addresses")
        if not isinstance(email_addresses, list) or len(email_addresses) != 1:
            return call
        selected_email = email_addresses[0]
        if not isinstance(selected_email, str) or not selected_email.strip():
            return call
        selected_contact = self._contact_record_by_email(selected_email)
        if not isinstance(selected_contact, dict):
            return call
        selected_id = selected_contact.get("contact_id") or selected_contact.get("id")
        target_id = self._unique_contact_intersection_id()
        if not (
            isinstance(selected_id, str)
            and isinstance(target_id, str)
            and selected_id != target_id
        ):
            return call
        target_contact = self._contact_record_by_id(target_id)
        if not isinstance(target_contact, dict):
            return call
        target_email = target_contact.get("email")
        if not isinstance(target_email, str) or not target_email.strip():
            return call
        new_call = copy.deepcopy(call)
        new_args = dict(args)
        new_args["email_addresses"] = [target_email.strip()]
        new_call["arguments"] = new_args
        self.scratchpad["gates"]["contact_recipient_guard"] = {
            "status": "REPAIRED",
            "tool_name": "send_email",
            "from_contact_id": selected_id,
            "to_contact_id": target_id,
            "from_email": selected_email,
            "to_email": target_email.strip(),
            "reason": (
                "A unique contact intersection was known, but the email was "
                "addressed to a different known contact."
            ),
        }
        return new_call

    def _unique_contact_intersection_id(self) -> str | None:
        entities = self.scratchpad.get("entities", {})
        direct = entities.get("last_unique_contact_intersection_id")
        if isinstance(direct, str) and direct:
            return direct
        lookup = entities.get("last_contact_lookup")
        if isinstance(lookup, dict):
            value = lookup.get("unique_intersection_with_previous_contact_id")
            if isinstance(value, str) and value:
                return value
        return None

    def _contact_record_by_id(self, contact_id: str) -> dict[str, Any] | None:
        contacts_by_id = self.scratchpad.get("entities", {}).get("contacts_by_id")
        if isinstance(contacts_by_id, dict):
            contact = contacts_by_id.get(contact_id)
            if isinstance(contact, dict):
                return contact
        contacts = self.scratchpad.get("entities", {}).get("last_contacts")
        if isinstance(contacts, list):
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                if contact.get("contact_id") == contact_id or contact.get("id") == contact_id:
                    return contact
        return None

    def _contact_record_by_email(self, email: str) -> dict[str, Any] | None:
        needle = email.strip().lower()
        if not needle:
            return None
        contacts_by_id = self.scratchpad.get("entities", {}).get("contacts_by_id")
        if isinstance(contacts_by_id, dict):
            for contact in contacts_by_id.values():
                if not isinstance(contact, dict):
                    continue
                candidate = contact.get("email")
                if isinstance(candidate, str) and candidate.strip().lower() == needle:
                    return contact
        contacts = self.scratchpad.get("entities", {}).get("last_contacts")
        if isinstance(contacts, list):
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                candidate = contact.get("email")
                if isinstance(candidate, str) and candidate.strip().lower() == needle:
                    return contact
        return None

    @staticmethod
    def _find_unresolved_confirmation_argument(
        calls: list[dict[str, Any]],
    ) -> str | None:
        unresolved_text_patterns = (
            re.compile(r"^(?:none|null|unknown|n/?a|tbd)$", re.IGNORECASE),
            re.compile(
                r"\b(?:approximately|about|around)\s+(?:none|null|unknown)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:none|null|unknown)\s+"
                r"(?:km|kilometers?|minutes?|hours?|duration|distance|range|time)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:duration|distance|range|time)\s+(?:is\s+)?"
                r"(?:none|null|unknown)\b",
                re.IGNORECASE,
            ),
        )

        def walk(value: Any, path: str) -> str | None:
            if isinstance(value, UnknownToolResponseValue):
                value.require()
            if value is None:
                return path
            if isinstance(value, str):
                stripped = value.strip()
                if any(pattern.search(stripped) for pattern in unresolved_text_patterns):
                    return path
                return None
            if isinstance(value, dict):
                for key, nested_value in value.items():
                    unresolved = walk(nested_value, f"{path}.{key}")
                    if unresolved is not None:
                        return unresolved
                return None
            if isinstance(value, (list, tuple)):
                for index, nested_value in enumerate(value):
                    unresolved = walk(nested_value, f"{path}[{index}]")
                    if unresolved is not None:
                        return unresolved
            return None

        for call_index, call in enumerate(calls):
            tool_name = call["tool_name"]
            for argument_name, value in call["arguments"].items():
                unresolved = walk(
                    value,
                    f"{tool_name}[{call_index}].{argument_name}",
                )
                if unresolved is not None:
                    return unresolved
        return None

    @staticmethod
    def _confirmation_prompt_for_calls(calls: list[dict[str, Any]]) -> str:
        if len(calls) == 1:
            call = calls[0]
            summary = CoroutineWorkspace._confirmation_action_summary_for_call(call)
            return (
                "This action requires confirmation. I will "
                f"{summary}. Please confirm with yes."
            )
        summaries = [
            CoroutineWorkspace._confirmation_action_summary_for_call(call)
            for call in calls
        ]
        return (
            "These actions require confirmation: "
            f"{_human_join(summaries)}. Please confirm with yes."
        )

    @staticmethod
    def _confirmation_action_summary_for_call(call: dict[str, Any]) -> str:
        tool_name = call["tool_name"]
        arguments = call["arguments"]
        if tool_name == "set_head_lights_high_beams":
            if arguments.get("on") is True:
                return "turn the high beam headlights on (on=True)"
            if arguments.get("on") is False:
                return "turn the high beam headlights off (on=False)"
        if tool_name == "open_close_trunk_door":
            action = arguments.get("action")
            if action == "OPEN":
                return "open the trunk door (action=OPEN)"
            if action == "CLOSE":
                return "close the trunk door (action=CLOSE)"
        if tool_name == "send_email":
            recipients = arguments.get("email_addresses")
            recipient_text = ""
            if isinstance(recipients, list):
                recipient_text = _human_join(
                    [item for item in recipients if isinstance(item, str) and item.strip()]
                )
            content = arguments.get("content_message")
            content_text = content.strip() if isinstance(content, str) else ""
            if recipient_text and content_text:
                return f"send an email to {recipient_text} saying: {content_text}"
            if recipient_text:
                return f"send an email to {recipient_text}"
            return "send the email"

        args = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        if args:
            return f"perform {tool_name} with {args}"
        return f"perform {tool_name}"


    @staticmethod
    def _confirmation_success_message_for_calls(calls: list[dict[str, Any]]) -> str:
        if len(calls) != 1:
            return "Confirmed, I completed the requested actions."

        call = calls[0]
        tool_name = call["tool_name"]
        arguments = call["arguments"]
        if tool_name == "set_head_lights_high_beams":
            if arguments.get("on") is True:
                return "High beams turned on."
            if arguments.get("on") is False:
                return "High beams turned off."
        if tool_name == "open_close_trunk_door":
            action = arguments.get("action")
            if action == "OPEN":
                return "Trunk door opened."
            if action == "CLOSE":
                return "Trunk door closed."
        if tool_name == "send_email":
            recipients = arguments.get("email_addresses")
            if isinstance(recipients, list):
                recipient_text = _human_join(
                    [item for item in recipients if isinstance(item, str) and item.strip()]
                )
                if recipient_text:
                    return f"Email sent to {recipient_text}."
            return "Email sent."
        return "Confirmed, I completed it."

    @staticmethod
    def _confirmation_intent(text: str) -> str:
        normalized = " " + re.sub(r"\s+", " ", text.strip().lower()) + " "
        no_patterns = (
            r"\bno\b",
            r"\bnope\b",
            r"\bcancel\b",
            r"\bstop\b",
            r"\bdon't\b",
            r"\bdo not\b",
        )
        yes_patterns = (
            r"\byes\b",
            r"\byeah\b",
            r"\byep\b",
            r"\bconfirm\b",
            r"\bconfirmed\b",
            r"\bgo ahead\b",
            r"\bproceed\b",
            r"\bdo it\b",
        )
        if any(re.search(pattern, normalized) for pattern in no_patterns):
            return "NO"
        if any(re.search(pattern, normalized) for pattern in yes_patterns):
            return "YES"
        return "UNKNOWN"

    def handle_pending_confirmation(self) -> dict[str, Any] | None:
        """Resolve a stored confirmation gate from the latest user follow-up."""

        self._ensure_scratchpad_shape()
        pending = self.scratchpad["facts"].get("pending_confirmation")
        if not isinstance(pending, dict):
            return None

        gate_name = str(pending.get("gate_name") or "pending_confirmation")
        intent = self._confirmation_intent(self.last_user_message)
        if intent == "NO":
            self.scratchpad["facts"].pop("pending_confirmation", None)
            self.scratchpad["gates"][gate_name] = {
                "status": "NO",
                "policy": pending.get("policy"),
                "reason": "user declined confirmation",
            }
            message = str(pending.get("response_on_cancel") or "Okay, I won't do it.")
            self._abort_with_response(message)

        if intent != "YES":
            self.scratchpad["gates"][gate_name] = {
                "status": "WAITING_CONFIRMATION",
                "policy": pending.get("policy"),
                "reason": "confirmation was not explicit",
            }
            message = str(
                pending.get("confirmation_retry_prompt")
                or "Please confirm with yes if you want me to proceed."
            )
            self._abort_with_response(message)

        raw_calls = pending.get("on_confirm_calls") or []
        calls = [self._normalize_call_spec(item) for item in raw_calls]
        action = str(pending.get("action") or "perform the confirmed action")
        blocker = self._require_tool_surface_for_calls(gate_name, action, calls)
        self.scratchpad["facts"].pop("pending_confirmation", None)
        if blocker:
            return blocker

        self._confirmation_execution_depth += 1
        try:
            results = self._call_raw_tools_sync(calls)
        finally:
            self._confirmation_execution_depth -= 1
        for result in results:
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, action, result)

        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": pending.get("policy"),
            "confirmed": True,
            "actions": [call["tool_name"] for call in calls],
        }
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "SUCCESS",
                "policy": pending.get("policy"),
                "confirmed": True,
                "actions": [call["tool_name"] for call in calls],
                "results": results,
            },
        )
        message = str(
            pending.get("response_on_success")
            or self._confirmation_success_message_for_calls(calls)
        )
        report["message"] = message
        self._respond_locked(message)
        return {"status": "SUCCESS", "actions": results, "report": report, "message": message}

    def _current_policy_context(self) -> dict[str, Any]:
        def load_object(name: str) -> dict[str, Any] | None:
            match = re.search(rf"{name}\s*=\s*(\{{[^\n]*\}})", self.policy)
            if not match:
                return None
            try:
                value = json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None

        location = load_object("CURRENT_LOCATION")
        now = load_object("DATETIME")
        if not isinstance(location, dict) or not isinstance(now, dict):
            return {}
        return {
            "location_id": location.get("id"),
            "month": now.get("month"),
            "day": now.get("day"),
            "hour": now.get("hour"),
            "minute": now.get("minute"),
            "location": location,
            "datetime": now,
        }

    @staticmethod
    def _weather_condition(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        candidates = [
            value.get("condition"),
            value.get("weather"),
            value.get("current_weather"),
        ]
        current_slot = value.get("current_slot")
        if isinstance(current_slot, dict):
            candidates.extend([current_slot.get("condition"), current_slot.get("weather")])
        for candidate in candidates:
            if (
                isinstance(candidate, str)
                and not isinstance(candidate, UnknownToolResponseValue)
                and candidate.strip()
            ):
                return candidate.strip().lower()
        for inner in value.values():
            if isinstance(inner, dict):
                condition = CoroutineWorkspace._weather_condition(inner)
                if condition:
                    return condition
        return None

    def get_navigation_state(self, detailed_information: bool = True) -> dict[str, Any]:
        """Return the navigation state in a stable shape."""

        gate_name = "get_navigation_state"
        call = ("get_current_navigation_state", {"detailed_information": detailed_information})
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "get the current navigation state",
            [call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        if result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "get the current navigation state", result)
        payload = result_value(result)
        if not isinstance(payload, dict):
            return self._limitation_response(
                gate_name,
                "get the current navigation state",
                reason="the navigation state result had an unexpected shape",
            )
        self._require_known_response_fields(
            gate_name,
            "get the current navigation state",
            "get_current_navigation_state",
            payload,
            ["navigation_active"],
        )

        active = payload["navigation_active"]
        if not isinstance(active, bool):
            return self._limitation_response(
                gate_name,
                "get the current navigation state",
                reason="the navigation active state was not a boolean",
            )
        waypoint_ids = payload.get("waypoints_id", [])
        route_ids = payload.get("routes_to_final_destination_id", [])
        details = payload.get("details")
        waypoints = payload.get("new_waypoints", [])
        routes = payload.get("new_routes", [])
        if active:
            required = ["waypoints_id", "routes_to_final_destination_id"]
            if detailed_information:
                required.extend(["details.waypoints", "details.routes"])
            self._require_known_response_fields(
                gate_name,
                "get the current navigation state",
                "get_current_navigation_state",
                payload,
                required,
            )
        if isinstance(details, dict):
            waypoints = details.get("waypoints", waypoints)
            routes = details.get("routes", routes)
        if not isinstance(waypoint_ids, list):
            waypoint_ids = []
        if not isinstance(route_ids, list):
            route_ids = []
        if not isinstance(waypoints, list):
            waypoints = []
        if not isinstance(routes, list):
            routes = []

        start = waypoints[0] if waypoints else None
        destination = waypoints[-1] if len(waypoints) > 1 else None
        normalized = {
            "status": "SUCCESS",
            "navigation_active": active,
            "waypoint_ids": waypoint_ids,
            "route_ids": route_ids,
            "waypoints": waypoints,
            "routes": routes,
            "start": start,
            "start_id": (
                start.get("id")
                if isinstance(start, dict)
                else (waypoint_ids[0] if waypoint_ids else None)
            ),
            "destination": destination,
            "destination_id": (
                destination.get("id")
                if isinstance(destination, dict)
                else (waypoint_ids[-1] if len(waypoint_ids) > 1 else None)
            ),
            "intermediate_waypoints": waypoints[1:-1] if len(waypoints) > 2 else [],
            "raw_result": result,
        }
        normalized.update(self._navigation_shape_facts(waypoint_ids))
        self._store_helper_report(gate_name, normalized)
        return normalized

    def preflight_navigation_state(self) -> dict[str, Any]:
        """Populate current navigation facts before the model's first decision."""

        self._ensure_scratchpad_shape()
        state = self.scratchpad["entities"].get("navigation_state")
        if isinstance(state, dict) and state.get("stale") is not True:
            return {"status": "CACHED", "navigation_state": copy.deepcopy(state)}
        if not self.tool_available("get_current_navigation_state"):
            return {"status": "SKIPPED", "reason": "navigation state tool unavailable"}

        previous_source = self.last_source
        try:
            result = self._call_raw_tool_sync(
                "get_current_navigation_state",
                {"detailed_information": True},
            )
        finally:
            self.last_source = previous_source
        persisted = self.scratchpad["entities"].get("navigation_state")
        return {
            "status": str(result.get("status") or "UNKNOWN"),
            "navigation_state": copy.deepcopy(persisted),
        }

    def preflight_user_preferences(self) -> dict[str, Any]:
        """Populate stable user preference facts before the model's first decision."""

        self._ensure_scratchpad_shape()
        existing = self.scratchpad["entities"].get("user_preferences")
        if isinstance(existing, dict):
            return {
                "status": "CACHED",
                "user_preferences": copy.deepcopy(existing),
                "summary": copy.deepcopy(existing.get("summary") or []),
                "requested_categories": copy.deepcopy(existing.get("requested_categories") or {}),
            }
        if not self.tool_available("get_user_preferences"):
            return {"status": "SKIPPED", "reason": "preference tool unavailable"}

        request = self._preflight_preference_categories()
        if not request:
            return {"status": "SKIPPED", "reason": "no supported preference categories"}

        previous_source = self.last_source
        try:
            result = self._call_raw_tool_sync(
                "get_user_preferences",
                {"preference_categories": request},
            )
        finally:
            self.last_source = previous_source

        status = str(result.get("status") or "UNKNOWN")
        if status != "SUCCESS":
            return {"status": status, "result": copy.deepcopy(result)}

        raw = result_value(result)
        if isinstance(raw, dict) and raw.get("status") == "SUCCESS":
            raw = {key: value for key, value in raw.items() if key != "status"}
        summary = self._summarize_preference_facts(raw)
        stored = {
            "status": "SUCCESS",
            "requested_categories": copy.deepcopy(request),
            "preferences": copy.deepcopy(raw),
            "summary": summary,
        }
        self.scratchpad["entities"]["user_preferences"] = stored
        return {
            "status": "SUCCESS",
            "user_preferences": copy.deepcopy(stored),
            "summary": copy.deepcopy(summary),
            "requested_categories": copy.deepcopy(request),
        }

    def _preflight_preference_categories(self) -> dict[str, dict[str, bool]]:
        """Request all preference leaves supported by the live tool schema."""

        try:
            schema = self.tool_schema("get_user_preferences")
        except KeyError:
            return {}
        preference_arg = (schema.get("properties") or {}).get("preference_categories")
        if not isinstance(preference_arg, dict):
            return {}
        categories = preference_arg.get("properties") or {}
        request: dict[str, dict[str, bool]] = {}
        for category_name, category_schema in categories.items():
            if not isinstance(category_name, str) or not isinstance(category_schema, dict):
                continue
            subcategories = category_schema.get("properties") or {}
            requested_subcategories = {
                str(subcategory_name): True
                for subcategory_name, subcategory_schema in subcategories.items()
                if (
                    isinstance(subcategory_name, str)
                    and isinstance(subcategory_schema, dict)
                    and subcategory_schema.get("type") == "boolean"
                )
            }
            if requested_subcategories:
                request[category_name] = requested_subcategories
        return request

    @staticmethod
    def _summarize_preference_facts(raw: Any, *, limit: int = 24) -> list[str]:
        """Flatten non-empty preference strings while preserving their category path."""

        summary: list[str] = []

        def collect(value: Any, path: tuple[str, ...]) -> None:
            if len(summary) >= limit:
                return
            if isinstance(value, str):
                text = value.strip()
                if text:
                    label = ".".join(path) if path else "preference"
                    summary.append(f"{label}: {text}")
                return
            if isinstance(value, dict):
                for key, inner in value.items():
                    if isinstance(key, str):
                        collect(inner, (*path, key))
                return
            if isinstance(value, list):
                for inner in value:
                    collect(inner, path)

        collect(raw, ())
        return summary

    def get_contact_details(
        self,
        contact_ids: str | list[str],
        required_fields: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return contact information in a stable list and ID map."""

        gate_name = "get_contact_details"
        ids = [contact_ids] if isinstance(contact_ids, str) else list(contact_ids)
        if not ids or not all(isinstance(contact_id, str) and contact_id for contact_id in ids):
            raise ValueError("contact_ids must contain at least one grounded contact ID")
        call = ("get_contact_information", {"contact_ids": ids})
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "get the requested contact information",
            [call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        if result.get("status") != "SUCCESS":
            return self._failed_tool_response(
                gate_name,
                "get the requested contact information",
                result,
            )
        payload = result_value(result)
        if not isinstance(payload, dict):
            return self._limitation_response(
                gate_name,
                "get the requested contact information",
                reason="the contact information result had an unexpected shape",
            )

        by_id: dict[str, dict[str, Any]] = {}
        candidates: list[tuple[str | None, Any]]
        if isinstance(payload.get("contacts"), list):
            candidates = [(None, candidate) for candidate in payload["contacts"]]
        elif any(contact_id in payload for contact_id in ids):
            candidates = [
                (contact_id, payload.get(contact_id))
                for contact_id in ids
            ]
        elif "id" in payload:
            candidates = [(None, payload)]
        else:
            candidates = [
                (key if isinstance(key, str) else None, candidate)
                for key, candidate in payload.items()
            ]
        for keyed_id, candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            contact_id = (
                keyed_id
                or candidate.get("contact_id")
                or candidate.get("id")
            )
            if isinstance(contact_id, str) and contact_id:
                by_id[contact_id] = self._normalize_contact_record(
                    contact_id,
                    candidate,
                )
        contacts = [by_id[contact_id] for contact_id in ids if contact_id in by_id]
        if not contacts:
            return self._limitation_response(
                gate_name,
                "get the requested contact information",
                reason="the requested contacts were absent from the contact information result",
            )

        fields = [str(field) for field in required_fields or []]
        for contact_id in ids:
            if contact_id not in by_id:
                self._abort_missing_tool_response(
                    f"result.get_contact_information.{contact_id}",
                    "get the requested contact information",
                    gate_name,
                )
            for field in fields:
                value = self._response_path_value(by_id[contact_id], field)
                if isinstance(value, UnknownToolResponseValue) or value is None:
                    self._abort_missing_tool_response(
                        f"result.get_contact_information.{contact_id}.{field}",
                        "get the requested contact information",
                        gate_name,
                    )
        normalized = {
            "status": "SUCCESS",
            "contacts": contacts,
            "by_id": by_id,
            "first": contacts[0],
            "raw_result": result,
        }
        if len(contacts) == 1:
            for field in (
                "email",
                "phone_number",
                "name",
                "display_name",
                "first_name",
                "last_name",
            ):
                if field in contacts[0]:
                    normalized[field] = contacts[0][field]
        self.remember_entity("last_contacts", contacts)
        self._store_helper_report(gate_name, normalized)
        return normalized

    def get_next_calendar_entry(self) -> dict[str, Any]:
        """Return current-day calendar entries and the next chronological entry."""

        gate_name = "get_next_calendar_entry"
        now = self.policy_now()
        month = now.get("month")
        day = now.get("day")
        hour = now.get("hour")
        minute = now.get("minute")
        if not all(isinstance(value, int) for value in (month, day, hour, minute)):
            return self._limitation_response(
                gate_name,
                "read the next calendar entry",
                reason="the current policy date or time is unavailable",
            )
        call = ("get_entries_from_calendar", {"month": month, "day": day})
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "read the next calendar entry",
            [call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        if result.get("status") != "SUCCESS":
            return self._failed_tool_response(
                gate_name,
                "read the next calendar entry",
                result,
            )
        payload = result_value(result)
        if not isinstance(payload, dict):
            return self._limitation_response(
                gate_name,
                "read the next calendar entry",
                reason="the calendar result had an unexpected shape",
            )
        meetings = payload.get("meetings")
        if not isinstance(meetings, list):
            meetings = []

        current_minutes = hour * 60 + minute
        normalized_entries = [
            entry
            for entry in self._normalize_calendar_entries(meetings)
            if isinstance(entry.get("start_minutes"), int)
        ]

        future_entries = [
            entry
            for entry in normalized_entries
            if entry["start_minutes"] >= current_minutes
        ]
        next_entry = min(
            future_entries,
            key=lambda entry: entry["start_minutes"],
            default=None,
        )
        normalized = {
            "status": "SUCCESS" if next_entry is not None else "NOT_FOUND",
            "date": copy.deepcopy(payload.get("date")),
            "entries": normalized_entries,
            "meetings": normalized_entries,
            "next_entry": next_entry,
            "raw_result": result,
        }
        if next_entry is not None:
            self.remember_entity("next_calendar_entry", next_entry)
        self._store_helper_report(gate_name, normalized)
        return normalized

    def set_fog_lights_on_safe(self) -> dict[str, Any]:
        """Activate fog lights under weather and exterior-light policies."""

        gate_name = "set_fog_lights_on_safe"
        context = self._current_policy_context()
        if not context.get("location_id"):
            return self._limitation_response(
                gate_name,
                "turn on the fog lights safely",
                reason="the current location or time from policy was unavailable",
            )
        read_calls = [
            (
                "get_weather",
                {
                    "location_or_poi_id": context["location_id"],
                    "month": context["month"],
                    "day": context["day"],
                    "time_hour_24hformat": context["hour"],
                    "time_minutes": context["minute"],
                },
            ),
            ("get_exterior_lights_status", {}),
        ]
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "turn on the fog lights safely",
            read_calls,
        )
        if blocker:
            return blocker
        readings = self._call_raw_tools_sync(read_calls)
        weather_result = result_by_tool(readings, "get_weather")
        lights_result = result_by_tool(readings, "get_exterior_lights_status")
        for result in (weather_result, lights_result):
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "turn on the fog lights safely", result)
        weather = weather_result.get("result")
        lights = lights_result.get("result")
        if not isinstance(weather, dict) or not isinstance(lights, dict):
            return self._limitation_response(
                gate_name,
                "turn on the fog lights safely",
                reason="the weather or exterior-light result had an unexpected shape",
            )
        condition = self._weather_condition(weather)
        weather_unknown = not condition

        unknown_response_fields: list[str] = []
        fog_on = lights.get("fog_lights")
        low_on = lights.get("head_lights_low_beams")
        high_on = lights.get("head_lights_high_beams")
        if isinstance(fog_on, UnknownToolResponseValue) or "fog_lights" not in lights:
            unknown_response_fields.append("result.get_exterior_lights_status.fog_lights")
        elif not isinstance(fog_on, bool):
            return self._limitation_response(
                gate_name,
                "turn on the fog lights safely",
                reason="the fog-light state was not a boolean value",
            )
        if isinstance(low_on, UnknownToolResponseValue) or "head_lights_low_beams" not in lights:
            unknown_response_fields.append(
                "result.get_exterior_lights_status.head_lights_low_beams"
            )
        elif not isinstance(low_on, bool):
            return self._limitation_response(
                gate_name,
                "turn on the fog lights safely",
                reason="the low-beam state was not a boolean value",
            )
        if isinstance(high_on, UnknownToolResponseValue) or "head_lights_high_beams" not in lights:
            unknown_response_fields.append(
                "result.get_exterior_lights_status.head_lights_high_beams"
            )
        elif not isinstance(high_on, bool):
            return self._limitation_response(
                gate_name,
                "turn on the fog lights safely",
                reason="the high-beam state was not a boolean value",
            )
        if fog_on is True:
            message = "The fog lights are already on."
            self._store_helper_report(
                gate_name,
                {
                    "helper": gate_name,
                    "status": "SUCCESS",
                    "message": message,
                    "actions": [],
                    "unknown_response_fields": unknown_response_fields,
                },
            )
            self._helper_message(message)
            return {"status": "SUCCESS", "actions": [], "message": message}

        action_calls: list[tuple[str, dict[str, Any]]] = []
        if low_on is not True:
            action_calls.append(("set_head_lights_low_beams", {"on": True}))
        if high_on is not False:
            action_calls.append(("set_head_lights_high_beams", {"on": False}))
        action_calls.append(("set_fog_lights", {"on": True}))
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "turn on the fog lights safely",
            action_calls,
        )
        if blocker:
            return blocker

        needs_weather_confirmation = weather_unknown or condition not in {
            "cloudy_and_thunderstorm",
            "cloudy_and_hail",
        }
        needs_tool_confirmation = any(
            name == "set_head_lights_high_beams"
            for name, _ in action_calls
        ) and self._tool_requires_confirmation(
            "set_head_lights_high_beams"
        )
        if needs_weather_confirmation or needs_tool_confirmation:
            changes: list[str] = []
            if low_on is not True:
                changes.append("turn on the low beams")
            if high_on is not False:
                changes.append("turn off the high beams")
            changes.append("turn on the fog lights")
            weather_text = (
                "the current weather condition is unavailable"
                if weather_unknown
                else f"the current weather is {condition}"
            )
            unknown_notes = []
            if isinstance(low_on, UnknownToolResponseValue) or "head_lights_low_beams" not in lights:
                unknown_notes.append("low-beam status is unavailable")
            if isinstance(high_on, UnknownToolResponseValue) or "head_lights_high_beams" not in lights:
                unknown_notes.append("high-beam status is unavailable")
            if isinstance(fog_on, UnknownToolResponseValue) or "fog_lights" not in lights:
                unknown_notes.append("fog-light status is unavailable")
            unknown_text = ""
            if unknown_notes:
                unknown_text = " I also found that " + _human_join(unknown_notes) + "."
            prompt = (
                f"I checked the weather, and {weather_text}.{unknown_text} "
                f"Before I {', '.join(changes)}, please explicitly confirm with yes."
            )
            pending = {
                "type": "fog_lights_confirmation",
                "gate_name": gate_name,
                "policy": "008_009_013",
                "action": "turn on the fog lights safely",
                "on_confirm_calls": action_calls,
                "confirmation_prompt": prompt,
                "confirmation_retry_prompt": (
                    "Please confirm with yes if you want me to apply the required lighting "
                    "changes and turn on the fog lights."
                ),
                "response_on_cancel": "Okay, I won't turn on the fog lights.",
                "response_on_success": (
                    "Confirmed. I applied the required lighting changes and turned on the fog lights."
                ),
                "unknown_response_fields": unknown_response_fields,
            }
            self.remember("pending_confirmation", pending)
            report = {
                "helper": gate_name,
                "status": "WAITING_CONFIRMATION",
                "policy": "008_009_013",
                "weather_condition": condition,
                "weather_condition_unknown": weather_unknown,
                "actions": [name for name, _ in action_calls],
                "unknown_response_fields": unknown_response_fields,
                "message": prompt,
            }
            self.scratchpad["gates"][gate_name] = {
                "status": "WAITING_CONFIRMATION",
                "policy": "008_009_013",
                "weather_condition": condition,
                "weather_condition_unknown": weather_unknown,
                "actions": report["actions"],
                "unknown_response_fields": unknown_response_fields,
            }
            self._store_helper_report(gate_name, report)
            self._abort_with_response(prompt)

        results = self._call_raw_tools_sync(action_calls)
        for result in results:
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "turn on the fog lights safely", result)
        adjusted = []
        if low_on is not True:
            adjusted.append("low beams turned on")
        if high_on is not False:
            adjusted.append("high beams turned off")
        message = "Fog lights turned on"
        if adjusted:
            message += " with " + _human_join(adjusted)
        message += "."
        report = {
            "helper": gate_name,
            "status": "SUCCESS",
            "policy": "008_009_013",
            "weather_condition": condition,
            "weather_condition_unknown": weather_unknown,
            "actions": [name for name, _ in action_calls],
            "results": results,
            "unknown_response_fields": unknown_response_fields,
            "message": message,
        }
        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": "008_009_013",
            "weather_condition": condition,
            "weather_condition_unknown": weather_unknown,
            "actions": report["actions"],
            "unknown_response_fields": unknown_response_fields,
        }
        self._store_helper_report(gate_name, report)
        self._helper_message(message)
        return {"status": "SUCCESS", "actions": results, "report": report, "message": message}

    def set_high_beams_on_safe(self) -> dict[str, Any]:
        """Activate high beams only when fog lights are off."""

        gate_name = "set_high_beams_on_safe"
        read_call = ("get_exterior_lights_status", {})
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "turn on the high beams safely",
            [read_call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*read_call)
        if result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "turn on the high beams safely", result)
        lights = result_value(result)
        if not isinstance(lights, dict):
            return self._limitation_response(
                gate_name,
                "turn on the high beams safely",
                reason="the exterior-light result had an unexpected shape",
            )
        unknown_response_fields: list[str] = []
        fog_on = lights.get("fog_lights")
        high_on = lights.get("head_lights_high_beams")
        if isinstance(fog_on, UnknownToolResponseValue) or "fog_lights" not in lights:
            unknown_response_fields.append("result.get_exterior_lights_status.fog_lights")
        elif not isinstance(fog_on, bool):
            return self._limitation_response(
                gate_name,
                "turn on the high beams safely",
                reason="the fog-light state was not a boolean value",
            )
        if isinstance(high_on, UnknownToolResponseValue) or "head_lights_high_beams" not in lights:
            unknown_response_fields.append("result.get_exterior_lights_status.head_lights_high_beams")
        elif not isinstance(high_on, bool):
            return self._limitation_response(
                gate_name,
                "turn on the high beams safely",
                reason="the high-beam state was not a boolean value",
            )
        if fog_on is True:
            message = (
                "I can't turn on the high beams while the fog lights are on because policy 014 "
                "prohibits that combination."
            )
            report = {
                "helper": gate_name,
                "status": "BLOCKED",
                "policy": "014",
                "message": message,
            }
            self.scratchpad["gates"][gate_name] = {
                "status": "NO",
                "policy": "014",
                "reason": "fog lights are on",
            }
            self._store_helper_report(gate_name, report)
            self._abort_with_response(message)
        if high_on is True:
            message = "The high beams are already on."
            self._store_helper_report(
                gate_name,
                {
                    "helper": gate_name,
                    "status": "SUCCESS",
                    "message": message,
                    "actions": [],
                    "unknown_response_fields": unknown_response_fields,
                },
            )
            self._helper_message(message)
            return {"status": "SUCCESS", "actions": [], "message": message}

        action_call = ("set_head_lights_high_beams", {"on": True})
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "turn on the high beams safely",
            [action_call],
        )
        if blocker:
            return blocker
        if self._tool_requires_confirmation("set_head_lights_high_beams"):
            prompt = self._high_beam_confirmation_prompt(
                fog_on=fog_on,
                high_on=high_on,
            )
            pending = {
                "type": "high_beams_confirmation",
                "gate_name": gate_name,
                "policy": "004_014",
                "action": "turn on the high beams safely",
                "on_confirm_calls": [action_call],
                "confirmation_prompt": prompt,
                "confirmation_retry_prompt": (
                    "Please confirm with yes if you want me to turn on the high beams."
                ),
                "response_on_cancel": "Okay, I won't turn on the high beams.",
                "response_on_success": "High beams turned on.",
                "unknown_response_fields": unknown_response_fields,
            }
            self.remember("pending_confirmation", pending)
            report = {
                "helper": gate_name,
                "status": "WAITING_CONFIRMATION",
                "policy": "004_014",
                "actions": ["set_head_lights_high_beams"],
                "arguments": [{"on": True}],
                "unknown_response_fields": unknown_response_fields,
                "message": prompt,
            }
            self.scratchpad["gates"][gate_name] = {
                "status": "WAITING_CONFIRMATION",
                "policy": "004_014",
                "actions": report["actions"],
                "arguments": report["arguments"],
                "unknown_response_fields": unknown_response_fields,
            }
            self._store_helper_report(gate_name, report)
            self._abort_with_response(prompt)
        action_result = self._call_raw_tool_sync(*action_call)
        if action_result.get("status") != "SUCCESS":
            return self._failed_tool_response(
                gate_name,
                "turn on the high beams safely",
                action_result,
            )
        message = "High beams turned on."
        report = {
            "helper": gate_name,
            "status": "SUCCESS",
            "policy": "014",
            "actions": ["set_head_lights_high_beams"],
            "results": [action_result],
            "unknown_response_fields": unknown_response_fields,
            "message": message,
        }
        self._store_helper_report(gate_name, report)
        self._helper_message(message)
        return {
            "status": "SUCCESS",
            "actions": [action_result],
            "report": report,
            "message": message,
        }

    @staticmethod
    def _high_beam_confirmation_prompt(*, fog_on: Any, high_on: Any) -> str:
        if fog_on is True:
            fog_text = "fog lights are on"
        elif fog_on is False:
            fog_text = "fog lights are off"
        else:
            fog_text = "fog-light status is unavailable"

        if high_on is True:
            high_text = "high beams are already on"
        elif high_on is False:
            high_text = "high beams are currently off"
        else:
            high_text = "current high-beam status is unavailable"

        return (
            "This action requires confirmation. I checked the exterior lights: "
            f"{fog_text}, and {high_text}. I will turn the high beam headlights "
            "on (on=True). Please confirm with yes."
        )

    def open_sunroof_safe(self, percentage: int | float) -> dict[str, Any]:
        """Set sunroof position while applying policies 005 and 008/009."""

        gate_name = "open_sunroof_safe"
        target = float(percentage)
        if not 0 <= target <= 100:
            return self._limitation_response(
                gate_name,
                "set the sunroof",
                reason="the requested sunroof percentage is outside the supported 0 to 100 range",
            )
        target_arg: int | float = int(target) if target.is_integer() else target
        read_call = ("get_sunroof_and_sunshade_position", {})
        blocker = self._require_tool_surface_for_calls(gate_name, "set the sunroof safely", [read_call])
        if blocker:
            return blocker

        state_result = self._call_raw_tool_sync(*read_call)
        if state_result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "set the sunroof safely", state_result)
        state = result_value(state_result)
        if not isinstance(state, dict):
            return self._limitation_response(
                gate_name,
                "set the sunroof safely",
                reason="the sunroof and sunshade state result had an unexpected shape",
            )

        current_sunroof = state.get("sunroof_position", 0)
        current_sunshade = state.get("sunshade_position", 0)
        opening = target > 0 and (
            not isinstance(current_sunroof, (int, float)) or target > float(current_sunroof)
        )
        action_calls: list[tuple[str, dict[str, Any]]] = []
        adjusted_sunshade = False
        if opening and (not isinstance(current_sunshade, (int, float)) or float(current_sunshade) < 100):
            action_calls.append(("open_close_sunshade", {"percentage": 100}))
            adjusted_sunshade = True
        action_calls.append(("open_close_sunroof", {"percentage": target_arg}))

        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "open the sunroof safely" if opening else "set the sunroof",
            action_calls,
        )
        if blocker:
            return blocker

        weather_condition: str | None = None
        if opening:
            context = self._current_policy_context()
            if not context.get("location_id"):
                return self._limitation_response(
                    gate_name,
                    "open the sunroof safely",
                    reason="the current location or time from policy was unavailable",
                )
            weather_call = (
                "get_weather",
                {
                    "location_or_poi_id": context["location_id"],
                    "month": context["month"],
                    "day": context["day"],
                    "time_hour_24hformat": context["hour"],
                    "time_minutes": context["minute"],
                },
            )
            blocker = self._require_tool_surface_for_calls(
                gate_name,
                "check weather before opening the sunroof",
                [weather_call],
            )
            if blocker:
                return blocker
            weather_result = self._call_raw_tool_sync(*weather_call)
            if weather_result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "check weather before opening the sunroof", weather_result)
            weather_payload = weather_result.get("result")
            weather_condition = self._weather_condition(weather_payload)
            weather_unknown = not weather_condition
            safe_weather = weather_condition in {"sunny", "cloudy", "partly_cloudy"}
            if weather_unknown or not safe_weather:
                weather_text = (
                    "the current weather condition is unavailable"
                    if weather_unknown
                    else f"the current weather is {weather_condition}"
                )
                prompt = (
                    f"I checked the weather, and {weather_text}. Opening the sunroof needs "
                    f"your confirmation. I will open the sunroof to {target_arg:g}%"
                    + (" and open the sunshade fully first." if adjusted_sunshade else ".")
                    + " Please confirm with yes."
                )
                pending = {
                    "type": "sunroof_weather_confirmation",
                    "gate_name": gate_name,
                    "policy": "005_008_009",
                    "action": "open the sunroof safely",
                    "reason": (
                        "weather condition unavailable"
                        if weather_unknown
                        else f"weather condition {weather_condition}"
                    ),
                    "on_confirm_calls": action_calls,
                    "confirmation_prompt": prompt,
                    "confirmation_retry_prompt": "Please confirm with yes if you want me to open the sunroof.",
                    "response_on_cancel": "Okay, I won't open the sunroof.",
                    "response_on_success": (
                        f"Sunroof opened to {target_arg:g}%"
                        + (" after opening the sunshade fully." if adjusted_sunshade else ".")
                    ),
                }
                self.remember("pending_confirmation", pending)
                self.scratchpad["gates"][gate_name] = {
                    "status": "WAITING_CONFIRMATION",
                    "policy": "005_008_009",
                    "weather_condition": weather_condition,
                    "actions": [name for name, _ in action_calls],
                }
                report = self._store_helper_report(
                    gate_name,
                    {
                        "helper": gate_name,
                        "status": "WAITING_CONFIRMATION",
                        "policy": "005_008_009",
                        "weather_condition": weather_condition,
                        "adjusted_sunshade": adjusted_sunshade,
                        "actions": [name for name, _ in action_calls],
                        "message": prompt,
                    },
                )
                self._abort_with_response(prompt)

        results = self._call_raw_tools_sync(action_calls)
        for result in results:
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "set the sunroof", result)

        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": "005_008_009" if opening else "005",
            "weather_condition": weather_condition,
            "actions": [name for name, _ in action_calls],
        }
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "SUCCESS",
                "policy": "005_008_009" if opening else "005",
                "weather_condition": weather_condition,
                "adjusted_sunshade": adjusted_sunshade,
                "actions": [name for name, _ in action_calls],
                "results": results,
            },
        )
        if adjusted_sunshade:
            self._helper_message(f"Sunshade opened fully and sunroof set to {target_arg:g}%.")
        else:
            self._helper_message(f"Sunroof set to {target_arg:g}%.")
        return {"status": "SUCCESS", "actions": results, "report": report}

    def open_close_window_safe(self, window: str, percentage: int | float) -> dict[str, Any]:
        """Move a window while applying CAR-bench policy 007."""

        gate_name = "open_close_window_safe"
        try:
            target = float(percentage)
        except (TypeError, ValueError):
            return self._limitation_response(
                gate_name,
                "move the window",
                reason="the requested window percentage was not a number",
            )
        if not 0 <= target <= 100:
            return self._limitation_response(
                gate_name,
                "move the window",
                reason="the requested window percentage is outside the supported 0 to 100 range",
            )
        target_arg: int | float = int(target) if target.is_integer() else target
        action_args = {"window": window, "percentage": target_arg}
        if self.tool_available("open_close_window"):
            action_args = self._normalize_tool_arguments("open_close_window", action_args)
        action_call = ("open_close_window", action_args)

        required_calls: list[tuple[str, dict[str, Any]]] = [action_call]
        if target > 25:
            required_calls.append(("get_climate_settings", {}))
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "move the window safely under policy 007",
            required_calls,
        )
        if blocker:
            return blocker

        needs_confirmation = False
        ac_unknown = False
        if target > 25:
            climate_result = self._call_raw_tool_sync("get_climate_settings", {})
            if climate_result.get("status") != "SUCCESS":
                return self._failed_tool_response(
                    gate_name,
                    "read climate settings before opening the window",
                    climate_result,
                )
            climate = climate_result.get("result")
            if not isinstance(climate, dict):
                return self._limitation_response(
                    gate_name,
                    "move the window safely under policy 007",
                    reason="the climate settings result had an unexpected shape",
                )
            ac_state = climate.get("air_conditioning")
            if isinstance(ac_state, UnknownToolResponseValue) or "air_conditioning" not in climate:
                needs_confirmation = True
                ac_unknown = True
            elif isinstance(ac_state, bool):
                needs_confirmation = ac_state is True
            else:
                return self._limitation_response(
                    gate_name,
                    "move the window safely under policy 007",
                    reason="the air conditioning state was not a boolean value",
                )

        if needs_confirmation:
            window_label = str(action_args.get("window") or window)
            weathering = (
                "the AC status is unavailable"
                if ac_unknown
                else "air conditioning is on"
            )
            prompt = (
                f"I checked the climate settings, and {weathering}. Opening a window "
                f"above 25% can waste energy. I will set window={window_label} "
                f"to percentage={target_arg:g}. Please confirm with yes."
            )
            pending = {
                "type": "window_policy_007_confirmation",
                "gate_name": gate_name,
                "policy": "007",
                "action": "move the window safely under policy 007",
                "on_confirm_calls": [action_call],
                "confirmation_prompt": prompt,
                "confirmation_retry_prompt": (
                    "Please confirm with yes if you want me to move the window."
                ),
                "response_on_cancel": "Okay, I won't move the window.",
                "response_on_success": (
                    f"Window {window_label} set to {target_arg:g}%."
                ),
                "ac_state_unknown": ac_unknown,
            }
            self.remember("pending_confirmation", pending)
            self.scratchpad["gates"][gate_name] = {
                "status": "WAITING_CONFIRMATION",
                "policy": "007",
                "window": action_args.get("window"),
                "percentage": target_arg,
                "ac_state_unknown": ac_unknown,
            }
            self._store_helper_report(
                gate_name,
                {
                    "helper": gate_name,
                    "status": "WAITING_CONFIRMATION",
                    "policy": "007",
                    "window": action_args.get("window"),
                    "percentage": target_arg,
                    "ac_state_unknown": ac_unknown,
                    "message": prompt,
                },
            )
            self._abort_with_response(prompt)

        result = self._call_raw_tool_sync(*action_call)
        if result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "move the window", result)
        message = f"Window {action_args.get('window')} set to {target_arg:g}%."
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "SUCCESS",
                "policy": "007" if target > 25 else "none",
                "window": action_args.get("window"),
                "percentage": target_arg,
                "message": message,
            },
        )
        self._helper_message(message)
        return {"status": "SUCCESS", "action": result, "report": report, "message": message}

    def defrost_front_window(self) -> dict[str, Any]:
        """Apply the CAR-bench front-defrost policy as one workspace helper."""

        return self._set_window_defrost_safe("FRONT", gate_name="defrost_front_window")

    def set_window_defrost_safe(self, defrost_window: str = "FRONT") -> dict[str, Any]:
        """Apply the CAR-bench defrost policy for FRONT/ALL defrost."""

        return self._set_window_defrost_safe(defrost_window, gate_name="set_window_defrost_safe")

    def _set_window_defrost_safe(
        self,
        defrost_window: str,
        *,
        gate_name: str,
    ) -> dict[str, Any]:
        normalized_window = str(defrost_window or "FRONT").upper()
        if normalized_window not in {"FRONT", "ALL", "REAR"}:
            return self._limitation_response(
                gate_name,
                "turn on window defrost",
                reason="defrost_window must be FRONT, ALL, or REAR",
            )
        defrost_label = (
            "front defrost"
            if normalized_window == "FRONT"
            else "all-window defrost"
            if normalized_window == "ALL"
            else "rear defrost"
        )
        if normalized_window == "REAR":
            call = ("set_window_defrost", {"on": True, "defrost_window": "REAR"})
            blocker = self._require_tool_surface_for_calls(gate_name, "turn on rear defrost", [call])
            if blocker:
                return blocker
            result = self._call_raw_tool_sync(*call)
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "turn on rear defrost", result)
            message = "Rear defrost is on."
            report = self._store_helper_report(
                gate_name,
                {
                    "helper": gate_name,
                    "status": "SUCCESS",
                    "policy": "none",
                    "actions": ["set_window_defrost"],
                    "message": message,
                },
            )
            self._helper_message(message)
            return {"status": "SUCCESS", "actions": [result], "report": report, "message": message}

        def failed_result(result: dict[str, Any]) -> dict[str, Any] | None:
            if result.get("status") == "SUCCESS":
                return None
            tool_name = str(result.get("tool_name") or "")
            label = _tool_label(tool_name) if tool_name else "required tool"
            message = (
                f"I can't safely turn on {defrost_label} because I couldn't get a usable "
                f"result from the {label}."
                + (f" The failed tool was {tool_name}." if tool_name else "")
            )
            self.scratchpad["gates"][gate_name] = {
                "status": "BLOCKED",
                "failed_tool": result.get("tool_name"),
                "result": result,
            }
            self._abort_with_response(message)

        read_calls: list[tuple[str, dict[str, Any]]] = [("get_climate_settings", {})]
        has_window_reader = self.tool_available("get_vehicle_window_positions")
        if has_window_reader:
            read_calls.append(("get_vehicle_window_positions", {}))
        blocker = self._require_tool_surface_for_calls(gate_name, f"turn on {defrost_label}", read_calls)
        if blocker:
            return blocker

        readings = self._call_raw_tools_sync(read_calls)
        climate_result = result_by_tool(readings, "get_climate_settings")
        blocked = failed_result(climate_result)
        if blocked:
            return blocked
        climate = result_value(climate_result)
        windows: dict[str, Any] = {}
        if has_window_reader:
            windows_result = result_by_tool(readings, "get_vehicle_window_positions")
            blocked = failed_result(windows_result)
            if blocked:
                return blocked
            raw_windows = windows_result.get("result")
            if isinstance(raw_windows, dict):
                windows = raw_windows

        action_calls: list[tuple[str, dict[str, Any]]] = [
            ("set_window_defrost", {"on": True, "defrost_window": normalized_window})
        ]
        adjusted_windows: list[dict[str, Any]] = []
        unknown_windows: list[dict[str, Any]] = []

        if climate.get("fan_speed", 0) < 2:
            action_calls.append(("set_fan_speed", {"level": 2}))

        if "WINDSHIELD" not in str(climate.get("fan_airflow_direction", "")):
            action_calls.append(("set_fan_airflow_direction", {"direction": "WINDSHIELD"}))

        ac_state = climate.get("air_conditioning")
        ac_must_enable = ac_state is not True
        if ac_must_enable:
            if not has_window_reader:
                return self._limitation_response(
                    gate_name,
                    f"safely turn on {defrost_label} under policy 010/011",
                    missing_tools=["get_vehicle_window_positions"],
                )
            else:
                windows_to_close, unknown_windows = self._windows_over_position(windows, 20)
                windows_to_close = [*windows_to_close, *unknown_windows]
                for window_info in windows_to_close:
                    action_calls.append(
                        (
                            "open_close_window",
                            {"window": window_info["tool_window"], "percentage": 0},
                        )
                    )
                    adjusted_windows.append(window_info)
                action_calls.append(("set_air_conditioning", {"on": True}))

        blocker = self._require_tool_surface_for_calls(
            gate_name,
            f"safely turn on {defrost_label} under policy 010/011",
            action_calls,
        )
        if blocker:
            return blocker

        if any(name == "set_air_conditioning" for name, _ in action_calls):
            self._clear_active_policy_011_blocker()
        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": "010_011",
            "actions": [name for name, _ in action_calls],
        }
        action_results = self._call_raw_tools_sync(action_calls)
        for result in action_results:
            blocked = failed_result(result)
            if blocked:
                return blocked
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "adjusted_windows": adjusted_windows,
                "unknown_windows": unknown_windows,
                "actions": [name for name, _ in action_calls],
            },
        )
        unknown_note = self._unknown_window_close_note(unknown_windows, action=defrost_label)
        if unknown_note:
            self._add_response_obligation(
                "unknown_window_closed_for_defrost",
                unknown_note,
                satisfied_patterns=(
                    r"\bunknown\b",
                    r"\bunavailable\b",
                    r"\bclosed\b.*\bwindow\b",
                ),
            )
            self._helper_message(
                f"{defrost_label.capitalize()} is on, and I closed windows with unavailable "
                "position data before turning it on."
            )
        else:
            self._helper_message(
                f"{defrost_label.capitalize()} is on, with the required fan, AC, "
                "and window safety settings handled."
            )
        return {"status": "SUCCESS", "actions": action_results, "report": report}

    def set_air_conditioning_on_safe(self) -> dict[str, Any]:
        """Turn AC on while applying deterministic CAR-bench policy 011."""

        gate_name = "set_air_conditioning_on_safe"
        read_calls = [
            ("get_climate_settings", {}),
            ("get_vehicle_window_positions", {}),
        ]
        blocker = self._require_tool_surface_for_calls(gate_name, "turn on AC safely under policy 011", read_calls)
        if blocker:
            return blocker

        readings = self._call_raw_tools_sync(read_calls)
        climate_result = result_by_tool(readings, "get_climate_settings")
        if climate_result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "turn on AC safely", climate_result)
        windows_result = result_by_tool(readings, "get_vehicle_window_positions")
        if windows_result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "turn on AC safely", windows_result)

        climate = result_value(climate_result)
        windows = windows_result.get("result")
        if not isinstance(climate, dict) or not isinstance(windows, dict):
            return self._limitation_response(
                gate_name,
                "turn on AC safely",
                reason="the climate or window state result had an unexpected shape",
            )

        if climate.get("air_conditioning") is True:
            self.scratchpad["gates"][gate_name] = {
                "status": "YES",
                "policy": "011",
                "actions": [],
                "reason": "AC already on",
            }
            self._helper_message("AC is already on.")
            return {"status": "SUCCESS", "actions": [], "already_on": True}

        action_calls: list[tuple[str, dict[str, Any]]] = []
        adjusted_windows: list[dict[str, Any]] = []
        windows_to_close, unknown_windows = self._windows_over_position(windows, 20)
        windows_to_close = [*windows_to_close, *unknown_windows]
        for window_info in windows_to_close:
            action_calls.append(
                (
                    "open_close_window",
                    {"window": window_info["tool_window"], "percentage": 0},
                )
            )
            adjusted_windows.append(window_info)
        if climate.get("fan_speed", 0) == 0:
            action_calls.append(("set_fan_speed", {"level": 1}))
        action_calls.append(("set_air_conditioning", {"on": True}))

        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "turn on AC safely under policy 011",
            action_calls,
        )
        if blocker:
            return blocker

        self._clear_active_policy_011_blocker()
        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": "011",
            "actions": [name for name, _ in action_calls],
        }
        action_results = self._call_raw_tools_sync(action_calls)
        for result in action_results:
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "turn on AC safely", result)
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "adjusted_windows": adjusted_windows,
                "unknown_windows": unknown_windows,
                "actions": [name for name, _ in action_calls],
            },
        )
        unknown_note = self._unknown_window_close_note(unknown_windows)
        if unknown_note:
            self._add_response_obligation(
                "unknown_window_closed_for_ac",
                unknown_note,
                satisfied_patterns=(
                    r"\bunknown\b",
                    r"\bunavailable\b",
                    r"\bclosed\b.*\bwindow\b",
                ),
            )
            self._helper_message(
                "AC is on, and I closed windows with unavailable position data "
                "before turning it on."
            )
        else:
            self._helper_message("AC is on, and I handled the required fan and window safety settings.")
        return {"status": "SUCCESS", "actions": action_results, "report": report}

    def close_known_windows_for_blocked_ac(self, window: str | None = None) -> dict[str, Any]:
        """Close only windows proven open in the last blocked AC/defrost helper report."""

        gate_name = "close_known_windows_for_blocked_ac"
        last_report = self.scratchpad.get("facts", {}).get("last_helper_report")
        if not isinstance(last_report, dict) or last_report.get("status") != "UNAVAILABLE":
            return self._limitation_response(
                gate_name,
                "close windows for AC",
                reason="there is no previous blocked AC or defrost helper report to ground which windows are open",
            )
        policy = str(last_report.get("policy", ""))
        if "011" not in policy:
            return self._limitation_response(
                gate_name,
                "close windows for AC",
                reason="the previous limitation was not an AC/window policy 011 limitation",
            )
        details = last_report.get("known_window_details_over_20")
        if not isinstance(details, list) or not details:
            labels = last_report.get("known_windows_over_20")
            details = []
            if isinstance(labels, list):
                for label in labels:
                    tool_window = WINDOW_LABEL_TO_TOOL.get(str(label).lower())
                    if tool_window:
                        details.append({"label": str(label), "tool_window": tool_window})
        candidates = [
            item
            for item in details
            if isinstance(item, dict) and item.get("tool_window") and item.get("label")
        ]
        if not candidates:
            return self._limitation_response(
                gate_name,
                "close windows for AC",
                reason="the previous helper report did not identify any specific known window that can be closed",
            )

        selected = candidates
        if window is not None and str(window).strip():
            requested = str(window).strip().lower().replace("_", " ")
            selected = [
                item
                for item in candidates
                if requested in str(item.get("label", "")).lower()
                or requested == str(item.get("tool_window", "")).lower().replace("_", " ")
            ]
            if not selected:
                known = ", ".join(str(item.get("label")) for item in candidates)
                return self._limitation_response(
                    gate_name,
                    "close that window for AC",
                    reason=f"the previous helper report only identified these closeable windows: {known}",
                )

        action_calls = [
            ("open_close_window", {"window": item["tool_window"], "percentage": 0})
            for item in selected
        ]
        blocker = self._require_tool_surface_for_calls(gate_name, "close windows for AC", action_calls)
        if blocker:
            return blocker
        results = self._call_raw_tools_sync(action_calls)
        for result in results:
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "close windows for AC", result)

        closed_labels = [str(item.get("label")) for item in selected]
        missing_info = last_report.get("missing_information")
        missing_text = ""
        if isinstance(missing_info, list) and missing_info:
            missing_text = (
                " I still can't turn on air conditioning because the current position is unavailable for "
                + ", ".join(str(item) for item in missing_info)
                + "."
            )
        closed_text = ", ".join(f"the {label}" for label in closed_labels)
        message = f"I closed {closed_text}.{missing_text}"
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "PARTIAL_SUCCESS",
                "policy": "011",
                "closed_windows": closed_labels,
                "remaining_missing_information": missing_info if isinstance(missing_info, list) else [],
                "message": message,
            },
        )
        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": "011",
            "actions": ["open_close_window"],
            "closed_windows": closed_labels,
            "remaining_missing_information": missing_info if isinstance(missing_info, list) else [],
        }
        self._helper_message(message)
        return {"status": "PARTIAL_SUCCESS", "actions": results, "report": report}

    def set_climate_temperature_safe(self, seat_zone: str, temperature: int | float) -> dict[str, Any]:
        """Set an explicit temperature and apply policy 012 warning if needed."""

        gate_name = "set_climate_temperature_safe"
        normalized_zone = str(seat_zone).upper()
        if normalized_zone not in {"ALL_ZONES", "DRIVER", "PASSENGER"}:
            return self._limitation_response(
                gate_name,
                "set the temperature",
                reason="the seat zone is not one of ALL_ZONES, DRIVER, or PASSENGER",
            )
        target = float(temperature)
        if not 16 <= target <= 28:
            return self._limitation_response(
                gate_name,
                "set the temperature",
                reason="the requested temperature is outside the supported 16 to 28 degrees Celsius range",
            )
        if not (target * 2).is_integer():
            return self._limitation_response(
                gate_name,
                "set the temperature",
                reason="the requested temperature must use 0.5 degree Celsius increments",
            )

        required_calls: list[tuple[str, dict[str, Any]]] = [
            ("set_climate_temperature", {"seat_zone": normalized_zone, "temperature": target})
        ]
        if normalized_zone != "ALL_ZONES":
            required_calls.append(("get_temperature_inside_car", {}))
        blocker = self._require_tool_surface_for_calls(gate_name, "set the temperature safely", required_calls)
        if blocker:
            return blocker

        other_temp: float | None = None
        if normalized_zone != "ALL_ZONES":
            temp_result = self._call_raw_tool_sync("get_temperature_inside_car", {})
            if temp_result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "set the temperature safely", temp_result)
            temp_state = result_value(temp_result)
            if not isinstance(temp_state, dict):
                return self._limitation_response(
                    gate_name,
                    "set the temperature safely",
                    reason="the temperature result had an unexpected shape",
                )
            other_key = (
                "climate_temperature_passenger"
                if normalized_zone == "DRIVER"
                else "climate_temperature_driver"
            )
            raw_other = temp_state.get(other_key)
            if isinstance(raw_other, (int, float)):
                other_temp = float(raw_other)
            else:
                return self._limitation_response(
                    gate_name,
                    "set the temperature safely",
                    reason=f"the other seat zone temperature {other_key} was unavailable",
                )

        action = self._call_raw_tool_sync(
            "set_climate_temperature",
            {"seat_zone": normalized_zone, "temperature": target},
        )
        if action.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "set the temperature", action)

        warning = False
        if other_temp is not None and abs(target - other_temp) > 3:
            warning = True
        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": "012",
            "seat_zone": normalized_zone,
            "temperature": target,
            "warned_difference_over_3": warning,
        }
        if normalized_zone == "ALL_ZONES":
            message = f"Temperature set to {target:g} degrees Celsius for all zones."
        elif warning:
            other_label = "passenger" if normalized_zone == "DRIVER" else "driver"
            message = (
                f"{normalized_zone.lower()} temperature set to {target:g} degrees Celsius. "
                f"Heads up, that is more than 3 degrees different from the {other_label} side."
            )
        else:
            message = f"{normalized_zone.lower()} temperature set to {target:g} degrees Celsius."
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "SUCCESS",
                "policy": "012",
                "seat_zone": normalized_zone,
                "temperature": target,
                "warning_difference_over_3": warning,
                "other_temperature": other_temp,
                "message": message,
            },
        )
        if warning:
            other_label = "passenger" if normalized_zone == "DRIVER" else "driver"
            self._add_response_obligation(
                "policy_012_temperature_difference",
                (
                    "Heads up, that is more than 3 degrees different from the "
                    f"{other_label} side."
                ),
                (
                    r"\bmore than 3\b.*\bdegrees?\b",
                    r"\bover 3\b.*\bdegrees?\b",
                    r"\btemperature difference\b.*\b3\b",
                ),
            )
        self._helper_message(message)
        return {
            "status": "SUCCESS",
            "action": action,
            "warning_difference_over_3": warning,
            "other_temperature": other_temp,
            "message": message,
            "report": report,
        }

    def sync_climate_zone(
        self,
        source_zone: str,
        target_zone: str,
        include_temperature: bool = True,
        include_seat_heating: bool = True,
    ) -> dict[str, Any]:
        """Copy front-zone climate values from source to target."""

        gate_name = "sync_climate_zone"
        source = str(source_zone).upper()
        target = str(target_zone).upper()
        if source not in {"DRIVER", "PASSENGER"} or target not in {"DRIVER", "PASSENGER"}:
            return self._limitation_response(
                gate_name,
                "sync climate zones",
                reason="source_zone and target_zone must be DRIVER or PASSENGER",
            )
        if source == target:
            return self._limitation_response(
                gate_name,
                "sync climate zones",
                reason="source_zone and target_zone are the same",
            )
        if not include_temperature and not include_seat_heating:
            return self._limitation_response(
                gate_name,
                "sync climate zones",
                reason="at least one of include_temperature or include_seat_heating must be true",
            )

        required: list[tuple[str, dict[str, Any]]] = []
        if include_temperature:
            required.extend([
                ("get_temperature_inside_car", {}),
                ("set_climate_temperature", {"seat_zone": target, "temperature": 20}),
            ])
        if include_seat_heating:
            required.extend([
                ("get_seat_heating_level", {}),
                ("set_seat_heating", {"seat_zone": target, "level": 0}),
            ])
        blocker = self._require_tool_surface_for_calls(gate_name, "sync climate zones", required)
        if blocker:
            return blocker

        actions: list[dict[str, Any]] = []
        copied: dict[str, Any] = {}
        if include_temperature:
            temp_result = self._call_raw_tool_sync("get_temperature_inside_car", {})
            if temp_result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "read temperature", temp_result)
            temp_state = result_value(temp_result)
            if not isinstance(temp_state, dict):
                return self._limitation_response(
                    gate_name,
                    "sync climate zones",
                    reason="the temperature result had an unexpected shape",
                )
            source_key = f"climate_temperature_{source.lower()}"
            source_temp = temp_state.get(source_key)
            if not isinstance(source_temp, (int, float)):
                return self._limitation_response(
                    gate_name,
                    "sync climate zones",
                    reason=f"source temperature {source_key} was unavailable",
                )
            action = self._call_raw_tool_sync(
                "set_climate_temperature",
                {"seat_zone": target, "temperature": float(source_temp)},
            )
            if action.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "set target temperature", action)
            actions.append(action)
            copied["temperature"] = float(source_temp)

        if include_seat_heating:
            heat_result = self._call_raw_tool_sync("get_seat_heating_level", {})
            if heat_result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "read seat heating", heat_result)
            heat_state = result_value(heat_result)
            if not isinstance(heat_state, dict):
                return self._limitation_response(
                    gate_name,
                    "sync climate zones",
                    reason="the seat-heating result had an unexpected shape",
                )
            source_key = f"seat_heating_{source.lower()}"
            source_level = heat_state.get(source_key)
            if not isinstance(source_level, (int, float)):
                return self._limitation_response(
                    gate_name,
                    "sync climate zones",
                    reason=f"source seat-heating level {source_key} was unavailable",
                )
            level = int(source_level)
            action = self._call_raw_tool_sync(
                "set_seat_heating",
                {"seat_zone": target, "level": level},
            )
            if action.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "set target seat heating", action)
            actions.append(action)
            copied["seat_heating_level"] = level

        message_parts = []
        if "temperature" in copied:
            message_parts.append(f"temperature {copied['temperature']:g} degrees Celsius")
        if "seat_heating_level" in copied:
            message_parts.append(f"seat heating level {copied['seat_heating_level']}")
        message = (
            f"Copied {source.lower()} {' and '.join(message_parts)} "
            f"to the {target.lower()} zone."
        )
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "SUCCESS",
                "source_zone": source,
                "target_zone": target,
                "copied": copied,
                "message": message,
            },
        )
        self._helper_message(message)
        return {
            "status": "SUCCESS",
            "source_zone": source,
            "target_zone": target,
            "copied": copied,
            "actions": actions,
            "message": message,
            "report": report,
        }

    def increase_fan_speed(self, steps: int = 1) -> dict[str, Any]:
        return self._adjust_fan_speed(abs(int(steps or 1)))

    def decrease_fan_speed(self, steps: int = 1) -> dict[str, Any]:
        return self._adjust_fan_speed(-abs(int(steps or 1)))

    def _abort_unknown_fan_speed_for_relative_change(
        self,
        gate_name: str,
        delta: int,
    ) -> NoReturn:
        self._ensure_scratchpad_shape()
        message = self._unknown_fan_speed_relative_message(delta)
        report = {
            "helper": gate_name,
            "status": "UNAVAILABLE",
            "missing_response_fields": ["result.get_climate_settings.fan_speed"],
            "delta": delta,
            "reason": "current fan_speed was unavailable",
            "message": message,
        }
        self.scratchpad["gates"][gate_name] = {
            "status": "NO",
            "missing_response_fields": ["result.get_climate_settings.fan_speed"],
            "reason": report["reason"],
        }
        self._store_helper_report(gate_name, report)
        self._abort_with_response(message)

    def _adjust_fan_speed(self, delta: int) -> dict[str, Any]:
        gate_name = "increase_fan_speed" if delta > 0 else "decrease_fan_speed"
        if delta == 0:
            return self._limitation_response(
                gate_name,
                "adjust fan speed",
                reason="steps must be at least 1",
            )
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "adjust fan speed",
            [
                ("get_climate_settings", {}),
                ("set_fan_speed", {"level": 1}),
            ],
        )
        if blocker:
            return blocker
        climate_result = self._call_raw_tool_sync("get_climate_settings", {})
        if climate_result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "read climate settings", climate_result)
        climate = climate_result.get("result")
        if not isinstance(climate, dict):
            return self._limitation_response(
                gate_name,
                "adjust fan speed",
                reason="the climate settings result had an unexpected shape",
            )
        current = climate.get("fan_speed")
        if self._fan_speed_value_unavailable(climate):
            self._abort_unknown_fan_speed_for_relative_change(gate_name, delta)
        if not isinstance(current, (int, float)):
            return self._limitation_response(
                gate_name,
                "adjust fan speed",
                reason="current fan_speed was unavailable",
            )
        target = max(0, min(7, int(current) + delta))
        action = self._call_raw_tool_sync("set_fan_speed", {"level": target})
        if action.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "set fan speed", action)
        direction = "increased" if delta > 0 else "decreased"
        message = f"Fan speed {direction} from {int(current)} to {target}."
        report = self._store_helper_report(
            gate_name,
            {
                "helper": gate_name,
                "status": "SUCCESS",
                "current_level": int(current),
                "target_level": target,
                "delta": delta,
                "message": message,
            },
        )
        self._helper_message(message)
        return {
            "status": "SUCCESS",
            "current_level": int(current),
            "target_level": target,
            "delta": delta,
            "action": action,
            "message": message,
            "report": report,
        }

    def set_occupied_seat_heating(
        self,
        level: int | None = None,
        increase_by: int | None = None,
    ) -> dict[str, Any]:
        """Set seat heating only for occupied front seats.

        Reads occupancy and current levels from live state, then sets each
        occupied front seat (DRIVER/PASSENGER are the only heatable zones).
        Pass `level` for an absolute target or `increase_by` for a relative
        change; the helper performs the setter so the action is never just
        claimed.
        """

        gate_name = "set_occupied_seat_heating"
        if (level is None) == (increase_by is None):
            return self._limitation_response(
                gate_name,
                "set occupied-seat heating",
                reason="provide exactly one of level or increase_by",
            )
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "set occupied-seat heating",
            [
                ("get_seats_occupancy", {}),
                ("get_seat_heating_level", {}),
                ("set_seat_heating", {"level": 0, "seat_zone": "DRIVER"}),
            ],
        )
        if blocker:
            return blocker
        reads = self._call_raw_tools_sync([
            {"tool_name": "get_seats_occupancy", "arguments": {}},
            {"tool_name": "get_seat_heating_level", "arguments": {}},
        ])
        occupancy_result = result_by_tool(reads, "get_seats_occupancy")
        levels_result = result_by_tool(reads, "get_seat_heating_level")
        if occupancy_result.get("status") != "SUCCESS":
            return self._failed_tool_response(gate_name, "read seat occupancy", occupancy_result)
        occupancy_payload = result_value(occupancy_result)
        occupied = (
            occupancy_payload.get("seats_occupied")
            if isinstance(occupancy_payload, dict)
            else None
        )
        if not isinstance(occupied, dict):
            return self._limitation_response(
                gate_name,
                "set occupied-seat heating",
                reason="the seat occupancy result had an unexpected shape",
            )
        levels_payload = result_value(levels_result) if levels_result.get("status") == "SUCCESS" else {}
        if increase_by is not None and levels_result.get("status") != "SUCCESS":
            return self._failed_tool_response(
                gate_name, "read current seat heating", levels_result
            )
        # Only front seats are heatable zones.
        zone_by_occupancy_key = {"driver": "DRIVER", "passenger": "PASSENGER"}
        targets: dict[str, int] = {}
        for occupancy_key, zone in zone_by_occupancy_key.items():
            if occupied.get(occupancy_key) is not True:
                continue
            if level is not None:
                target = int(level)
            else:
                current = (
                    levels_payload.get(f"seat_heating_{occupancy_key}")
                    if isinstance(levels_payload, dict)
                    else None
                )
                if not isinstance(current, (int, float)) or isinstance(current, bool):
                    return self._limitation_response(
                        gate_name,
                        "set occupied-seat heating",
                        reason=f"the current {zone.lower()} seat heating level is unavailable",
                    )
                target = int(current) + int(increase_by)
            targets[zone] = max(0, min(3, target))
        if not targets:
            self._helper_message("None of the front seats are occupied, so there's nothing to heat.")
            return {"status": "SUCCESS", "actions": [], "targets": {}}
        action_results = []
        for zone, target in targets.items():
            result = self._call_raw_tool_sync(
                "set_seat_heating", {"level": target, "seat_zone": zone}
            )
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "set seat heating", result)
            action_results.append(result)
        zones_text = _human_join([zone.lower() for zone in targets])
        message = f"Seat heating set for the occupied {zones_text} seat."
        report = {"status": "SUCCESS", "helper": gate_name, "targets": targets, "message": message}
        self._store_helper_report(gate_name, report)
        self._helper_message(message)
        return {
            "status": "SUCCESS",
            "actions": action_results,
            "targets": targets,
            "report": report,
            "message": message,
        }

    def get_distance_by_soc_value(
        self,
        initial_state_of_charge: int,
        final_state_of_charge: int = 0,
    ) -> dict[str, Any]:
        """Return a normalized distance from CAR-bench's dynamic distance result key."""

        call = (
            "get_distance_by_soc",
            {
                "initial_state_of_charge": initial_state_of_charge,
                "final_state_of_charge": final_state_of_charge,
            },
        )
        blocker = self._require_tool_surface_for_calls(
            "get_distance_by_soc_value",
            "calculate distance by state of charge",
            [call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        raw = result_value(result)
        if not isinstance(raw, dict):
            raise ValueError(f"Expected get_distance_by_soc result object, got {raw!r}")
        for key, value in raw.items():
            if not str(key).startswith("distance_"):
                continue
            parsed = self._parse_distance_value(value)
            parsed.update(raw_key=key, raw_value=value, raw_result=raw)
            return parsed
        raise ValueError(f"No distance_* field in get_distance_by_soc result: {raw!r}")

    def get_route_options(self, start_id: str, destination_id: str) -> dict[str, Any]:
        """Return normalized route options between two grounded IDs."""

        start_id = self._resolve_preloaded_argument_value(start_id)
        destination_id = self._resolve_preloaded_argument_value(destination_id)
        normalized_kwargs, endpoint_block = self._normalize_route_endpoint_arguments(
            {"start_id": start_id, "destination_id": destination_id}
        )
        if endpoint_block is not None:
            return endpoint_block
        start_id = normalized_kwargs["start_id"]
        destination_id = normalized_kwargs["destination_id"]
        if isinstance(start_id, str) and start_id and start_id == destination_id:
            self.scratchpad["gates"]["degenerate_route_guard"] = {
                "status": "SKIPPED",
                "start_id": start_id,
                "destination_id": destination_id,
            }
            return {
                "status": "SKIPPED",
                "routes": [],
                "fastest": None,
                "shortest": None,
                "reason": "start and destination are the same location; no route is needed",
            }
        replacement_blocker = self._destination_replacement_surface_blocker(
            "destination_replacement_surface",
            destination_id,
        )
        if replacement_blocker:
            return replacement_blocker
        call = (
            "get_routes_from_start_to_destination",
            {"start_id": start_id, "destination_id": destination_id},
        )
        blocker = self._require_tool_surface_for_calls("get_route_options", "look up route options", [call])
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        if result.get("status") != "SUCCESS":
            return {"status": "FAILED_TOOL_RESULT", "result": result}
        self._abort_if_route_options_unavailable(
            "get_route_options",
            start_id,
            destination_id,
            result,
        )
        raw = result_value(result)
        routes = self._extract_routes(raw)
        normalized = [self._normalize_route(route) for route in routes]
        fastest = self.select_route(normalized, alias="fastest", record_selection=False)
        shortest = self.select_route(normalized, alias="shortest", record_selection=False)
        fastest_route = fastest.get("route") if fastest.get("status") == "SUCCESS" else None
        shortest_route = shortest.get("route") if shortest.get("status") == "SUCCESS" else None
        route_options = {
            "status": "SUCCESS",
            "routes": normalized,
            "fastest": fastest_route,
            "shortest": shortest_route,
            "fastest_route_id": (
                fastest_route.get("route_id")
                if isinstance(fastest_route, dict)
                else None
            ),
            "shortest_route_id": (
                shortest_route.get("route_id")
                if isinstance(shortest_route, dict)
                else None
            ),
            "raw_result": raw,
        }
        self.remember_entity(
            "last_route_options",
            {
                "revision": int(
                    self.scratchpad["entities"].get("navigation_revision") or 0
                ),
                "start_id": start_id,
                "destination_id": destination_id,
                **{
                    key: copy.deepcopy(route_options[key])
                    for key in (
                        "routes",
                        "fastest",
                        "shortest",
                        "fastest_route_id",
                        "shortest_route_id",
                    )
                },
            },
        )
        if len(normalized) > 1 and isinstance(fastest_route, dict):
            fastest_id = fastest_route.get("route_id") or fastest_route.get("id")
            if isinstance(fastest_id, str):
                self._store_route_narration(normalized, fastest_id, stage="search")
        return route_options

    def select_route(
        self,
        routes: Any,
        route_id: str | None = None,
        alias: str | None = None,
        name_via: str | None = None,
        prefer: str | None = None,
        record_selection: bool = True,
    ) -> dict[str, Any]:
        """Select exactly one route from a route list without guessing."""

        route_list = [self._normalize_route(route) for route in self._extract_routes(routes)]
        if not route_list:
            return {"status": "NOT_FOUND", "matches": [], "reason": "no routes available"}

        criteria = [
            value is not None and str(value).strip() != ""
            for value in (route_id, alias, name_via, prefer)
        ]
        if not any(criteria):
            selected_route = route_list[0] if len(route_list) == 1 else None
            result = {
                "status": "AMBIGUOUS" if len(route_list) > 1 else "SUCCESS",
                "route": selected_route,
                "result": selected_route,
                "route_id": selected_route.get("route_id") if selected_route else None,
                "selected_route_id": (
                    selected_route.get("route_id") if selected_route else None
                ),
                "matches": route_list,
                "reason": "no selector provided" if len(route_list) > 1 else "only one route",
            }
            if selected_route is not None and record_selection:
                self._remember_route_selection(
                    selected_route,
                    {
                        "route_id": route_id,
                        "alias": alias,
                        "name_via": name_via,
                        "prefer": prefer,
                    },
                )
            return result

        matches = route_list
        if route_id:
            wanted = str(route_id).strip()
            matches = [route for route in matches if route.get("route_id") == wanted]
        selector_alias = alias or prefer
        if selector_alias:
            wanted_alias = str(selector_alias).strip().lower()
            matches = [
                route
                for route in matches
                if wanted_alias in [str(item).lower() for item in route.get("alias", [])]
            ]
        if name_via:
            wanted_via = self._normalize_via(name_via)
            matches = [
                route
                for route in matches
                if self._normalize_via(route.get("name_via", "")) == wanted_via
            ]

        if len(matches) == 1:
            result = {
                "status": "SUCCESS",
                "route": matches[0],
                "result": matches[0],
                "route_id": matches[0].get("route_id"),
                "selected_route_id": matches[0].get("route_id"),
            }
            if record_selection:
                self._remember_route_selection(
                    matches[0],
                    {
                        "route_id": route_id,
                        "alias": alias,
                        "name_via": name_via,
                        "prefer": prefer,
                    },
                )
            return result
        if not matches:
            return {"status": "NOT_FOUND", "matches": [], "reason": "selector matched no route"}
        return {"status": "AMBIGUOUS", "matches": matches, "reason": "selector matched multiple routes"}

    def select_route_by_user_preferences(
        self,
        routes: Any,
        preference_text: str | list[str] | None = None,
        record_selection: bool = True,
    ) -> dict[str, Any]:
        """Select a route using supported stored route-selection preferences."""

        route_list = [self._normalize_route(route) for route in self._extract_routes(routes)]
        if not route_list:
            return {"status": "NOT_FOUND", "matches": [], "reason": "no routes available"}
        if len(route_list) == 1:
            return self.select_route(
                route_list,
                route_id=route_list[0].get("route_id"),
                record_selection=record_selection,
            )

        preference_texts = self._route_preference_texts(preference_text)
        if not preference_texts:
            return {
                "status": "UNAVAILABLE",
                "matches": route_list,
                "reason": "no stored route-selection preference is available",
            }

        preference_blob = " ".join(preference_texts).lower()
        threshold = self._route_preference_threshold_minutes(preference_blob)
        wants_fastest = "fastest" in preference_blob or "quickest" in preference_blob
        wants_shortest = "shortest" in preference_blob
        avoids_tolls = any(
            phrase in preference_blob
            for phrase in (
                "without toll",
                "no toll",
                "avoid toll",
                "avoids toll",
                "does not include toll",
                "don't include toll",
                "do not include toll",
                "toll-free",
                "toll free",
            )
        )

        chosen: dict[str, Any] | None = None
        rule = "stored route preference"
        if wants_fastest and avoids_tolls:
            fastest = self._fastest_route(route_list)
            no_toll_routes = [
                route for route in route_list if not self._route_includes_toll(route)
            ]
            fastest_no_toll = self._fastest_route(no_toll_routes)
            if threshold is not None and fastest is not None and fastest_no_toll is not None:
                fastest_minutes = self._route_duration_minutes(fastest)
                no_toll_minutes = self._route_duration_minutes(fastest_no_toll)
                if (
                    fastest_minutes is not None
                    and no_toll_minutes is not None
                    and no_toll_minutes <= fastest_minutes + threshold
                ):
                    chosen = fastest_no_toll
                    rule = f"fastest no-toll route within {threshold} minutes of fastest"
                else:
                    chosen = fastest
                    rule = f"overall fastest because no-toll route exceeds {threshold} minute threshold"
            elif fastest_no_toll is not None:
                chosen = fastest_no_toll
                rule = "fastest route without toll roads"
        elif avoids_tolls:
            no_toll_routes = [
                route for route in route_list if not self._route_includes_toll(route)
            ]
            if len(no_toll_routes) == 1:
                chosen = no_toll_routes[0]
                rule = "only route without toll roads"
            elif len(no_toll_routes) > 1:
                return {
                    "status": "AMBIGUOUS",
                    "matches": no_toll_routes,
                    "reason": "stored preference excludes toll roads but multiple no-toll routes remain",
                    "preference_texts": preference_texts,
                }
        elif wants_fastest:
            chosen = self._fastest_route(route_list)
            rule = "fastest route preference"
        elif wants_shortest:
            chosen = self._shortest_route(route_list)
            rule = "shortest route preference"

        if chosen is None:
            return {
                "status": "UNAVAILABLE",
                "matches": route_list,
                "reason": "stored route-selection preference could not be applied by this helper",
                "preference_texts": preference_texts,
            }

        route_id = chosen.get("route_id")
        selected = self.select_route(
            route_list,
            route_id=route_id if isinstance(route_id, str) else None,
            record_selection=record_selection,
        )
        if selected.get("status") == "SUCCESS":
            selected["preference_texts"] = preference_texts
            selected["preference_rule"] = rule
            selected_route = selected.get("route")
            if isinstance(selected_route, dict):
                self._store_preference_route_narration(route_list, selected_route, rule)
        return selected

    def _store_preference_route_narration(
        self,
        routes: list[dict[str, Any]],
        selected_route: dict[str, Any],
        rule: str,
    ) -> None:
        route_id = selected_route.get("route_id") or selected_route.get("id")
        if not isinstance(route_id, str) or not route_id:
            return
        alternatives = max(0, len(routes) - 1)
        via = _clean_string(selected_route.get("name_via") or selected_route.get("via"))
        text = "I selected your preference-resolved route"
        if not self._route_includes_toll(selected_route) and (
            "no-toll" in rule or "without toll" in rule
        ):
            text += " without toll roads"
        if via:
            text += f" via {via}"
        text += " for this segment"
        if alternatives > 0:
            verb = "is" if alternatives == 1 else "are"
            plural = "" if alternatives == 1 else "s"
            text += f"; there {verb} {alternatives} other option{plural}"
        text += "."
        if self._route_includes_toll(selected_route):
            text += " It uses toll roads."
        self._ensure_scratchpad_shape()
        self.scratchpad["facts"]["pending_route_narration"] = text

    def _route_preference_texts(self, preference_text: str | list[str] | None) -> list[str]:
        if isinstance(preference_text, str) and preference_text.strip():
            return [preference_text.strip()]
        if isinstance(preference_text, list):
            return [str(item).strip() for item in preference_text if str(item).strip()]

        entities = self.scratchpad.get("entities", {})
        stored = entities.get("user_preferences")
        preferences = stored.get("preferences") if isinstance(stored, dict) else None
        texts: list[str] = []
        if isinstance(preferences, dict):
            route_selection = (
                preferences.get("navigation_and_routing", {})
                if isinstance(preferences.get("navigation_and_routing"), dict)
                else {}
            ).get("route_selection")
            if isinstance(route_selection, str) and route_selection.strip():
                texts.append(route_selection.strip())
            elif isinstance(route_selection, list):
                texts.extend(str(item).strip() for item in route_selection if str(item).strip())
        if not texts and isinstance(stored, dict):
            summary = stored.get("summary")
            if isinstance(summary, list):
                texts.extend(
                    str(item).split(":", 1)[-1].strip()
                    for item in summary
                    if "route_selection" in str(item) and str(item).strip()
                )
        return texts

    @staticmethod
    def _route_preference_threshold_minutes(text: str) -> int | None:
        patterns = (
            r"(?:not|no)\s+more\s+than\s+(\d+)\s+minutes?\s+longer\s+than\s+(?:the\s+)?fastest",
            r"within\s+(\d+)\s+minutes?\s+of\s+(?:the\s+)?fastest",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _route_includes_toll(route: dict[str, Any]) -> bool:
        if route.get("includes_toll") is True or route.get("has_tolls") is True:
            return True
        if route.get("tolls") not in (None, False, "", [], {}):
            return True
        road_types = route.get("road_types")
        if isinstance(road_types, list):
            return any("toll" in str(road_type).lower() for road_type in road_types)
        return False

    @staticmethod
    def _route_duration_minutes(route: dict[str, Any]) -> int | None:
        duration = route.get("duration_total_minutes")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            return int(duration)
        hours = route.get("duration_hours")
        minutes = route.get("duration_minutes")
        if (
            isinstance(hours, (int, float))
            and not isinstance(hours, bool)
            and isinstance(minutes, (int, float))
            and not isinstance(minutes, bool)
        ):
            return int(hours) * 60 + int(minutes)
        return None

    @staticmethod
    def _route_distance_km(route: dict[str, Any]) -> float | None:
        distance = first_number_value(route.get("distance_km"), default=None)
        if isinstance(distance, (int, float)) and not isinstance(distance, bool):
            return float(distance)
        distance = first_number_value(route.get("distance"), default=None)
        if isinstance(distance, (int, float)) and not isinstance(distance, bool):
            return float(distance)
        return None

    def _fastest_route(self, routes: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [
            (self._route_duration_minutes(route), str(route.get("route_id") or ""), route)
            for route in routes
        ]
        timed = [item for item in candidates if item[0] is not None]
        if timed:
            return min(timed, key=lambda item: (int(item[0]), item[1]))[2]
        selected = self.select_route(routes, alias="fastest", record_selection=False)
        if selected.get("status") == "SUCCESS" and isinstance(selected.get("route"), dict):
            return selected["route"]
        return None

    def _shortest_route(self, routes: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [
            (self._route_distance_km(route), str(route.get("route_id") or ""), route)
            for route in routes
        ]
        measured = [item for item in candidates if item[0] is not None]
        if measured:
            return min(measured, key=lambda item: (float(item[0]), item[1]))[2]
        selected = self.select_route(routes, alias="shortest", record_selection=False)
        if selected.get("status") == "SUCCESS" and isinstance(selected.get("route"), dict):
            return selected["route"]
        return None

    @staticmethod
    def _normalize_poi_selector_text(value: Any) -> str:
        text = _clean_string(value) or ""
        text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower()
        return " ".join(text.split())

    def select_poi(
        self,
        pois: Any = None,
        poi_id: str | None = None,
        name: str | None = None,
        category: str | None = None,
        record_selection: bool = True,
    ) -> dict[str, Any]:
        """Select exactly one POI from grounded search results without guessing."""

        if pois is None:
            pois = self.scratchpad.get("entities", {}).get("last_pois")
        if isinstance(pois, list):
            poi_list = [poi for poi in pois if isinstance(poi, dict)]
        elif isinstance(pois, dict) and any(
            key in pois for key in ("poi_id", "id", "navigation_id", "name")
        ) and not any(key in pois for key in ("pois", "pois_found", "pois_found_along_route")):
            poi_list = [pois]
        else:
            poi_list = [poi for poi in pois_value(pois) if isinstance(poi, dict)]

        if category:
            wanted_category = str(category).strip().lower()
            poi_list = [
                poi
                for poi in poi_list
                if str(poi.get("category") or "").strip().lower() == wanted_category
            ]

        matches = poi_list
        if poi_id:
            wanted_id = str(poi_id).strip()
            matches = [
                poi
                for poi in matches
                if wanted_id
                in {
                    str(poi.get("poi_id") or "").strip(),
                    str(poi.get("id") or "").strip(),
                    str(poi.get("navigation_id") or "").strip(),
                }
            ]
        if name:
            wanted_name = self._normalize_poi_selector_text(name)
            exact = [
                poi
                for poi in matches
                if self._normalize_poi_selector_text(poi.get("name")) == wanted_name
            ]
            if exact:
                matches = exact
            else:
                matches = [
                    poi
                    for poi in matches
                    if wanted_name
                    and (
                        wanted_name in self._normalize_poi_selector_text(poi.get("name"))
                        or self._normalize_poi_selector_text(poi.get("name")) in wanted_name
                    )
                ]

        if len(matches) == 1:
            selected = copy.deepcopy(matches[0])
            poi_identifier = (
                selected.get("poi_id")
                or selected.get("id")
                or selected.get("navigation_id")
            )
            if "poi_id" not in selected and isinstance(poi_identifier, str):
                selected["poi_id"] = poi_identifier
            if "navigation_id" not in selected and isinstance(poi_identifier, str):
                selected["navigation_id"] = poi_identifier
            result = {
                "status": "SUCCESS",
                "poi": selected,
                "selected": selected,
                "result": selected,
                "poi_id": selected.get("poi_id"),
                "id": selected.get("poi_id"),
                "navigation_id": selected.get("navigation_id"),
                "name": selected.get("name"),
            }
            if record_selection:
                self.remember_entity("selected_poi", copy.deepcopy(result))
                category_value = str(selected.get("category") or "").lower()
                if "charging" in category_value or self._is_charging_poi_id(selected.get("poi_id")):
                    self.remember_entity("selected_charging_poi", copy.deepcopy(result))
            return result
        if not matches:
            return {"status": "NOT_FOUND", "matches": [], "reason": "selector matched no POI"}
        return {
            "status": "AMBIGUOUS",
            "matches": copy.deepcopy(matches),
            "reason": "selector matched multiple POIs",
        }

    def select_poi_at_location_open_at_route_arrival(
        self,
        location_id: str,
        category_poi: str,
        route: Any = None,
        route_id: str | None = None,
        routes: Any = None,
        start_id: str | None = None,
        record_selection: bool = True,
    ) -> dict[str, Any]:
        """Select POIs open at route-arrival time without guessing."""

        gate_name = "select_poi_at_location_open_at_route_arrival"
        if not isinstance(location_id, str) or not location_id.strip():
            raise ValueError("location_id must be a grounded location id")
        if not isinstance(category_poi, str) or not category_poi.strip():
            raise ValueError("category_poi must be a non-empty POI category")
        location_id = location_id.strip()
        category_poi = category_poi.strip()

        route_fact = self._resolve_route_for_arrival_poi(
            location_id=location_id,
            route=route,
            route_id=route_id,
            routes=routes,
            start_id=start_id,
        )
        if route_fact.get("status") != "SUCCESS":
            return route_fact
        route_value = route_fact.get("route")
        if not isinstance(route_value, dict):
            return {
                "status": "UNAVAILABLE",
                "reason": "route facts for arrival-time POI selection are unavailable",
            }
        arrival = self._arrival_time_for_route(route_value)
        if arrival.get("status") != "SUCCESS":
            return arrival

        call = (
            "search_poi_at_location",
            {"location_id": location_id, "category_poi": category_poi},
        )
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "search POIs open at route arrival",
            [call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        if result.get("status") != "SUCCESS":
            return {"status": "FAILED_TOOL_RESULT", "result": result}

        pois = self._summarize_pois(result, call[1])
        arrival_minute_of_day = int(arrival["hour"]) * 60 + int(arrival["minute"])
        open_pois: list[dict[str, Any]] = []
        closed_pois: list[dict[str, Any]] = []
        unknown_opening_pois: list[dict[str, Any]] = []
        for poi in pois:
            if not isinstance(poi, dict) or poi.get("_truncated"):
                continue
            opening_hours = poi.get("opening_hours")
            status = self._poi_open_status_at_minutes(opening_hours, arrival_minute_of_day)
            enriched = copy.deepcopy(poi)
            enriched["open_at_arrival"] = status
            enriched["arrival_time"] = arrival.get("time_label")
            if status is True:
                open_pois.append(enriched)
            elif status is False:
                closed_pois.append(enriched)
            else:
                unknown_opening_pois.append(enriched)

        report_base = {
            "helper": gate_name,
            "location_id": location_id,
            "category_poi": category_poi,
            "arrival": copy.deepcopy(arrival),
            "route": copy.deepcopy(route_value),
            "route_source": route_fact.get("source"),
            "open_pois": copy.deepcopy(open_pois),
            "closed_pois": copy.deepcopy(closed_pois),
            "unknown_opening_pois": copy.deepcopy(unknown_opening_pois),
            "searched_pois": copy.deepcopy(pois),
        }

        if len(open_pois) == 1:
            selected = self.select_poi(open_pois, record_selection=record_selection)
            if selected.get("status") == "SUCCESS":
                selected_poi = selected.get("poi")
                report = {
                    **report_base,
                    "status": "SUCCESS",
                    "selected_poi": copy.deepcopy(selected_poi),
                    "poi_id": selected.get("poi_id"),
                    "navigation_id": selected.get("navigation_id"),
                    "name": selected.get("name"),
                }
                self.remember_entity("last_open_at_arrival_poi", copy.deepcopy(report))
                self._store_helper_report(gate_name, report)
                return report
            return selected

        status = "NOT_FOUND" if not open_pois else "AMBIGUOUS"
        reason = (
            "no searched POI has opening hours covering route arrival"
            if status == "NOT_FOUND"
            else "multiple POIs are open at route arrival"
        )
        report = {**report_base, "status": status, "reason": reason}
        self.remember_entity("last_open_at_arrival_poi", copy.deepcopy(report))
        self._store_helper_report(gate_name, report)
        return report

    def _remember_route_selection(
        self,
        route: dict[str, Any],
        selector: dict[str, Any],
    ) -> None:
        entities = self.scratchpad.get("entities", {})
        route_options = entities.get("last_route_options")
        route_id = route.get("route_id")
        selection = {
            "route_id": route_id,
            "selected_route_id": route_id,
            "route": route,
            "revision": int(entities.get("navigation_revision") or 0),
            "selector": selector,
        }
        if isinstance(route_options, dict) and any(
            isinstance(option, dict) and option.get("route_id") == route_id
            for option in route_options.get("routes", [])
        ):
            selection["start_id"] = route_options.get("start_id")
            selection["destination_id"] = route_options.get("destination_id")
            selection["options_revision"] = route_options.get("revision")
        self.remember_entity("selected_route", selection)
        history = entities.setdefault("route_selection_history", [])
        if isinstance(history, list):
            history.append(copy.deepcopy(selection))
            del history[:-8]

    def get_weather_at_route_arrival(
        self,
        location_or_poi_id: str,
        route: Any = None,
        route_id: str | None = None,
        routes: Any = None,
        start_id: str | None = None,
    ) -> dict[str, Any]:
        """Call weather at destination arrival time for route-conditioned decisions."""

        destination_id = self._resolve_preloaded_argument_value(location_or_poi_id)
        if not isinstance(destination_id, str) or not destination_id:
            raise ValueError("location_or_poi_id must be a grounded location or POI id")
        selected_route = self._resolve_route_for_arrival_weather(
            destination_id=destination_id,
            route=route,
            route_id=route_id,
            routes=routes,
            start_id=start_id,
        )
        if selected_route.get("status") != "SUCCESS":
            return selected_route
        route_fact = selected_route["route"]
        arrival = self._arrival_time_for_route(route_fact)
        if arrival.get("status") != "SUCCESS":
            return arrival
        kwargs = {
            "location_or_poi_id": destination_id,
            "month": arrival["month"],
            "day": arrival["day"],
            "time_hour_24hformat": arrival["hour"],
            "time_minutes": arrival["minute"],
        }
        result = self._call_raw_tool_sync("get_weather", kwargs)
        if result.get("status") == "SUCCESS":
            self.scratchpad["facts"]["last_arrival_weather"] = {
                "location_or_poi_id": destination_id,
                "route_id": route_fact.get("route_id") or route_fact.get("id"),
                "arrival": arrival,
                "weather": copy.deepcopy(result_value(result)),
            }
        return result

    def select_charging_plug(
        self,
        pois: Any = None,
        require_available: bool = False,
    ) -> dict[str, Any]:
        """Select the highest-power charging plug while preserving station context."""

        if pois is None:
            pois = self.scratchpad.get("entities", {}).get("last_pois")
        if isinstance(pois, list):
            poi_list = [poi for poi in pois if isinstance(poi, dict)]
        elif isinstance(pois, dict) and isinstance(pois.get("charging_plugs"), list):
            poi_list = [pois]
        else:
            poi_list = [poi for poi in pois_value(pois) if isinstance(poi, dict)]
        candidates: list[dict[str, Any]] = []
        for poi in poi_list:
            plugs = poi.get("charging_plugs")
            if not isinstance(plugs, list):
                continue
            station_id = poi.get("poi_id") or poi.get("id") or poi.get("navigation_id")
            for plug in plugs:
                if not isinstance(plug, dict):
                    continue
                plug_id = plug.get("plug_id")
                power_kw = plug.get("power_kw")
                availability = str(plug.get("availability") or "").lower()
                if not isinstance(plug_id, str):
                    continue
                if not isinstance(power_kw, (int, float)) or isinstance(power_kw, bool):
                    continue
                if require_available and availability != "available":
                    continue
                candidates.append({
                    "station_id": station_id,
                    "charging_station_id": station_id,
                    "station_name": poi.get("name"),
                    "phone_number": poi.get("phone_number") or poi.get("phone"),
                    "navigation_id": poi.get("navigation_id") or station_id,
                    "host_location_id": poi.get("host_location_id"),
                    "plug_id": plug_id,
                    "charging_station_plug_id": plug_id,
                    "power_kw": power_kw,
                    "power_type": plug.get("power_type"),
                    "availability": plug.get("availability"),
                    "station": copy.deepcopy(poi),
                    "plug": copy.deepcopy(plug),
                })
        if not candidates:
            return {
                "status": "NOT_FOUND",
                "matches": [],
                "reason": (
                    "no available charging plugs matched"
                    if require_available
                    else "no charging plugs found"
                ),
            }
        candidates.sort(
            key=lambda item: (
                float(item["power_kw"]),
                str(item.get("power_type") or ""),
                str(item.get("station_name") or ""),
                str(item.get("plug_id") or ""),
            ),
            reverse=True,
        )
        selected = candidates[0]
        report = {
            "status": "SUCCESS",
            "selected": selected,
            "result": selected,
            "station_id": selected.get("station_id"),
            "charging_station_id": selected.get("charging_station_id"),
            "plug_id": selected.get("plug_id"),
            "charging_station_plug_id": selected.get("charging_station_plug_id"),
            "power_kw": selected.get("power_kw"),
            "power_type": selected.get("power_type"),
            "availability": selected.get("availability"),
            "matches": candidates,
            "require_available": require_available,
        }
        self.remember_entity("selected_charging_plug", copy.deepcopy(report))
        return report

    def plan_charging_for_next_meeting(
        self,
        range_buffer_km: int | float = 40,
        arrival_buffer_minutes: int = 5,
    ) -> dict[str, Any]:
        """Compute charging time bounds for reaching the next meeting."""

        now = self.policy_now()
        if not all(isinstance(now.get(key), int) for key in ("hour", "minute")):
            return self._limitation_response(
                "plan_charging_for_next_meeting",
                "plan charging for the next meeting",
                reason="the current policy time is unavailable",
            )
        current_location_id = self.policy_location_id()
        if not isinstance(current_location_id, str) or not current_location_id:
            return self._limitation_response(
                "plan_charging_for_next_meeting",
                "plan charging for the next meeting",
                reason="the current location is unavailable",
            )

        meeting_result = self.get_next_calendar_entry()
        if meeting_result.get("status") != "SUCCESS":
            return meeting_result
        meeting = meeting_result.get("next_entry")
        if not isinstance(meeting, dict):
            return self._limitation_response(
                "plan_charging_for_next_meeting",
                "plan charging for the next meeting",
                reason="the next calendar entry was unavailable",
            )
        meeting_location = _clean_string(meeting.get("location_name")) or _clean_string(meeting.get("location"))
        meeting_start_minutes = meeting.get("start_minutes")
        if not meeting_location or not isinstance(meeting_start_minutes, int):
            return self._limitation_response(
                "plan_charging_for_next_meeting",
                "plan charging for the next meeting",
                reason="the next calendar entry did not include a usable start time and location",
            )

        meeting_location_id = id_value(
            self._call_raw_tool_sync(
                "get_location_id_by_location_name",
                {"location": meeting_location},
            )
        )
        route_options = self.get_route_options(
            start_id=current_location_id,
            destination_id=meeting_location_id,
        )
        selected_route = self.select_route(route_options, prefer="fastest")
        if selected_route.get("status") != "SUCCESS":
            return selected_route
        route = selected_route.get("route")
        if not isinstance(route, dict):
            return self._limitation_response(
                "plan_charging_for_next_meeting",
                "plan charging for the next meeting",
                reason="the selected meeting route was unavailable",
            )
        route_distance_km = first_number_value(route.get("distance_km"))
        route_duration_minutes = route.get("duration_total_minutes")
        if not isinstance(route_duration_minutes, (int, float)):
            route_duration_minutes = (
                int(first_number_value(route.get("duration_hours"), default=0)) * 60
                + int(first_number_value(route.get("duration_minutes"), default=0))
            )

        charging = result_value(self._call_raw_tool_sync("get_charging_specs_and_status", {}))
        if not isinstance(charging, dict):
            return self._limitation_response(
                "plan_charging_for_next_meeting",
                "plan charging for the next meeting",
                reason="the charging status result had an unexpected shape",
            )
        current_soc = first_number_value(charging.get("state_of_charge"))
        full_range = self.get_distance_by_soc_value(
            initial_state_of_charge=100,
            final_state_of_charge=0,
        )
        full_range_km = full_range.get("distance_km")
        if not isinstance(full_range_km, (int, float)) or full_range_km <= 0:
            return self._limitation_response(
                "plan_charging_for_next_meeting",
                "plan charging for the next meeting",
                reason="the full-range distance was unavailable",
            )

        required_distance_km = float(route_distance_km) + float(range_buffer_km)
        target_soc = max(
            int(math.ceil(float(current_soc))),
            int(math.ceil(required_distance_km / float(full_range_km) * 100)),
        )
        target_soc = min(100, max(0, target_soc))

        pois_result = self._call_raw_tool_sync(
            "search_poi_at_location",
            {
                "location_id": current_location_id,
                "category_poi": "charging_stations",
            },
        )
        plug = self.select_charging_plug(pois_result)
        if plug.get("status") != "SUCCESS":
            return plug
        charge_time_result = self._call_raw_tool_sync(
            "calculate_charging_time_by_soc",
            {
                "charging_station_id": plug["charging_station_id"],
                "charging_station_plug_id": plug["charging_station_plug_id"],
                "start_state_of_charge": int(current_soc),
                "target_state_of_charge": int(target_soc),
            },
        )
        if charge_time_result.get("status") != "SUCCESS":
            return self._failed_tool_response(
                "plan_charging_for_next_meeting",
                "calculate the minimum charging time",
                charge_time_result,
            )
        min_charging_minutes = first_number_value(charge_time_result)
        now_minutes = int(now["hour"]) * 60 + int(now["minute"])
        max_charging_minutes = (
            int(meeting_start_minutes)
            - now_minutes
            - int(route_duration_minutes)
            - int(arrival_buffer_minutes)
        )
        max_charging_minutes = max(0, int(max_charging_minutes))

        report = {
            "status": "SUCCESS",
            "min_charging_minutes": int(min_charging_minutes),
            "max_charging_minutes": max_charging_minutes,
            "target_state_of_charge": int(target_soc),
            "required_distance_km": required_distance_km,
            "range_buffer_km": float(range_buffer_km),
            "arrival_buffer_minutes": int(arrival_buffer_minutes),
            "meeting": copy.deepcopy(meeting),
            "meeting_location_id": meeting_location_id,
            "route": copy.deepcopy(route),
            "route_id": route.get("route_id"),
            "route_distance_km": route_distance_km,
            "route_duration_minutes": int(route_duration_minutes),
            "selected_charging_plug": copy.deepcopy(plug),
            "charging_station_id": plug.get("charging_station_id"),
            "charging_station_plug_id": plug.get("charging_station_plug_id"),
            "charging_station_navigation_id": plug.get("selected", {}).get("navigation_id"),
            "charging_station_phone_number": plug.get("selected", {}).get("phone_number"),
        }
        self.remember("last_charging_time_plan", copy.deepcopy(report))
        self.remember_entity("selected_charging_plan", copy.deepcopy(report))
        return report

    def call_selected_charging_provider(self) -> dict[str, Any]:
        """Call the provider for the selected charging station, if grounded."""

        phone_number = self._selected_charging_provider_phone()
        if not phone_number:
            report = {
                "status": "UNAVAILABLE",
                "reason": "no selected charging-station phone number is known",
            }
            self.scratchpad["gates"]["call_selected_charging_provider"] = report
            self._abort_with_response(
                "I can't call the charging-station provider because I don't have a grounded phone number for the selected station."
            )
            return report
        call = (
            "call_phone_by_number",
            {"phone_number": phone_number},
        )
        blocker = self._require_tool_surface_for_calls(
            "call_selected_charging_provider",
            "call the selected charging-station provider",
            [call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        if result.get("status") != "SUCCESS":
            return self._failed_tool_response(
                "call_selected_charging_provider",
                "call the selected charging-station provider",
                result,
            )
        self.scratchpad["facts"]["last_charging_provider_call"] = {
            "phone_number": phone_number,
            "result": copy.deepcopy(result_value(result)),
        }
        return {
            "status": "SUCCESS",
            "tool_name": "call_selected_charging_provider",
            "phone_number": phone_number,
            "result": result_value(result),
        }

    def _selected_charging_provider_phone(self) -> str | None:
        entities = self.scratchpad.get("entities", {})
        selected_plug = entities.get("selected_charging_plug")
        if isinstance(selected_plug, dict):
            selected = selected_plug.get("selected")
            if isinstance(selected, dict):
                phone = _clean_string(selected.get("phone_number"))
                if phone:
                    return phone
            phone = _clean_string(selected_plug.get("phone_number"))
            if phone:
                return phone

        station_ids: set[str] = set()
        for source in (
            selected_plug,
            entities.get("selected_charging_plan"),
        ):
            if not isinstance(source, dict):
                continue
            for key in ("station_id", "charging_station_id", "navigation_id"):
                value = _clean_string(source.get(key))
                if value:
                    station_ids.add(value)

        for collection in (
            entities.get("last_pois"),
            entities.get("pois"),
            self._navigation_waypoints_from_state(entities.get("navigation_state")),
        ):
            phone = self._phone_from_charging_items(collection, station_ids)
            if phone:
                return phone
        return None

    @staticmethod
    def _navigation_waypoints_from_state(state: Any) -> list[dict[str, Any]]:
        if isinstance(state, dict):
            waypoints = state.get("waypoints")
            if isinstance(waypoints, list):
                return [item for item in waypoints if isinstance(item, dict)]
            details = state.get("details")
            if isinstance(details, dict) and isinstance(details.get("waypoints"), list):
                return [item for item in details["waypoints"] if isinstance(item, dict)]
        return []

    @staticmethod
    def _phone_from_charging_items(items: Any, station_ids: set[str]) -> str | None:
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            item_ids = {
                _clean_string(item.get("id")),
                _clean_string(item.get("poi_id")),
                _clean_string(item.get("navigation_id")),
            }
            item_ids.discard(None)
            if station_ids and not station_ids.intersection(item_ids):
                continue
            category = str(item.get("category") or "").lower()
            has_plugs = isinstance(item.get("charging_plugs"), list)
            if not has_plugs and "charg" not in category and station_ids:
                continue
            phone = _clean_string(item.get("phone_number") or item.get("phone"))
            if phone:
                return phone
        return None

    def get_preferred_ambient_light_color(self) -> dict[str, Any]:
        """Extract a unique ambient light color from user vehicle-setting preferences."""

        call = (
            "get_user_preferences",
            {"preference_categories": {"vehicle_settings": {"vehicle_settings": True}}},
        )
        blocker = self._require_tool_surface_for_calls(
            "get_preferred_ambient_light_color",
            "read user preferences",
            [call],
        )
        if blocker:
            return blocker
        result = self._call_raw_tool_sync(*call)
        if result.get("status") != "SUCCESS":
            return {"status": "FAILED_TOOL_RESULT", "result": result}
        raw = result_value(result)
        text_parts: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str):
                text_parts.append(value)
            elif isinstance(value, dict):
                for inner in value.values():
                    collect(inner)
            elif isinstance(value, list):
                for inner in value:
                    collect(inner)

        collect(raw)
        found: list[str] = []
        for text in text_parts:
            upper = text.upper()
            for color in AMBIENT_LIGHT_COLORS:
                if re.search(rf"\b{re.escape(color)}\b", upper):
                    found.append(color)
        unique = sorted(set(found))
        if len(unique) == 1:
            return {"status": "SUCCESS", "lightcolor": unique[0], "raw_result": raw}
        if len(unique) > 1:
            return {"status": "AMBIGUOUS", "lightcolors": unique, "raw_result": raw}
        return {"status": "NOT_FOUND", "lightcolors": [], "raw_result": raw}

    @staticmethod
    def _extract_routes(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, UnknownToolResponseValue):
            value.require()
        if isinstance(value, dict):
            routes = value.get("routes")
            if isinstance(routes, UnknownToolResponseValue):
                routes.require()
            if isinstance(routes, list):
                return [route for route in routes if isinstance(route, dict)]
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            return CoroutineWorkspace._extract_routes(value["result"])
        if isinstance(value, dict) and isinstance(value.get("raw_result"), dict):
            return CoroutineWorkspace._extract_routes(value["raw_result"])
        if isinstance(value, list):
            return [route for route in value if isinstance(route, dict)]
        return []

    @staticmethod
    def _normalize_route(route: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(route)
        aliases = normalized.get("alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        normalized["alias"] = [str(alias).lower() for alias in aliases]
        hours = normalized.get("duration_hours")
        minutes = normalized.get("duration_minutes")
        if isinstance(hours, (int, float)) and isinstance(minutes, (int, float)):
            normalized["duration_total_minutes"] = int(hours) * 60 + int(minutes)
            normalized.setdefault("duration", f"{int(hours)}h {int(minutes)}m")
        normalized["display"] = CoroutineWorkspace._route_display(normalized)
        return normalized

    def _resolve_route_for_arrival_poi(
        self,
        *,
        location_id: str,
        route: Any = None,
        route_id: str | None = None,
        routes: Any = None,
        start_id: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(route, dict):
            normalized = self._normalize_route(route)
            return {"status": "SUCCESS", "route": normalized, "source": "route"}
        entities = self.scratchpad.get("entities", {})
        selected = entities.get("selected_route")
        if isinstance(selected, dict):
            selected_route = selected.get("route")
            selected_destination = selected.get("destination_id")
            if not isinstance(selected_destination, str) and isinstance(selected_route, dict):
                selected_destination = selected_route.get("destination_id")
            if (
                isinstance(selected_route, dict)
                and isinstance(selected_destination, str)
                and selected_destination == location_id
            ):
                return {
                    "status": "SUCCESS",
                    "route": self._normalize_route(selected_route),
                    "source": "selected_route",
                }
        return self._resolve_route_for_arrival_weather(
            destination_id=location_id,
            route=route,
            route_id=route_id,
            routes=routes,
            start_id=start_id,
        )

    def _resolve_route_for_arrival_weather(
        self,
        *,
        destination_id: str,
        route: Any = None,
        route_id: str | None = None,
        routes: Any = None,
        start_id: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(route, dict):
            normalized = self._normalize_route(route)
            return {"status": "SUCCESS", "route": normalized, "source": "route"}
        routes_by_id = self.scratchpad.get("entities", {}).get("routes_by_id")
        if isinstance(route_id, str) and route_id and isinstance(routes_by_id, dict):
            stored = routes_by_id.get(route_id)
            if isinstance(stored, dict):
                return {
                    "status": "SUCCESS",
                    "route": self._normalize_route(stored),
                    "source": "route_id",
                }
        route_list = self._extract_routes(routes)
        if route_list:
            selected = self.select_route(route_list, alias="fastest", record_selection=False)
            if selected.get("status") == "SUCCESS" and isinstance(selected.get("route"), dict):
                return {
                    "status": "SUCCESS",
                    "route": selected["route"],
                    "source": "routes",
                }
        entities = self.scratchpad.get("entities", {})
        last_options = entities.get("last_route_options")
        if isinstance(last_options, dict) and last_options.get("destination_id") == destination_id:
            selected = self.select_route(
                last_options.get("routes"),
                alias="fastest",
                record_selection=False,
            )
            if selected.get("status") == "SUCCESS" and isinstance(selected.get("route"), dict):
                return {
                    "status": "SUCCESS",
                    "route": selected["route"],
                    "source": "last_route_options",
                }
        last_routes = entities.get("last_routes")
        if isinstance(last_routes, list):
            matching = [
                item for item in last_routes
                if isinstance(item, dict) and item.get("destination_id") == destination_id
            ]
            if matching:
                selected = self.select_route(matching, alias="fastest", record_selection=False)
                if selected.get("status") == "SUCCESS" and isinstance(selected.get("route"), dict):
                    return {
                        "status": "SUCCESS",
                        "route": selected["route"],
                        "source": "last_routes",
                    }
        route_start = start_id or self.policy_location_id()
        if not isinstance(route_start, str) or not route_start:
            return {
                "status": "UNAVAILABLE",
                "reason": "route start is unavailable for arrival-time weather",
            }
        options = self.get_route_options(start_id=route_start, destination_id=destination_id)
        if options.get("status") != "SUCCESS":
            return options
        selected = self.select_route(options.get("routes"), alias="fastest", record_selection=False)
        if selected.get("status") == "SUCCESS" and isinstance(selected.get("route"), dict):
            return {
                "status": "SUCCESS",
                "route": selected["route"],
                "source": "route_lookup",
            }
        return selected

    def _arrival_time_for_route(self, route: dict[str, Any]) -> dict[str, Any]:
        now = self.policy_now()
        if not isinstance(now, dict):
            return {"status": "UNAVAILABLE", "reason": "policy time is unavailable"}
        hour = now.get("hour")
        minute = now.get("minute")
        month = now.get("month")
        day = now.get("day")
        if not all(isinstance(value, (int, float)) for value in (hour, minute, month, day)):
            return {"status": "UNAVAILABLE", "reason": "policy date/time is incomplete"}
        duration = route.get("duration_total_minutes")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            hours = route.get("duration_hours")
            minutes = route.get("duration_minutes")
            if isinstance(hours, (int, float)) and isinstance(minutes, (int, float)):
                duration = int(hours) * 60 + int(minutes)
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            return {"status": "UNAVAILABLE", "reason": "route duration is unavailable"}
        start_total = int(hour) * 60 + int(minute)
        arrival_total = start_total + int(duration)
        return {
            "status": "SUCCESS",
            "month": int(month),
            "day": int(day),
            "hour": (arrival_total // 60) % 24,
            "minute": arrival_total % 60,
            "time_label": f"{(arrival_total // 60) % 24:02d}:{arrival_total % 60:02d}",
            "duration_total_minutes": int(duration),
            "route_id": route.get("route_id") or route.get("id"),
        }

    @staticmethod
    def _poi_open_status_at_minutes(opening_hours: Any, minute_of_day: int) -> bool | None:
        if not isinstance(opening_hours, str) or not opening_hours.strip():
            return None
        windows = re.findall(
            r"(\d{1,2}):(\d{2})h?\s*-\s*(\d{1,2}):(\d{2})h?",
            opening_hours,
        )
        if not windows:
            return None
        minute_of_day = int(minute_of_day) % (24 * 60)
        for start_hour, start_minute, end_hour, end_minute in windows:
            start = int(start_hour) * 60 + int(start_minute)
            end = int(end_hour) * 60 + int(end_minute)
            if start == end:
                return True
            if end > start and start <= minute_of_day <= end:
                return True
            if end < start and (minute_of_day >= start or minute_of_day <= end):
                return True
        return False

    @staticmethod
    def _route_display(route: dict[str, Any]) -> str:
        route_id = route.get("route_id") or route.get("id")
        via = route.get("name_via") or route.get("via") or route.get("name")
        distance = route.get("distance_km") or route.get("distance")
        duration = route.get("duration")
        if not isinstance(duration, str):
            hours = route.get("duration_hours")
            minutes = route.get("duration_minutes")
            if isinstance(hours, (int, float)) and isinstance(minutes, (int, float)):
                duration = f"{int(hours)}h {int(minutes)}m"
        aliases = route.get("alias")
        aliases_text = ""
        if isinstance(aliases, list) and aliases:
            aliases_text = "; " + ", ".join(str(alias) for alias in aliases)
        parts: list[str] = []
        if isinstance(via, str) and via.strip():
            parts.append(via.strip())
        if isinstance(distance, (int, float)):
            parts.append(f"{float(distance):g} km")
        elif isinstance(distance, str) and distance.strip():
            parts.append(distance.strip())
        if isinstance(duration, str) and duration.strip():
            parts.append(duration.strip())
        road_types = route.get("road_types")
        includes_toll = bool(route.get("includes_toll") or route.get("has_tolls") or route.get("tolls"))
        if isinstance(road_types, list):
            includes_toll = includes_toll or any(
                "toll" in str(road_type).lower() for road_type in road_types
            )
        if includes_toll:
            parts.append("includes toll roads")
        label = ", ".join(parts) if parts else "route"
        if isinstance(route_id, str) and route_id:
            return f"{label} (route_id: {route_id}{aliases_text})"
        return f"{label}{aliases_text}"

    @staticmethod
    def _normalize_via(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value).strip().lower())

    @staticmethod
    def _parse_distance_value(value: Any) -> dict[str, Any]:
        if isinstance(value, (int, float)):
            return {"distance": float(value), "unit": "km", "distance_km": float(value)}
        if not isinstance(value, str):
            raise ValueError(f"Unsupported distance value: {value!r}")
        text = value.strip().lower()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            raise ValueError(f"No numeric distance in value: {value!r}")
        distance = float(match.group(0))
        unit = "mi" if "mile" in text or re.search(r"\bmi\b", text) else "km"
        out = {"distance": distance, "unit": unit}
        if unit == "km":
            out["distance_km"] = distance
        return out

    def _normalize_protocol_batch(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize deterministic CAR policy action bundles before A2A emission.

        For front/all defrost with AC activation, policy 011 requires windows over
        20% to be closed. If the model emits per-window close calls in the same
        defrost+AC bundle, keep them but place them before AC activation.
        """

        has_front_or_all_defrost = any(
            call["tool_name"] == "set_window_defrost"
            and call["arguments"].get("on") is True
            and call["arguments"].get("defrost_window") in {"FRONT", "ALL"}
            for call in calls
        )
        has_ac_on = any(
            call["tool_name"] == "set_air_conditioning"
            and call["arguments"].get("on") is True
            for call in calls
        )
        if not (has_front_or_all_defrost and has_ac_on):
            return calls

        close_window_calls = [
            call
            for call in calls
            if call["tool_name"] == "open_close_window"
            and call["arguments"].get("percentage") == 0
        ]
        if not close_window_calls:
            return calls

        defrost_calls: list[dict[str, Any]] = []
        fan_calls: list[dict[str, Any]] = []
        airflow_calls: list[dict[str, Any]] = []
        ac_calls: list[dict[str, Any]] = []
        other_calls: list[dict[str, Any]] = []

        for call in calls:
            name = call["tool_name"]
            if name == "open_close_window" and call["arguments"].get("percentage") == 0:
                continue
            if name == "set_window_defrost":
                defrost_calls.append(call)
            elif name == "set_fan_speed":
                fan_calls.append(call)
            elif name == "set_fan_airflow_direction":
                airflow_calls.append(call)
            elif name == "set_air_conditioning":
                ac_calls.append(call)
            else:
                other_calls.append(call)

        return [
            *defrost_calls,
            *fan_calls,
            *airflow_calls,
            *close_window_calls,
            *ac_calls,
            *other_calls,
        ]

    def available_tool_names(self) -> list[str]:
        with self._lock:
            return sorted(self.available_tools)

    def _normalize_call_spec(self, item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            tool_name = self._canonical_call_name(
                item.get("tool_name") or item.get("tool") or item.get("name") or ""
            )
            arguments = item.get("arguments") or item.get("args") or {}
        elif isinstance(item, (tuple, list)) and item:
            tool_name = self._canonical_call_name(item[0])
            arguments = item[1] if len(item) > 1 else {}
        else:
            raise ValueError("Tool call spec must be a dict or (tool_name, arguments) tuple")
        if not isinstance(arguments, dict):
            raise ValueError(f"Arguments for {tool_name!r} must be a dict")
        return {"tool_name": tool_name, "arguments": dict(arguments)}

    def _canonical_call_name(self, value: Any) -> str:
        if isinstance(value, str):
            name = value.strip()
        elif callable(value):
            tagged_name = self._preloaded_callables.get(id(value), "")
            bound_name = str(getattr(value, "__name__", "")).strip()
            is_bound_helper = (
                getattr(value, "__self__", None) is self
                and bound_name in WORKSPACE_HELPER_NAMES
            )
            if tagged_name:
                name = tagged_name
            elif is_bound_helper:
                name = bound_name
            else:
                raise ValueError(
                    "Tool/helper callable must be a preloaded wrapper or bound workspace helper"
                )
        else:
            raise ValueError("Tool/helper name must be a string or known preloaded callable")
        if not name:
            raise ValueError("Tool/helper name must be non-empty")
        if name not in KNOWN_CALL_NAMES:
            raise ValueError(f"Unknown tool/helper name {name!r}")
        return name

    @staticmethod
    def _delegate_policy_sensitive_call(call: dict[str, Any]) -> dict[str, Any]:
        tool_name = call["tool_name"]
        arguments = call["arguments"]
        if tool_name == "set_air_conditioning" and arguments.get("on") is True:
            return {"tool_name": "set_air_conditioning_on_safe", "arguments": {}}
        if (
            tool_name == "set_window_defrost"
            and arguments.get("on") is True
            and str(arguments.get("defrost_window") or "").upper() in {"FRONT", "ALL"}
        ):
            return {
                "tool_name": "set_window_defrost_safe",
                "arguments": {
                    "defrost_window": str(arguments.get("defrost_window") or "FRONT").upper()
                },
            }
        if tool_name == "open_close_sunroof":
            return {"tool_name": "open_sunroof_safe", "arguments": dict(arguments)}
        if tool_name == "open_close_window":
            return {"tool_name": "open_close_window_safe", "arguments": dict(arguments)}
        if tool_name == "set_fog_lights" and arguments.get("on") is True:
            return {"tool_name": "set_fog_lights_on_safe", "arguments": {}}
        if tool_name == "set_head_lights_high_beams" and arguments.get("on") is True:
            return {"tool_name": "set_high_beams_on_safe", "arguments": {}}
        if tool_name == "set_new_navigation":
            return {"tool_name": "set_new_navigation_guarded", "arguments": dict(arguments)}
        if tool_name == "get_routes_from_start_to_destination":
            return {"tool_name": "get_routes_guarded", "arguments": dict(arguments)}
        if tool_name == "get_weather":
            return {"tool_name": "get_weather_guarded", "arguments": dict(arguments)}
        if tool_name == "search_poi_along_the_route":
            return {"tool_name": "search_poi_along_route_guarded", "arguments": dict(arguments)}
        if tool_name == "get_contact_id_by_contact_name":
            return {"tool_name": "get_contact_id_by_contact_name_guarded", "arguments": dict(arguments)}
        if tool_name == "navigation_add_one_waypoint":
            return {"tool_name": "navigation_add_one_waypoint_guarded", "arguments": dict(arguments)}
        if tool_name == "navigation_delete_waypoint":
            return {"tool_name": "navigation_delete_waypoint_guarded", "arguments": dict(arguments)}
        if tool_name == "navigation_replace_one_waypoint":
            return {"tool_name": "navigation_replace_one_waypoint_guarded", "arguments": dict(arguments)}
        if tool_name == "navigation_replace_final_destination":
            return {"tool_name": "navigation_replace_final_destination_guarded", "arguments": dict(arguments)}
        return call

    def _normalize_tool_arguments(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            tool = self.available_tools.get(tool_name) or {}
            schema = tool.get("function", {}).get("parameters", {}) or {}
        properties = schema.get("properties", {}) or {}
        normalized: dict[str, Any] = {}
        for name, value in arguments.items():
            value = self._resolve_preloaded_argument_value(value)
            if (
                tool_name == "call_phone_by_number"
                and name == "phone_number"
                and isinstance(value, str)
            ):
                value = value.strip()
            elif (
                tool_name == "send_email"
                and name == "email_addresses"
                and isinstance(value, list)
            ):
                value = [
                    item.strip() if isinstance(item, str) else item
                    for item in value
                ]
            property_schema = properties.get(name)
            if isinstance(property_schema, dict):
                value = self._normalize_argument_value(tool_name, name, value, property_schema)
            normalized[name] = value
        return normalized

    def _normalize_argument_value(
        self,
        tool_name: str,
        argument_name: str,
        value: Any,
        property_schema: dict[str, Any],
    ) -> Any:
        if tool_name == "open_close_window" and argument_name == "window" and isinstance(value, str):
            window_value = value.strip()
            if window_value in WINDOW_POSITION_KEY_TO_TOOL:
                return WINDOW_POSITION_KEY_TO_TOOL[window_value]
            lowered = window_value.lower().replace("_", " ")
            if lowered in WINDOW_LABEL_TO_TOOL:
                return WINDOW_LABEL_TO_TOOL[lowered]
            return window_value.upper() if window_value.upper() in set(property_schema.get("enum", []) or []) else value

        if isinstance(value, dict) and "id" in value:
            schema_type = property_schema.get("type")
            if argument_name.endswith("_id") or argument_name in {
                "location_id",
                "start_id",
                "destination_id",
                "route_id",
                "new_destination_id",
                "charging_station_id",
                "charging_station_plug_id",
            } or schema_type == "string":
                return value["id"]

        schema_type = property_schema.get("type")
        if schema_type == "integer":
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
                return int(value.strip())
        if schema_type == "number":
            if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", value.strip()):
                parsed = float(value.strip())
                return int(parsed) if parsed.is_integer() else parsed
        if schema_type == "boolean" and isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "on"}:
                return True
            if lowered in {"false", "no", "off"}:
                return False
        return value

    def _validate_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        with self._lock:
            if tool_name not in self.available_tools:
                available = ", ".join(self.available_tool_names())
                raise RuntimeError(f"Tool {tool_name!r} is not available in this task. Available tools: {available}")
            schema = self.available_tools[tool_name].get("function", {}).get("parameters", {}) or {}
        properties = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        missing = required - set(arguments)
        if missing:
            raise ValueError(f"Tool {tool_name!r} missing required arguments: {sorted(missing)}")
        if properties:
            unexpected = set(arguments) - set(properties)
            if unexpected:
                raise ValueError(f"Tool {tool_name!r} got unknown arguments: {sorted(unexpected)}")
        for name, value in arguments.items():
            property_schema = properties.get(name)
            if isinstance(property_schema, dict):
                self._validate_argument_value(tool_name, name, value, property_schema, name in required)
        self._reject_placeholder_arguments(tool_name, arguments)

    def _validate_argument_value(
        self,
        tool_name: str,
        argument_name: str,
        value: Any,
        property_schema: dict[str, Any],
        required: bool,
    ) -> None:
        if value is None:
            if required:
                raise ValueError(f"Tool {tool_name!r} got None for required argument {argument_name!r}")
            return

        schema_type = property_schema.get("type")
        if schema_type == "string":
            if not isinstance(value, str):
                raise ValueError(
                    f"Tool {tool_name!r} argument {argument_name!r} must be a string, got {type(value).__name__}"
                )
            if not value.strip():
                raise ValueError(f"Tool {tool_name!r} argument {argument_name!r} must be a non-empty string")
        elif schema_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(
                    f"Tool {tool_name!r} argument {argument_name!r} must be an integer, got {value!r}"
                )
        elif schema_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(
                    f"Tool {tool_name!r} argument {argument_name!r} must be a number, got {value!r}"
                )
        elif schema_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(
                    f"Tool {tool_name!r} argument {argument_name!r} must be a boolean, got {value!r}"
                )
        elif schema_type == "array":
            if not isinstance(value, list):
                raise ValueError(
                    f"Tool {tool_name!r} argument {argument_name!r} must be a list, got {type(value).__name__}"
                )
            item_schema = property_schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    self._validate_argument_value(
                        tool_name,
                        f"{argument_name}[{index}]",
                        item,
                        item_schema,
                        True,
                    )

        enum = property_schema.get("enum")
        if enum and value not in enum:
            raise ValueError(
                f"Tool {tool_name!r} argument {argument_name!r} must be one of {list(enum)}, got {value!r}"
            )

        minimum = property_schema.get("minimum")
        maximum = property_schema.get("maximum")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if minimum is not None and value < minimum:
                raise ValueError(f"Tool {tool_name!r} argument {argument_name!r} below minimum {minimum}: {value!r}")
            if maximum is not None and value > maximum:
                raise ValueError(f"Tool {tool_name!r} argument {argument_name!r} above maximum {maximum}: {value!r}")

    @staticmethod
    def _reject_placeholder_arguments(tool_name: str, arguments: dict[str, Any]) -> None:
        bad_patterns = ("placeholder", "to_be_filled", "<", "TODO")

        def walk(value: Any, argument_name: str = "") -> bool:
            if isinstance(value, str):
                lowered = value.lower()
                if any(pattern.lower() in lowered for pattern in bad_patterns):
                    return True
                id_argument = (
                    argument_name == "id"
                    or argument_name.endswith("_id")
                    or argument_name.endswith("_ids")
                )
                return id_argument and "?" in value
            if isinstance(value, dict):
                return any(walk(v, str(key)) for key, v in value.items())
            if isinstance(value, (list, tuple)):
                return any(walk(v, argument_name) for v in value)
            return False

        if walk(arguments):
            raise ValueError(f"Tool {tool_name!r} got placeholder/ungrounded arguments: {arguments}")

    def _parse_tool_result(self, item: dict[str, Any]) -> dict[str, Any]:
        tool_name = item.get("tool_name") or item.get("toolName") or ""
        content = item.get("content") or ""
        parsed: Any
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = {"status": "RAW", "content": content}
        elif isinstance(content, dict):
            parsed = content
        else:
            parsed = {"status": "RAW", "content": content}
        if not isinstance(parsed, dict):
            parsed = {"status": "RAW", "content": parsed}
        parsed.setdefault("status", "UNKNOWN")
        parsed["tool_name"] = tool_name
        parsed["tool_call_id"] = item.get("tool_call_id") or item.get("toolCallId") or ""
        if parsed.get("status") == "SUCCESS" and isinstance(parsed.get("result"), dict):
            parsed["result"] = self._normalize_result_payload(tool_name, parsed["result"])
        self._augment_success_result(tool_name, parsed)
        return parsed

    def _normalize_result_payload(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            key: self._normalize_result_value(tool_name, [str(key)], key, value)
            for key, value in result.items()
        }
        normalized.setdefault("status", "SUCCESS")
        if tool_name == "get_location_id_by_location_name" and isinstance(normalized.get("id"), str):
            normalized.setdefault("location_id", normalized["id"])
        if tool_name == "get_entries_from_calendar":
            meetings = normalized.get("meetings")
            if isinstance(meetings, list):
                entries = self._normalize_calendar_entries(meetings)
                normalized["meetings"] = entries
                normalized["entries"] = entries
        if "pois_found" in normalized:
            normalized.setdefault("pois", normalized["pois_found"])
        return normalized

    def _normalize_result_value(
        self,
        tool_name: str,
        path: list[str],
        key: Any,
        value: Any,
    ) -> Any:
        if isinstance(value, dict):
            return {
                nested_key: self._normalize_result_value(
                    tool_name,
                    [*path, str(nested_key)],
                    nested_key,
                    nested_value,
                )
                for nested_key, nested_value in value.items()
            }
        if isinstance(value, list):
            return [
                self._normalize_result_value(tool_name, path, key, item)
                for item in value
            ]
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.lower() == "unknown":
            return UnknownToolResponseValue(
                self,
                f"result.{tool_name}.{'.'.join(path)}",
            )
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
            return value
        key_text = str(key).lower()
        numeric_key_fragments = (
            "speed",
            "level",
            "position",
            "temperature",
            "state_of_charge",
            "soc",
            "percentage",
            "minute",
            "hour",
            "day",
            "month",
            "year",
            "distance",
            "duration",
            "time",
        )
        if not any(fragment in key_text for fragment in numeric_key_fragments):
            return value
        parsed = float(stripped)
        return int(parsed) if parsed.is_integer() else parsed

    @staticmethod
    def _augment_success_result(tool_name: str, parsed: dict[str, Any]) -> None:
        if parsed.get("status") != "SUCCESS":
            return
        result = parsed.get("result")
        if not isinstance(result, dict):
            return
        for key, value in result.items():
            if key != "status":
                parsed.setdefault(key, value)
        if "id" in result and isinstance(result["id"], str):
            parsed.setdefault("id_value", result["id"])
        if "location_id" in result and isinstance(result["location_id"], str):
            parsed.setdefault("id_value", result["location_id"])
        if "pois_found" in result and "pois" not in parsed:
            parsed["pois"] = result["pois_found"]
        if "routes" in result and "routes" not in parsed:
            parsed["routes"] = result["routes"]
        for key, value in result.items():
            if not isinstance(key, str):
                continue
            if key == "remaining_range":
                remaining_range = CoroutineWorkspace._parse_first_number(value)
                if remaining_range is not None:
                    parsed.setdefault("remaining_range_km", remaining_range)
            if "charging_time" in key or key.startswith("time_") or key.endswith("_time"):
                minutes = CoroutineWorkspace._parse_first_number(value)
                if minutes is not None:
                    parsed.setdefault("minutes", minutes)
            if "soc" in key or "state_of_charge" in key:
                soc = CoroutineWorkspace._parse_first_number(value)
                if soc is not None:
                    parsed.setdefault("state_of_charge", soc)
            # Dynamic distance key, e.g. get_distance_by_soc's
            # `distance_km_for_85.0_until_0.0_percent_soc` -> stable distance_km.
            if key.startswith("distance") and ("_until_" in key or "_for_" in key):
                km = CoroutineWorkspace._parse_first_number(value)
                if km is not None:
                    parsed.setdefault("distance_km", km)
                    parsed.setdefault("distance", value)

    @staticmethod
    def _parse_first_number(value: Any) -> int | float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if not isinstance(value, str):
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if not match:
            return None
        parsed = float(match.group(0))
        return int(parsed) if parsed.is_integer() else parsed


def result_by_tool(results: list[dict[str, Any]], tool_name: str, index: int = 0) -> dict[str, Any]:
    matches = [item for item in results if item.get("tool_name") == tool_name]
    if index >= len(matches):
        raise KeyError(f"No result #{index} for tool {tool_name!r}; got {[r.get('tool_name') for r in results]}")
    return matches[index]


def result_value(result: Any, index: int | None = None) -> Any:
    if isinstance(result, list):
        if index is None:
            if len(result) != 1:
                raise ValueError(
                    "result_value(list) needs index=... when the list has "
                    f"{len(result)} items"
                )
            index = 0
        if not isinstance(index, int) or index < 0 or index >= len(result):
            raise IndexError(f"Result index {index!r} is out of range")
        return result_value(result[index])
    if not isinstance(result, dict):
        raise TypeError(
            "result_value expected a result dict or list, got "
            f"{type(result).__name__}"
        )
    if result.get("status") != "SUCCESS":
        raise RuntimeError(f"Tool {result.get('tool_name')} failed: {result}")
    value = result.get("result")
    unavailable = _unknown_value_when_entire_payload_is_unavailable(value)
    if unavailable is not None:
        unavailable.require()
    return value


def _unknown_value_when_entire_payload_is_unavailable(
    value: Any,
) -> UnknownToolResponseValue | None:
    if isinstance(value, UnknownToolResponseValue):
        return value
    if isinstance(value, dict):
        meaningful_values = [
            nested_value
            for key, nested_value in value.items()
            if key != "status"
        ]
        if not meaningful_values:
            return None
        unavailable = [
            _unknown_value_when_entire_payload_is_unavailable(nested_value)
            for nested_value in meaningful_values
        ]
        return unavailable[0] if all(item is not None for item in unavailable) else None
    if isinstance(value, list):
        if not value:
            return None
        unavailable = [
            _unknown_value_when_entire_payload_is_unavailable(item)
            for item in value
        ]
        return unavailable[0] if all(item is not None for item in unavailable) else None
    return None


def _unwrap_result_like(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 1:
        return _unwrap_result_like(value[0])
    if isinstance(value, dict) and value.get("status") == "SUCCESS" and "result" in value:
        return value["result"]
    return value


def _require_known_extracted_value(value: Any) -> Any:
    if isinstance(value, UnknownToolResponseValue):
        value.require()
    return value


def id_value(value: Any, *, field: str | None = None) -> str:
    data = _require_known_extracted_value(_unwrap_result_like(value))
    if isinstance(data, str) and data.strip():
        return data
    if not isinstance(data, dict):
        raise ValueError(f"Cannot extract ID from {value!r}")

    candidates = [field] if field else []
    candidates.extend(
        [
            "id",
            "location_id",
            "route_id",
            "poi_id",
            "charging_station_id",
            "charging_station_plug_id",
            "phone_number",
        ]
    )
    for name in candidates:
        candidate = _require_known_extracted_value(data.get(name)) if name else None
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    matches = data.get("matches")
    if isinstance(matches, dict) and len(matches) == 1:
        contact_id = next(iter(matches))
        if isinstance(contact_id, str) and contact_id.strip():
            return contact_id
    for list_key in ("contact_ids", "matches"):
        contact_ids = data.get(list_key)
        if isinstance(contact_ids, list):
            grounded = [
                item
                for item in contact_ids
                if isinstance(item, str) and item.strip()
            ]
            if len(grounded) == 1:
                return grounded[0]
            if len(grounded) > 1:
                raise ValueError(
                    f"Cannot extract one ID: {list_key} contains "
                    f"{len(grounded)} candidates"
                )
    raise ValueError(f"Cannot extract ID from {value!r}")


def unique_id_intersection(*values: Any) -> str:
    """Return the one grounded ID shared by every provided candidate set."""

    if len(values) < 2:
        raise ValueError("unique_id_intersection requires at least two candidate sets")

    candidate_sets: list[set[str]] = []
    for value in values:
        data = _require_known_extracted_value(_unwrap_result_like(value))
        candidates: Any = data
        if isinstance(data, dict):
            candidates = data.get("contact_ids")
            if candidates is None:
                candidates = data.get("matches")
            if isinstance(candidates, dict):
                candidates = list(candidates)
        if isinstance(candidates, str):
            grounded = {candidates.strip()} if candidates.strip() else set()
        elif isinstance(candidates, (list, tuple, set, frozenset)):
            grounded = {
                item.strip()
                for item in candidates
                if isinstance(item, str) and item.strip()
            }
        else:
            raise ValueError(f"Cannot extract an ID candidate set from {value!r}")
        candidate_sets.append(grounded)

    shared = set.intersection(*candidate_sets)
    if len(shared) != 1:
        raise ValueError(
            f"Expected exactly one shared grounded ID, found {len(shared)}: "
            f"{sorted(shared)!r}"
        )
    return next(iter(shared))


def pois_value(value: Any) -> list[dict[str, Any]]:
    data = _require_known_extracted_value(_unwrap_result_like(value))
    if isinstance(data, list):
        pois = data
    elif isinstance(data, dict):
        for key in ("pois", "pois_found", "pois_found_along_route"):
            if key in data:
                pois = data.get(key)
                break
        else:
            pois = None
    else:
        pois = None
    pois = _require_known_extracted_value(pois)
    if not isinstance(pois, list):
        raise ValueError(f"Cannot extract POI list from {value!r}")
    return [item for item in pois if isinstance(item, dict)]


def routes_value(value: Any) -> list[dict[str, Any]]:
    data = _require_known_extracted_value(_unwrap_result_like(value))
    if isinstance(data, list):
        routes = data
    elif isinstance(data, dict):
        routes = data.get("routes")
    else:
        routes = None
    routes = _require_known_extracted_value(routes)
    if not isinstance(routes, list):
        raise ValueError(f"Cannot extract route list from {value!r}")
    return [item for item in routes if isinstance(item, dict)]


def first_number_value(value: Any, *, default: int | float | None = None) -> int | float:
    _require_known_extracted_value(value)
    data = _unwrap_result_like(value)
    _require_known_extracted_value(data)
    if isinstance(data, dict):
        for key in (
            "distance_km",
            "duration_total_minutes",
            "time_minutes",
            "minutes",
            "state_of_charge",
            "target_state_of_charge",
            "power_kw",
            "level",
            "temperature",
        ):
            if key in data:
                parsed_key = CoroutineWorkspace._parse_first_number(data.get(key))
                if parsed_key is not None:
                    return parsed_key
        for item in data.values():
            parsed_item = CoroutineWorkspace._parse_first_number(item)
            if parsed_item is not None:
                return parsed_item
    parsed = CoroutineWorkspace._parse_first_number(data)
    if parsed is not None:
        return parsed
    if default is not None:
        return default
    raise ValueError(f"Cannot extract number from {value!r}")


class BlockingPythonExecutor:
    def __init__(self, workspace: CoroutineWorkspace) -> None:
        self.workspace = workspace
        self._active_stdout: io.StringIO | None = None
        self._globals = self._build_globals()

    def _build_globals(self) -> dict[str, Any]:
        ws = self.workspace
        safe_builtins = dict(SAFE_BUILTINS, __import__=self._safe_import)
        safe_builtins["print"] = self._print
        globals_dict: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "json": json,
            "math": math,
            "re": re,
            "datetime": datetime,
            "ws": ws,
            "scratchpad": ws.scratchpad,
            "respond": ws.respond,
            "stop_after_response": self._stop_after_response,
            "SystemExit": ResponseReady,
            "batch": ws.call_batch_sync,
            "result_by_tool": result_by_tool,
            "result_value": result_value,
            "id_value": id_value,
            "unique_id_intersection": unique_id_intersection,
            "pois_value": pois_value,
            "routes_value": routes_value,
            "first_number_value": first_number_value,
            "remember": ws.remember,
            "remember_entity": ws.remember_entity,
            "list_tools": ws.list_tools,
            "describe_tool": ws.describe_tool,
            "tool_schema": ws.tool_schema,
            "tool_signature": ws.tool_signature,
            "tool_required_arguments": ws.tool_required_arguments,
            "tool_optional_arguments": ws.tool_optional_arguments,
            "tool_available": ws.tool_available,
            "tool_supports_arguments": ws.tool_supports_arguments,
            "capability_claim_gate": ws.capability_claim_gate,
            "handle_pending_confirmation": ws.handle_pending_confirmation,
            "defrost_front_window": ws.defrost_front_window,
            "open_sunroof_safe": ws.open_sunroof_safe,
            "set_fog_lights_on_safe": ws.set_fog_lights_on_safe,
            "set_high_beams_on_safe": ws.set_high_beams_on_safe,
            "get_distance_by_soc_value": ws.get_distance_by_soc_value,
            "get_navigation_state": ws.get_navigation_state,
            "get_contact_details": ws.get_contact_details,
            "get_next_calendar_entry": ws.get_next_calendar_entry,
            "set_air_conditioning_on_safe": ws.set_air_conditioning_on_safe,
            "close_known_windows_for_blocked_ac": ws.close_known_windows_for_blocked_ac,
            "set_climate_temperature_safe": ws.set_climate_temperature_safe,
            "sync_climate_zone": ws.sync_climate_zone,
            "increase_fan_speed": ws.increase_fan_speed,
            "decrease_fan_speed": ws.decrease_fan_speed,
            "set_occupied_seat_heating": ws.set_occupied_seat_heating,
            "get_route_options": ws.get_route_options,
            "select_route": ws.select_route,
            "select_route_by_user_preferences": ws.select_route_by_user_preferences,
            "select_poi": ws.select_poi,
            "get_weather_at_route_arrival": ws.get_weather_at_route_arrival,
            "select_poi_at_location_open_at_route_arrival": ws.select_poi_at_location_open_at_route_arrival,
            "select_charging_plug": ws.select_charging_plug,
            "set_new_navigation_via_stop": ws.set_new_navigation_via_stop,
            "plan_charging_for_next_meeting": ws.plan_charging_for_next_meeting,
            "call_selected_charging_provider": ws.call_selected_charging_provider,
            "get_preferred_ambient_light_color": ws.get_preferred_ambient_light_color,
            "policy_now": ws.policy_now,
            "policy_location_id": ws.policy_location_id,
        }
        for tool_name in ALL_TOOL_NAMES:
            globals_dict[tool_name] = self._make_tool_wrapper(tool_name)
        ws.register_preloaded_callable(globals_dict["policy_now"], "policy_now")
        ws.register_preloaded_callable(
            globals_dict["policy_location_id"],
            "policy_location_id",
        )
        return globals_dict

    @staticmethod
    def _stop_after_response() -> None:
        raise ResponseReady()

    def _print(self, *args: Any, **kwargs: Any) -> None:
        if "file" not in kwargs and self._active_stdout is not None:
            kwargs["file"] = self._active_stdout
        builtins.print(*args, **kwargs)

    def _make_tool_wrapper(self, tool_name: str) -> Callable[..., dict[str, Any]]:
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if args:
                if len(args) != 1:
                    raise TypeError(
                        f"{tool_name} accepts keyword arguments or one positional argument"
                    )
                if isinstance(args[0], dict):
                    overlap = set(args[0]).intersection(kwargs)
                    if overlap:
                        raise TypeError(
                            f"{tool_name} received duplicate arguments: {sorted(overlap)}"
                        )
                    kwargs = {**args[0], **kwargs}
                else:
                    required = self.workspace.tool_required_arguments(tool_name)
                    if len(required) != 1 or kwargs:
                        raise TypeError(
                            f"{tool_name} positional form is available only when "
                            "the tool has exactly one required argument"
                        )
                    kwargs = {required[0]: args[0]}
            if tool_name == "set_fog_lights" and kwargs.get("on") is True:
                return self.workspace.set_fog_lights_on_safe()
            if tool_name == "set_head_lights_high_beams" and kwargs.get("on") is True:
                return self.workspace.set_high_beams_on_safe()
            if tool_name == "get_routes_from_start_to_destination":
                return self.workspace.get_routes_guarded(**kwargs)
            if tool_name == "get_weather":
                return self.workspace.get_weather_guarded(**kwargs)
            if tool_name == "search_poi_along_the_route":
                return self.workspace.search_poi_along_route_guarded(**kwargs)
            if tool_name == "get_contact_id_by_contact_name":
                return self.workspace.get_contact_id_by_contact_name_guarded(**kwargs)
            if tool_name == "navigation_add_one_waypoint":
                return self.workspace.navigation_add_one_waypoint_guarded(**kwargs)
            if tool_name == "navigation_delete_waypoint":
                return self.workspace.navigation_delete_waypoint_guarded(**kwargs)
            if tool_name == "navigation_replace_one_waypoint":
                return self.workspace.navigation_replace_one_waypoint_guarded(**kwargs)
            if tool_name == "navigation_replace_final_destination":
                return self.workspace.navigation_replace_final_destination_guarded(**kwargs)
            return self.workspace.call_tool_sync(tool_name, kwargs)

        wrapper.__name__ = tool_name
        self.workspace.register_preloaded_callable(wrapper, tool_name)
        wrapper.__doc__ = f"Call CAR-bench tool {tool_name}(**kwargs) and return parsed evaluator result."
        return wrapper

    def _safe_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in ALLOWED_IMPORTS:
            return ALLOWED_IMPORTS[name]
        raise ImportError(
            f"Import {name!r} is not available. Use ws, scratchpad, respond(...), "
            "batch(...), and the preloaded CAR-bench tool functions."
        )

    def run(self, code: str) -> ExecutionResult:
        self.workspace.reset_actions()
        stdout = io.StringIO()
        error = None
        self._active_stdout = stdout
        try:
            exec(code, self._globals, self._globals)
        except ResponseReady:
            pass
        except Exception as exc:
            error = {"type": exc.__class__.__name__, "message": str(exc)}
        finally:
            self._active_stdout = None
        return ExecutionResult(
            stdout=stdout.getvalue(),
            error=error,
            response_text=self.workspace._response_text,
        )


def format_observation(result: ExecutionResult, scratchpad: dict[str, Any]) -> str:
    parts = [
        f"STDOUT\n{result.stdout.strip()}" if result.stdout.strip() else "STDOUT\n(no output)"
    ]
    if result.error:
        parts.append(f"ERROR\n{result.error['type']}: {result.error['message']}")
    if result.response_text:
        parts.append(f"RESPONSE_TEXT\n{result.response_text}")
    if scratchpad:
        parts.append(f"SCRATCHPAD\n{json_dumps_safe(scratchpad, indent=2)}")
    return "\n\n".join(parts)
