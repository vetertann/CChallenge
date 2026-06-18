"""Blocking Python workspace for the coroutine-bridge CAR-bench agent."""

from __future__ import annotations

import builtins
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
    "handle_missing_requested_capability",
    "get_distance_by_soc_value",
    "get_navigation_state",
    "get_contact_details",
    "defrost_front_window",
    "open_sunroof_safe",
    "set_fog_lights_on_safe",
    "set_high_beams_on_safe",
    "set_air_conditioning_on_safe",
    "close_known_windows_for_blocked_ac",
    "set_climate_temperature_safe",
    "get_route_options",
    "select_route",
    "get_preferred_ambient_light_color",
)

KNOWN_CALL_NAMES = frozenset([*ALL_TOOL_NAMES, *WORKSPACE_HELPER_NAMES])
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

REMOVED_REQUIRED_PARAMETER_HINTS = {
    ("get_user_preferences", "preference_categories"): TOOL_TARGET_HINTS["get_user_preferences"],
    ("calculate_math", "expression"): TOOL_TARGET_HINTS["calculate_math"],
    ("calculate_datetime", "original_datetime"): TOOL_TARGET_HINTS["calculate_datetime"],
    ("calculate_datetime", "times_to_add"): TOOL_TARGET_HINTS["calculate_datetime"],
    ("think", "thought"): TOOL_TARGET_HINTS["think"],
    ("planning_tool", "command"): TOOL_TARGET_HINTS["planning_tool"],
    ("open_close_window", "percentage"): ("window", "windows"),
    ("open_close_window", "window"): ("window", "windows"),
    ("open_close_sunshade", "percentage"): ("sunshade", "shade"),
    ("open_close_sunroof", "percentage"): ("sunroof",),
    ("open_close_trunk_door", "action"): ("trunk", "boot"),
    ("set_air_circulation", "mode"): TOOL_TARGET_HINTS["set_air_circulation"],
    ("set_air_conditioning", "on"): TOOL_TARGET_HINTS["set_air_conditioning"],
    ("set_ambient_lights", "on"): ("ambient", "light", "lighting"),
    ("set_fan_speed", "level"): ("fan", "air conditioning", " ac ", "a/c"),
    ("set_ambient_lights", "lightcolor"): ("ambient", "light", "lighting", "color", "colour"),
    ("set_climate_temperature", "temperature"): TOOL_TARGET_HINTS["set_climate_temperature"],
    ("set_climate_temperature", "seat_zone"): TOOL_TARGET_HINTS["set_climate_temperature"],
    ("set_fan_airflow_direction", "direction"): TOOL_TARGET_HINTS["set_fan_airflow_direction"],
    ("set_fog_lights", "on"): TOOL_TARGET_HINTS["set_fog_lights"],
    ("set_head_lights_high_beams", "on"): TOOL_TARGET_HINTS["set_head_lights_high_beams"],
    ("set_head_lights_low_beams", "on"): TOOL_TARGET_HINTS["set_head_lights_low_beams"],
    ("set_reading_light", "position"): ("reading light",),
    ("set_reading_light", "on"): ("reading light",),
    ("set_seat_heating", "level"): ("seat heating", "seat heater", "heated seat", "warm seat"),
    ("set_seat_heating", "seat_zone"): ("seat heating", "seat heater", "heated seat", "warm seat"),
    ("set_steering_wheel_heating", "level"): TOOL_TARGET_HINTS["set_steering_wheel_heating"],
    ("set_window_defrost", "on"): TOOL_TARGET_HINTS["set_window_defrost"],
    ("set_window_defrost", "defrost_window"): TOOL_TARGET_HINTS["set_window_defrost"],
    ("get_weather", "location_or_poi_id"): TOOL_TARGET_HINTS["get_weather"],
    ("get_weather", "month"): TOOL_TARGET_HINTS["get_weather"],
    ("get_weather", "day"): TOOL_TARGET_HINTS["get_weather"],
    ("get_weather", "time_hour_24hformat"): TOOL_TARGET_HINTS["get_weather"],
    ("search_poi_at_location", "location_id"): TOOL_TARGET_HINTS["search_poi_at_location"],
    ("search_poi_along_the_route", "route_id"): TOOL_TARGET_HINTS["search_poi_along_the_route"],
    ("get_routes_from_start_to_destination", "start_id"): TOOL_TARGET_HINTS["get_routes_from_start_to_destination"],
    ("get_routes_from_start_to_destination", "destination_id"): TOOL_TARGET_HINTS["get_routes_from_start_to_destination"],
    ("convert_route_distance_and_time", "route_id"): TOOL_TARGET_HINTS["convert_route_distance_and_time"],
    ("set_new_navigation", "route_ids"): ("navigation", "navigate", "route", "directions"),
    ("navigation_add_one_waypoint", "waypoint_id_to_add"): TOOL_TARGET_HINTS["navigation_add_one_waypoint"],
    ("navigation_add_one_waypoint", "waypoint_id_before_new_waypoint"): TOOL_TARGET_HINTS["navigation_add_one_waypoint"],
    ("navigation_add_one_waypoint", "route_id_leading_to_new_waypoint"): TOOL_TARGET_HINTS["navigation_add_one_waypoint"],
    ("navigation_replace_one_waypoint", "waypoint_id_to_replace"): TOOL_TARGET_HINTS["navigation_replace_one_waypoint"],
    ("navigation_replace_one_waypoint", "new_waypoint_id"): TOOL_TARGET_HINTS["navigation_replace_one_waypoint"],
    ("navigation_replace_one_waypoint", "route_id_leading_to_new_waypoint"): TOOL_TARGET_HINTS["navigation_replace_one_waypoint"],
    ("navigation_replace_one_waypoint", "route_id_leading_away_from_new_waypoint"): TOOL_TARGET_HINTS["navigation_replace_one_waypoint"],
    ("navigation_replace_final_destination", "new_destination_id"): TOOL_TARGET_HINTS["navigation_replace_final_destination"],
    ("navigation_replace_final_destination", "route_id_leading_to_new_destination"): TOOL_TARGET_HINTS["navigation_replace_final_destination"],
    ("navigation_delete_waypoint", "waypoint_id_to_delete"): TOOL_TARGET_HINTS["navigation_delete_waypoint"],
    ("navigation_delete_waypoint", "route_id_without_waypoint"): TOOL_TARGET_HINTS["navigation_delete_waypoint"],
    ("navigation_delete_destination", "destination_id_to_delete"): TOOL_TARGET_HINTS["navigation_delete_destination"],
    ("get_distance_by_soc", "initial_state_of_charge"): TOOL_TARGET_HINTS["get_distance_by_soc"],
    ("calculate_charging_time_by_soc", "charging_station_id"): TOOL_TARGET_HINTS["calculate_charging_time_by_soc"],
    ("calculate_charging_time_by_soc", "charging_station_plug_id"): TOOL_TARGET_HINTS["calculate_charging_time_by_soc"],
    ("calculate_charging_time_by_soc", "start_state_of_charge"): TOOL_TARGET_HINTS["calculate_charging_time_by_soc"],
    ("calculate_charging_soc_by_time", "charging_station_id"): TOOL_TARGET_HINTS["calculate_charging_soc_by_time"],
    ("calculate_charging_soc_by_time", "charging_station_plug_id"): TOOL_TARGET_HINTS["calculate_charging_soc_by_time"],
    ("calculate_charging_soc_by_time", "start_state_of_charge"): TOOL_TARGET_HINTS["calculate_charging_soc_by_time"],
    ("calculate_charging_soc_by_time", "charging_time"): TOOL_TARGET_HINTS["calculate_charging_soc_by_time"],
    ("get_entries_from_calendar", "month"): TOOL_TARGET_HINTS["get_entries_from_calendar"],
    ("get_entries_from_calendar", "day"): TOOL_TARGET_HINTS["get_entries_from_calendar"],
    ("get_contact_information", "contact_ids"): TOOL_TARGET_HINTS["get_contact_information"],
    ("call_phone_by_number", "phone_number"): TOOL_TARGET_HINTS["call_phone_by_number"],
    ("send_email", "content_message"): ("email", "mail", "message"),
    ("send_email", "email_addresses"): ("email", "mail"),
    ("get_location_id_by_location_name", "location"): ("navigate", "navigation", "route", "directions", "location"),
    ("search_poi_at_location", "category_poi"): (
        "restaurant", "restaurants", "fast food", "charging", "charger", "parking",
        "airport", "bakery", "supermarket", "toilet", "poi", "place",
    ),
    ("search_poi_along_the_route", "category_poi"): (
        "restaurant", "restaurants", "fast food", "charging", "charger", "parking",
        "airport", "bakery", "supermarket", "toilet", "poi", "place",
    ),
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

    @staticmethod
    def _new_scratchpad() -> dict[str, Any]:
        return {"gates": {}, "entities": {}, "facts": {}}

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
                unknown.append({"key": key, "label": label, "value": value})
        return closable, unknown

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
            "handle_missing_requested_capability": "handle_missing_requested_capability(action='do that')",
            "defrost_front_window": "defrost_front_window()",
            "open_sunroof_safe": "open_sunroof_safe(percentage)",
            "set_fog_lights_on_safe": "set_fog_lights_on_safe()",
            "set_high_beams_on_safe": "set_high_beams_on_safe()",
            "get_distance_by_soc_value": (
                "get_distance_by_soc_value(initial_state_of_charge, final_state_of_charge=0)"
            ),
            "get_navigation_state": "get_navigation_state(detailed_information=True)",
            "get_contact_details": "get_contact_details(contact_ids, required_fields=None)",
            "set_air_conditioning_on_safe": "set_air_conditioning_on_safe()",
            "close_known_windows_for_blocked_ac": "close_known_windows_for_blocked_ac(window=None)",
            "set_climate_temperature_safe": "set_climate_temperature_safe(seat_zone, temperature)",
            "get_route_options": "get_route_options(start_id, destination_id)",
            "select_route": (
                "select_route(routes, route_id=None, alias=None, name_via=None, prefer=None)"
            ),
            "get_preferred_ambient_light_color": "get_preferred_ambient_light_color()",
        }
        if tool_name in helper_signatures:
            return helper_signatures[tool_name]
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
                    "contact-ID-keyed result into contacts, by_id, and first. Pass required_fields "
                    "such as ['email'] or ['phone_number'] so unavailable response fields are "
                    "reported directly."
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
        if tool_name == "defrost_front_window":
            return {
                "name": "defrost_front_window",
                "signature": "defrost_front_window()",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for front windshield defrost. "
                    "It checks required evaluator tools, reads climate/window state, "
                    "applies CAR-bench policy 010/011 through evaluator tools, remembers which "
                    "windows it adjusted, and responds with a limitation if any conditionally "
                    "required tool is missing."
                ),
                "required_arguments": [],
                "optional_arguments": [],
                "schema": {"type": "object", "required": [], "properties": {}},
                "argument_descriptions": {},
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
        if tool_name == "handle_missing_requested_capability":
            return {
                "name": "handle_missing_requested_capability",
                "signature": "handle_missing_requested_capability(action='do that')",
                "confirmation_required": False,
                "description": (
                    "Built-in workspace helper for hallucination-split missing tool/parameter cases. "
                    "It compares the current user request against the public original tool schema and "
                    "the live task tool surface. If a requested tool or required parameter is missing, "
                    "it emits the canonical static limitation response directly and returns a report; "
                    "otherwise it returns None."
                ),
                "required_arguments": [],
                "optional_arguments": ["action"],
                "schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "action": {
                            "type": "string",
                            "default": "do that",
                            "description": "Short verb phrase for the limitation message.",
                        }
                    },
                },
                "argument_descriptions": {
                    "action": "Short verb phrase for the limitation message.",
                },
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
                    "blocks activation while fog lights are on under policy 014, and routes the "
                    "high-beam setter through its explicit confirmation requirement."
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
                    "than 20%, sets fan speed to 1 if currently 0, turns AC on, remembers which "
                    "windows it adjusted, and emits a limitation response if required evaluator "
                    "tools are missing."
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
        if tool_name == "get_route_options":
            return {
                "name": "get_route_options",
                "signature": "get_route_options(start_id, destination_id)",
                "confirmation_required": False,
                "description": (
                    "Built-in read-only route helper. Calls get_routes_from_start_to_destination "
                    "and normalizes the result to a stable dict with `routes`, `fastest`, "
                    "`shortest`, route aliases, duration totals, toll metadata, and raw result."
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
                "signature": "select_route(routes, route_id=None, alias=None, name_via=None, prefer=None)",
                "confirmation_required": False,
                "description": (
                    "Built-in pure selector over normalized or raw route lists. It returns SUCCESS "
                    "only if route_id, alias, name_via, or prefer uniquely identifies one route; "
                    "otherwise it returns AMBIGUOUS or NOT_FOUND instead of guessing."
                ),
                "required_arguments": ["routes"],
                "optional_arguments": ["route_id", "alias", "name_via", "prefer"],
                "schema": {"type": "object", "required": ["routes"], "properties": {}},
                "argument_descriptions": {
                    "routes": "Route list or get_route_options(...) result.",
                    "route_id": "Exact route_id to select.",
                    "alias": "Route alias such as fastest, shortest, first, second, or third.",
                    "name_via": "Exact via-street name from route result, such as K57, B65.",
                    "prefer": "Alias preference, usually fastest or shortest, only when explicit/policy-resolved.",
                },
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
        self._response_text = self._safe_user_message(message)

    def handle_missing_requested_capability(self, action: str = "do that") -> dict[str, Any] | None:
        """Emit a static limitation if this user turn requests a missing tool/parameter."""

        rewrite = self._infer_tool_surface_limitation_from_user_request()
        if rewrite is None:
            return None
        report = self._record_tool_surface_limitation(
            "missing_requested_capability",
            action,
            missing_tools=rewrite["missing_tools"],
            missing_arguments=rewrite["missing_arguments"],
        )
        self._abort_with_response(report["message"])

    def _respond_locked(self, message: str) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("_respond_locked(message) requires a non-empty string")
        self._response_text = self._safe_user_message(message)
        self._response_locked = True

    @staticmethod
    def _safe_user_message(message: str) -> str:
        clean = message.strip()
        lowered = clean.lower()
        if any(artifact.lower() in lowered for artifact in USER_TEXT_RUNTIME_ARTIFACTS):
            return "I hit an internal issue while preparing the response."
        return clean

    def _abort_with_response(self, message: str) -> NoReturn:
        self._respond_locked(message)
        raise ResponseReady()

    def _abort_missing_tool_response(
        self,
        response_path: str,
        action: str = "complete the requested action",
        gate_name: str = "missing_tool_response",
    ) -> NoReturn:
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
    def _normalized_text_contains(text: str, phrase: str) -> bool:
        phrase = phrase.strip().lower()
        if not phrase:
            return False
        if phrase.startswith(" ") or phrase.endswith(" ") or " " in phrase or "/" in phrase:
            return phrase.strip() in text
        return re.search(rf"\b{re.escape(phrase)}\b", text) is not None

    @staticmethod
    def _tool_name_tokens(tool_name: str) -> list[str]:
        ignored = {
            "get", "set", "open", "close", "calculate", "search", "from", "to", "and",
            "the", "new", "one", "by", "current", "information", "status", "control",
        }
        return [
            token
            for token in tool_name.lower().split("_")
            if len(token) > 2 and token not in ignored
        ]

    def _tool_matches_user_request(self, tool_name: str, request_text: str) -> bool:
        hints: list[str] = list(TOOL_TARGET_HINTS.get(tool_name, ()))
        original_schema = ORIGINAL_TOOL_SCHEMAS.get(tool_name) or {}
        for param_name in original_schema.get("required", []) or []:
            hints.extend(REMOVED_REQUIRED_PARAMETER_HINTS.get((tool_name, str(param_name)), ()))
        if any(self._normalized_text_contains(request_text, hint) for hint in hints):
            return True
        tokens = self._tool_name_tokens(tool_name)
        if not tokens:
            return False
        matches = sum(1 for token in tokens if self._normalized_text_contains(request_text, token))
        return matches >= min(2, len(tokens))

    def _infer_tool_surface_limitation_from_user_request(self) -> dict[str, Any] | None:
        if not ORIGINAL_TOOL_SCHEMAS or not self.last_user_message.strip():
            return None
        if self._is_missing_capability_acknowledgement_followup(self.last_user_message):
            return None
        request_text = " " + re.sub(r"\s+", " ", self.last_user_message.lower()).strip() + " "
        missing_tools: list[str] = []
        missing_arguments: list[dict[str, Any]] = []
        with self._lock:
            live_tools = dict(self.available_tools)
        for tool_name, original_schema in ORIGINAL_TOOL_SCHEMAS.items():
            if not self._tool_matches_user_request(tool_name, request_text):
                continue
            if tool_name not in live_tools:
                missing_tools.append(tool_name)
                continue
            original_properties = original_schema.get("properties", {}) or {}
            original_required = [str(name) for name in original_schema.get("required", []) or []]
            live_schema = live_tools[tool_name].get("function", {}).get("parameters", {}) or {}
            live_properties = live_schema.get("properties", {}) or {}
            removed_required = [
                name
                for name in original_required
                if name in original_properties and name not in live_properties
            ]
            if removed_required:
                missing_arguments.append(
                    {"tool_name": tool_name, "missing_arguments": removed_required}
                )
        if not missing_tools and not missing_arguments:
            return None
        return {
            "missing_tools": sorted(set(missing_tools)),
            "missing_arguments": missing_arguments,
        }

    def _is_missing_capability_acknowledgement_followup(self, text: str) -> bool:
        normalized = " " + re.sub(r"\s+", " ", text.strip().lower()) + " "
        if not normalized.strip():
            return False
        acknowledgement_markers = (
            "acknowledge",
            "i understand",
            "understand",
            "got it",
            "okay",
            "ok ",
        )
        limitation_markers = (
            "can't",
            "cannot",
            "not available",
            "isn't available",
            "missing",
            "not supported",
            "no tool",
            "function",
            "capability",
        )
        if not any(marker in normalized for marker in acknowledgement_markers):
            return False
        if not any(marker in normalized for marker in limitation_markers):
            return False
        last_report = self.scratchpad.get("facts", {}).get("last_helper_report")
        return isinstance(last_report, dict) and last_report.get("status") == "UNAVAILABLE"

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
            missing = [name for name in argument_names if name not in properties]
            original_schema = ORIGINAL_TOOL_SCHEMAS.get(tool_name) or {}
            original_properties = original_schema.get("properties", {}) or {}
            original_required = set(original_schema.get("required", []) or [])
            removed_required = [
                name
                for name in original_required
                if name in original_properties and name not in properties
            ]
            for name in removed_required:
                if name not in missing:
                    missing.append(name)
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

        normalized = [
            self._delegate_policy_sensitive_call(self._normalize_call_spec(item))
            for item in calls
        ]
        raw_calls = [
            call for call in normalized if call["tool_name"] not in WORKSPACE_HELPER_NAMES
        ]
        helper_calls = [
            call for call in normalized if call["tool_name"] in WORKSPACE_HELPER_NAMES
        ]

        results = self._call_raw_tools_sync(raw_calls) if raw_calls else []
        for call in helper_calls:
            helper_name = call["tool_name"]
            helper = getattr(self, helper_name)
            helper_result = helper(**call["arguments"])
            helper_status = (
                str(helper_result.get("status") or "SUCCESS")
                if isinstance(helper_result, dict)
                else "SUCCESS"
            )
            results.append(
                {
                    "status": helper_status,
                    "tool_name": helper_name,
                    "tool_call_id": "",
                    "result": helper_result,
                }
            )
        return results

    def call_tools_sync(self, calls: list[Any]) -> list[dict[str, Any]]:
        """Public policy-aware multi-call entry point."""

        return self.call_batch_sync(calls)

    def _call_raw_tools_sync(self, calls: list[Any]) -> list[dict[str, Any]]:
        """Emit raw evaluator calls after helper/policy dispatch has completed."""

        normalized = [self._normalize_call_spec(item) for item in calls]
        normalized = self._normalize_protocol_batch(normalized)
        normalized = [
            {
                "tool_name": call["tool_name"],
                "arguments": self._normalize_tool_arguments(call["tool_name"], call["arguments"]),
            }
            for call in normalized
        ]
        blocked_by_surface = self._tool_surface_blocker_result(
            "tool_surface",
            "do that",
            normalized,
        )
        if blocked_by_surface is not None:
            self.observe_environment(blocked_by_surface)
            return blocked_by_surface
        for call in normalized:
            self._validate_tool_call(call["tool_name"], call["arguments"])
        blocked_by_confirmation = self._confirmation_required_blocker_result(normalized)
        if blocked_by_confirmation is not None:
            self.observe_environment(blocked_by_confirmation)
            return blocked_by_confirmation
        policy_011_blocker = self._active_policy_011_blocker()
        if policy_011_blocker is not None:
            for call in normalized:
                if call["tool_name"] == "set_air_conditioning" and call["arguments"].get("on") is True:
                    blocked = self._block_policy_011_action("turn on AC", policy_011_blocker)
                    return [
                        {
                            **blocked,
                            "tool_name": item["tool_name"],
                            "tool_call_id": "",
                        }
                        for item in normalized
                    ]
        raw_results = self.bridge.request_tool_calls(normalized)
        parsed = [self._parse_tool_result(item) for item in raw_results]
        self.observe_environment(parsed)
        return parsed

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
            "response_on_success": "Confirmed, I completed it.",
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
            args = ", ".join(f"{key}={value!r}" for key, value in call["arguments"].items())
            return (
                "This action requires confirmation. I intend to call "
                f"{call['tool_name']}({args}). Please confirm with yes."
            )
        names = ", ".join(call["tool_name"] for call in calls)
        return f"These actions require confirmation: {names}. Please confirm with yes."

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
        message = str(pending.get("response_on_success") or "Confirmed, I completed it.")
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
            if isinstance(candidate, str) and candidate.strip():
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
        self._store_helper_report(gate_name, normalized)
        return normalized

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
        if isinstance(payload.get("contacts"), list):
            candidates = payload["contacts"]
        elif any(contact_id in payload for contact_id in ids):
            candidates = [payload.get(contact_id) for contact_id in ids]
        elif "id" in payload:
            candidates = [payload]
        else:
            candidates = list(payload.values())
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            contact_id = candidate.get("id")
            if isinstance(contact_id, str) and contact_id:
                by_id[contact_id] = candidate
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
            self._require_known_response_fields(
                gate_name,
                "get the requested contact information",
                "get_contact_information",
                payload,
                [f"{contact_id}.{field}" for field in fields],
            )
        normalized = {
            "status": "SUCCESS",
            "contacts": contacts,
            "by_id": by_id,
            "first": contacts[0],
            "raw_result": result,
        }
        if len(contacts) == 1:
            for field in ("email", "phone_number", "name"):
                if field in contacts[0]:
                    normalized[field] = contacts[0][field]
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
        weather = result_value(weather_result)
        lights = result_value(lights_result)
        if not isinstance(weather, dict) or not isinstance(lights, dict):
            return self._limitation_response(
                gate_name,
                "turn on the fog lights safely",
                reason="the weather or exterior-light result had an unexpected shape",
            )
        self._require_known_response_fields(
            gate_name,
            "turn on the fog lights safely",
            "get_exterior_lights_status",
            lights,
            ["fog_lights", "head_lights_low_beams", "head_lights_high_beams"],
        )
        condition = self._weather_condition(weather)
        if not condition:
            self._abort_missing_tool_response(
                "result.get_weather.current_slot.condition",
                "turn on the fog lights safely",
                gate_name,
            )
        fog_on = lights["fog_lights"]
        low_on = lights["head_lights_low_beams"]
        high_on = lights["head_lights_high_beams"]
        if not all(isinstance(value, bool) for value in (fog_on, low_on, high_on)):
            return self._limitation_response(
                gate_name,
                "turn on the fog lights safely",
                reason="the exterior-light states were not boolean values",
            )
        if fog_on:
            message = "The fog lights are already on."
            self._store_helper_report(
                gate_name,
                {"helper": gate_name, "status": "SUCCESS", "message": message, "actions": []},
            )
            self._respond_locked(message)
            return {"status": "SUCCESS", "actions": [], "message": message}

        action_calls: list[tuple[str, dict[str, Any]]] = []
        if not low_on:
            action_calls.append(("set_head_lights_low_beams", {"on": True}))
        if high_on:
            action_calls.append(("set_head_lights_high_beams", {"on": False}))
        action_calls.append(("set_fog_lights", {"on": True}))
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "turn on the fog lights safely",
            action_calls,
        )
        if blocker:
            return blocker

        needs_weather_confirmation = condition not in {
            "cloudy_and_thunderstorm",
            "cloudy_and_hail",
        }
        needs_tool_confirmation = high_on and self._tool_requires_confirmation(
            "set_head_lights_high_beams"
        )
        if needs_weather_confirmation or needs_tool_confirmation:
            changes: list[str] = []
            if not low_on:
                changes.append("turn on the low beams")
            if high_on:
                changes.append("turn off the high beams")
            changes.append("turn on the fog lights")
            prompt = (
                f"The current weather is {condition}. Before I {', '.join(changes)}, "
                "please explicitly confirm with yes."
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
            }
            self.remember("pending_confirmation", pending)
            report = {
                "helper": gate_name,
                "status": "WAITING_CONFIRMATION",
                "policy": "008_009_013",
                "weather_condition": condition,
                "actions": [name for name, _ in action_calls],
                "message": prompt,
            }
            self.scratchpad["gates"][gate_name] = {
                "status": "WAITING_CONFIRMATION",
                "policy": "008_009_013",
                "weather_condition": condition,
                "actions": report["actions"],
            }
            self._store_helper_report(gate_name, report)
            self._abort_with_response(prompt)

        results = self._call_raw_tools_sync(action_calls)
        for result in results:
            if result.get("status") != "SUCCESS":
                return self._failed_tool_response(gate_name, "turn on the fog lights safely", result)
        adjusted = []
        if not low_on:
            adjusted.append("low beams turned on")
        if high_on:
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
            "actions": [name for name, _ in action_calls],
            "results": results,
            "message": message,
        }
        self.scratchpad["gates"][gate_name] = {
            "status": "YES",
            "policy": "008_009_013",
            "weather_condition": condition,
            "actions": report["actions"],
        }
        self._store_helper_report(gate_name, report)
        self._respond_locked(message)
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
        self._require_known_response_fields(
            gate_name,
            "turn on the high beams safely",
            "get_exterior_lights_status",
            lights,
            ["fog_lights", "head_lights_high_beams"],
        )
        fog_on = lights["fog_lights"]
        high_on = lights["head_lights_high_beams"]
        if not isinstance(fog_on, bool) or not isinstance(high_on, bool):
            return self._limitation_response(
                gate_name,
                "turn on the high beams safely",
                reason="the exterior-light states were not boolean values",
            )
        if fog_on:
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
        if high_on:
            message = "The high beams are already on."
            self._store_helper_report(
                gate_name,
                {"helper": gate_name, "status": "SUCCESS", "message": message, "actions": []},
            )
            self._respond_locked(message)
            return {"status": "SUCCESS", "actions": [], "message": message}

        action_call = ("set_head_lights_high_beams", {"on": True})
        blocker = self._require_tool_surface_for_calls(
            gate_name,
            "turn on the high beams safely",
            [action_call],
        )
        if blocker:
            return blocker
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
            "message": message,
        }
        self._store_helper_report(gate_name, report)
        self._respond_locked(message)
        return {
            "status": "SUCCESS",
            "actions": [action_result],
            "report": report,
            "message": message,
        }

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
            weather_condition = self._weather_condition(result_value(weather_result))
            if not weather_condition:
                return self._limitation_response(
                    gate_name,
                    "open the sunroof safely",
                    reason="the current weather condition was unavailable",
                )
            safe_weather = weather_condition in {"sunny", "cloudy", "partly_cloudy"}
            if not safe_weather:
                prompt = (
                    f"Opening the sunroof in {weather_condition} weather needs your confirmation. "
                    f"Should I open the sunroof to {target_arg:g}%"
                    + (" and open the sunshade fully first?" if adjusted_sunshade else "?")
                )
                pending = {
                    "type": "sunroof_weather_confirmation",
                    "gate_name": gate_name,
                    "policy": "005_008_009",
                    "action": "open the sunroof safely",
                    "reason": f"weather condition {weather_condition}",
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
            self._respond_locked(f"Sunshade opened fully and sunroof set to {target_arg:g}%.")
        else:
            self._respond_locked(f"Sunroof set to {target_arg:g}%.")
        return {"status": "SUCCESS", "actions": results, "report": report}

    def defrost_front_window(self) -> dict[str, Any]:
        """Apply the CAR-bench front-defrost policy as one workspace helper."""

        gate_name = "defrost_front_window"

        def failed_result(result: dict[str, Any]) -> dict[str, Any] | None:
            if result.get("status") == "SUCCESS":
                return None
            tool_name = str(result.get("tool_name") or "")
            label = _tool_label(tool_name) if tool_name else "required tool"
            message = (
                "I can't safely turn on front defrost because I couldn't get a usable "
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
        blocker = self._require_tool_surface_for_calls(gate_name, "turn on front defrost", read_calls)
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
            raw_windows = result_value(windows_result)
            if isinstance(raw_windows, dict):
                windows = raw_windows

        action_calls: list[tuple[str, dict[str, Any]]] = [
            ("set_window_defrost", {"on": True, "defrost_window": "FRONT"})
        ]
        adjusted_windows: list[dict[str, Any]] = []
        unknown_windows: list[dict[str, Any]] = []

        if climate.get("fan_speed", 0) < 2:
            action_calls.append(("set_fan_speed", {"level": 2}))

        if "WINDSHIELD" not in str(climate.get("fan_airflow_direction", "")):
            action_calls.append(("set_fan_airflow_direction", {"direction": "WINDSHIELD"}))

        if not climate.get("air_conditioning", False):
            if not has_window_reader:
                return self._limitation_response(
                    gate_name,
                    "safely turn on front defrost under policy 010/011",
                    missing_tools=["get_vehicle_window_positions"],
                )
            else:
                windows_to_close, unknown_windows = self._windows_over_position(windows, 20)
                if unknown_windows:
                    return self._window_policy_limitation(
                        gate_name,
                        "safely turn on front defrost",
                        "010/011",
                        unknown_windows,
                        windows_to_close,
                    )
                if windows_to_close:
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
            "safely turn on front defrost under policy 010/011",
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
        self._respond_locked("Front defrost is on, with the required fan, AC, and window safety settings handled.")
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
        windows = result_value(windows_result)
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
            self._respond_locked("AC is already on.")
            return {"status": "SUCCESS", "actions": [], "already_on": True}

        action_calls: list[tuple[str, dict[str, Any]]] = []
        adjusted_windows: list[dict[str, Any]] = []
        windows_to_close, unknown_windows = self._windows_over_position(windows, 20)
        if unknown_windows:
            return self._window_policy_limitation(
                gate_name,
                "turn on the air conditioning",
                "011",
                unknown_windows,
                windows_to_close,
            )
        if windows_to_close:
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
        self._respond_locked("AC is on, and I handled the required fan and window safety settings.")
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
        self._respond_locked(message)
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
            self._respond_locked(f"Temperature set to {target:g} degrees Celsius for all zones.")
        elif warning:
            other_label = "passenger" if normalized_zone == "DRIVER" else "driver"
            self._respond_locked(
                f"{normalized_zone.lower()} temperature set to {target:g} degrees Celsius. "
                f"Heads up, that is more than 3 degrees different from the {other_label} side."
            )
        else:
            self._respond_locked(f"{normalized_zone.lower()} temperature set to {target:g} degrees Celsius.")
        return {
            "status": "SUCCESS",
            "action": action,
            "warning_difference_over_3": warning,
            "other_temperature": other_temp,
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
        raw = result_value(result)
        routes = self._extract_routes(raw)
        normalized = [self._normalize_route(route) for route in routes]
        fastest = self.select_route(normalized, alias="fastest")
        shortest = self.select_route(normalized, alias="shortest")
        return {
            "status": "SUCCESS",
            "routes": normalized,
            "fastest": fastest.get("route") if fastest.get("status") == "SUCCESS" else None,
            "shortest": shortest.get("route") if shortest.get("status") == "SUCCESS" else None,
            "raw_result": raw,
        }

    def select_route(
        self,
        routes: Any,
        route_id: str | None = None,
        alias: str | None = None,
        name_via: str | None = None,
        prefer: str | None = None,
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
            return {
                "status": "AMBIGUOUS" if len(route_list) > 1 else "SUCCESS",
                "route": selected_route,
                "result": selected_route,
                "route_id": selected_route.get("route_id") if selected_route else None,
                "matches": route_list,
                "reason": "no selector provided" if len(route_list) > 1 else "only one route",
            }

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
            return {
                "status": "SUCCESS",
                "route": matches[0],
                "result": matches[0],
                "route_id": matches[0].get("route_id"),
            }
        if not matches:
            return {"status": "NOT_FOUND", "matches": [], "reason": "selector matched no route"}
        return {"status": "AMBIGUOUS", "matches": matches, "reason": "selector matched multiple routes"}

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
        return normalized

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
        if tool_name == "set_fog_lights" and arguments.get("on") is True:
            return {"tool_name": "set_fog_lights_on_safe", "arguments": {}}
        if tool_name == "set_head_lights_high_beams" and arguments.get("on") is True:
            return {"tool_name": "set_high_beams_on_safe", "arguments": {}}
        return call

    def _normalize_tool_arguments(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            tool = self.available_tools.get(tool_name) or {}
            schema = tool.get("function", {}).get("parameters", {}) or {}
        properties = schema.get("properties", {}) or {}
        normalized: dict[str, Any] = {}
        for name, value in arguments.items():
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

        def walk(value: Any) -> bool:
            if isinstance(value, str):
                lowered = value.lower()
                return any(pattern.lower() in lowered for pattern in bad_patterns)
            if isinstance(value, dict):
                return any(walk(v) for v in value.values())
            if isinstance(value, (list, tuple)):
                return any(walk(v) for v in value)
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
            if "charging_time" in key or key.startswith("time_") or key.endswith("_time"):
                minutes = CoroutineWorkspace._parse_first_number(value)
                if minutes is not None:
                    parsed.setdefault("minutes", minutes)
            if "soc" in key or "state_of_charge" in key:
                soc = CoroutineWorkspace._parse_first_number(value)
                if soc is not None:
                    parsed.setdefault("state_of_charge", soc)

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


def result_value(result: dict[str, Any]) -> Any:
    if isinstance(result, list) and len(result) == 1:
        return result_value(result[0])
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
    raise ValueError(f"Cannot extract ID from {value!r}")


def pois_value(value: Any) -> list[dict[str, Any]]:
    data = _require_known_extracted_value(_unwrap_result_like(value))
    if isinstance(data, list):
        pois = data
    elif isinstance(data, dict):
        pois = data.get("pois") if "pois" in data else data.get("pois_found")
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
    parsed = CoroutineWorkspace._parse_first_number(value)
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
            "batch": ws.call_batch_sync,
            "result_by_tool": result_by_tool,
            "result_value": result_value,
            "id_value": id_value,
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
            "handle_missing_requested_capability": ws.handle_missing_requested_capability,
            "defrost_front_window": ws.defrost_front_window,
            "open_sunroof_safe": ws.open_sunroof_safe,
            "set_fog_lights_on_safe": ws.set_fog_lights_on_safe,
            "set_high_beams_on_safe": ws.set_high_beams_on_safe,
            "get_distance_by_soc_value": ws.get_distance_by_soc_value,
            "get_navigation_state": ws.get_navigation_state,
            "get_contact_details": ws.get_contact_details,
            "set_air_conditioning_on_safe": ws.set_air_conditioning_on_safe,
            "close_known_windows_for_blocked_ac": ws.close_known_windows_for_blocked_ac,
            "set_climate_temperature_safe": ws.set_climate_temperature_safe,
            "get_route_options": ws.get_route_options,
            "select_route": ws.select_route,
            "get_preferred_ambient_light_color": ws.get_preferred_ambient_light_color,
        }
        for tool_name in ALL_TOOL_NAMES:
            globals_dict[tool_name] = self._make_tool_wrapper(tool_name)
        return globals_dict

    def _print(self, *args: Any, **kwargs: Any) -> None:
        if "file" not in kwargs and self._active_stdout is not None:
            kwargs["file"] = self._active_stdout
        builtins.print(*args, **kwargs)

    def _make_tool_wrapper(self, tool_name: str) -> Callable[..., dict[str, Any]]:
        def wrapper(**kwargs: Any) -> dict[str, Any]:
            if tool_name == "set_fog_lights" and kwargs.get("on") is True:
                return self.workspace.set_fog_lights_on_safe()
            if tool_name == "set_head_lights_high_beams" and kwargs.get("on") is True:
                return self.workspace.set_high_beams_on_safe()
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
        parts.append(f"SCRATCHPAD\n{json.dumps(scratchpad, indent=2, ensure_ascii=True)}")
    return "\n\n".join(parts)
