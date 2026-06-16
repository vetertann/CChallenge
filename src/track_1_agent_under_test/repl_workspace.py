"""Persistent Python workspace exposed to the model."""

from __future__ import annotations

import builtins
import contextlib
import datetime as datetime_module
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


ALL_TOOL_NAMES = [
    "get_user_preferences",
    "calculate_math",
    "calculate_datetime",
    "think",
    "planning_tool",
    "open_close_sunroof",
    "open_close_sunshade",
    "open_close_trunk_door",
    "open_close_window",
    "set_air_circulation",
    "set_air_conditioning",
    "set_ambient_lights",
    "set_climate_temperature",
    "set_fan_airflow_direction",
    "set_fan_speed",
    "set_fog_lights",
    "set_head_lights_high_beams",
    "set_head_lights_low_beams",
    "set_reading_light",
    "set_seat_heating",
    "set_steering_wheel_heating",
    "set_window_defrost",
    "get_ambient_light_status_and_color",
    "get_car_color",
    "get_climate_settings",
    "get_exterior_lights_status",
    "get_fuel_information",
    "get_reading_lights_status",
    "get_seat_heating_level",
    "get_seats_occupancy",
    "get_steering_wheel_heating_level",
    "get_sunroof_and_sunshade_position",
    "get_temperature_inside_car",
    "get_trunk_door_position",
    "get_vehicle_window_positions",
    "get_weather",
    "search_poi_at_location",
    "search_poi_along_the_route",
    "get_routes_from_start_to_destination",
    "get_location_id_by_location_name",
    "get_current_navigation_state",
    "convert_route_distance_and_time",
    "set_new_navigation",
    "navigation_add_one_waypoint",
    "navigation_replace_one_waypoint",
    "navigation_replace_final_destination",
    "navigation_delete_waypoint",
    "navigation_delete_destination",
    "delete_current_navigation",
    "get_charging_specs_and_status",
    "get_distance_by_soc",
    "calculate_charging_time_by_soc",
    "calculate_charging_soc_by_time",
    "get_contact_id_by_contact_name",
    "get_entries_from_calendar",
    "get_contact_information",
    "call_phone_by_number",
    "send_email",
]

SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs all any bool dict enumerate float getattr hasattr int isinstance "
        "len list max min print range repr reversed round set sorted str sum tuple zip"
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
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    response_text: str | None = None


class CarWorkspace:
    """State and tool emitters available to model-written Python."""

    def __init__(self) -> None:
        self.scratchpad: dict[str, Any] = self._new_scratchpad()
        self.policy: str = ""
        self.available_tools: dict[str, dict[str, Any]] = {}
        self.last_user_message: str = ""
        self.last_source: str = "user"
        self.tool_results: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self._pending_tool_calls: list[dict[str, Any]] = []
        self._response_text: str | None = None

    @staticmethod
    def _new_scratchpad() -> dict[str, Any]:
        return {
            "gates": {},
            "entities": {},
            "facts": {},
        }

    def _ensure_scratchpad_shape(self) -> None:
        defaults = self._new_scratchpad()
        for key, default in defaults.items():
            if key not in self.scratchpad or not isinstance(self.scratchpad[key], type(default)):
                self.scratchpad[key] = default

    @staticmethod
    def _preview(value: Any, limit: int = 240) -> str:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=True)
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 12] + " ...[truncated]"

    def remember(self, key: str, value: Any, section: str = "facts") -> Any:
        self._ensure_scratchpad_shape()
        if section not in self.scratchpad or not isinstance(self.scratchpad[section], dict):
            raise ValueError(f"scratchpad section {section!r} is not a mapping")
        self.scratchpad[section][key] = value
        return value

    def remember_entity(self, key: str, value: Any) -> Any:
        return self.remember(key, value, section="entities")

    def tool_available(self, tool_name: str) -> bool:
        return tool_name in self.available_tools

    def tool_schema(self, tool_name: str) -> dict[str, Any]:
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
        required = self.tool_required_arguments(tool_name)
        optional = self.tool_optional_arguments(tool_name)
        args = required + [f"{name}=..." for name in optional]
        joined = ", ".join(args)
        return f"{tool_name}({joined})"

    def _tool_mode_tag(self, tool_name: str) -> str:
        read_prefixes = (
            "get_",
            "search_",
            "calculate_",
            "convert_",
            "think",
            "planning_tool",
        )
        return "readonly" if tool_name.startswith(read_prefixes) or tool_name in {"think", "planning_tool"} else "mutating"

    def describe_tool(self, tool_name: str) -> dict[str, Any]:
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
            "mode": self._tool_mode_tag(tool_name),
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
        tools: list[dict[str, Any]] = []
        for tool_name in self.available_tool_names():
            info = self.describe_tool(tool_name)
            tools.append(
                {
                    "name": info["name"],
                    "confirmation_required": info["confirmation_required"],
                    "description": info["description"],
                }
            )
        return tools

    def tool_supports_arguments(self, tool_name: str, argument_names: list[str] | tuple[str, ...] | set[str] | None = None) -> bool:
        if tool_name not in self.available_tools:
            return False
        if not argument_names:
            return True
        schema = self.tool_schema(tool_name)
        properties = schema.get("properties", {}) or {}
        if not properties:
            return False
        required_names = [str(name) for name in argument_names]
        return all(name in properties for name in required_names)

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
                tool_name = item
            elif isinstance(item, dict):
                tool_name = str(item.get("tool_name") or item.get("tool") or "")
                raw_args = item.get("arguments") or item.get("argument_names") or []
                if isinstance(raw_args, str):
                    argument_names = [raw_args]
                else:
                    argument_names = [str(name) for name in raw_args]
            elif isinstance(item, (tuple, list)) and item:
                tool_name = str(item[0])
                if len(item) > 1:
                    raw_args = item[1]
                    if isinstance(raw_args, str):
                        argument_names = [raw_args]
                    else:
                        argument_names = [str(name) for name in raw_args or []]
            if not tool_name:
                raise ValueError("capability_claim_gate requirements must include a tool name")
            normalized.append({"tool_name": tool_name, "arguments": argument_names})
            if not self.tool_available(tool_name):
                missing_tools.append(tool_name)
                continue
            if argument_names and not self.tool_supports_arguments(tool_name, argument_names):
                schema = self.available_tools[tool_name].get("function", {}).get("parameters", {}) or {}
                properties = schema.get("properties", {}) or {}
                missing = [name for name in argument_names if name not in properties]
                if missing:
                    missing_arguments.append(
                        {
                            "tool_name": tool_name,
                            "missing_arguments": missing,
                        }
                    )

        ok = not missing_tools and not missing_arguments
        self.scratchpad["gates"][gate_name] = {
            "status": "YES" if ok else "NO",
            "requirements": normalized,
            "missing_tools": missing_tools,
            "missing_arguments": missing_arguments,
        }
        return ok

    def note_assistant_step(
        self,
        *,
        thought: str,
        response_text: str | None,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        _ = (thought, response_text, tool_calls)

    def reset_actions(self) -> None:
        self._pending_tool_calls = []
        self._response_text = None

    def update_tools(self, tools: list[dict[str, Any]]) -> None:
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
        self._response_text = message.strip()

    def emit_tool_call(self, tool_name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            arguments = dict(arguments or {}, **kwargs)
        arguments = dict(arguments or {})
        self._validate_tool_call(tool_name, arguments)
        call = {"tool_name": tool_name, "arguments": arguments}
        self._pending_tool_calls.append(call)
        return {"queued": tool_name, "arguments": arguments}

    def available_tool_names(self) -> list[str]:
        return sorted(self.available_tools)

    def _validate_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
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


class PythonExecutor:
    def __init__(self, workspace: CarWorkspace) -> None:
        self.workspace = workspace
        self._globals = self._build_globals()

    def _build_globals(self) -> dict[str, Any]:
        ws = self.workspace
        globals_dict: dict[str, Any] = {
            "__builtins__": dict(SAFE_BUILTINS, __import__=self._safe_import),
            "json": json,
            "math": math,
            "re": re,
            "datetime": datetime,
            "ws": ws,
            "scratchpad": ws.scratchpad,
            "respond": ws.respond,
            "emit_tool_call": ws.emit_tool_call,
            "call": ws.emit_tool_call,
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
        }
        for tool_name in ALL_TOOL_NAMES:
            globals_dict[tool_name] = self._make_tool_wrapper(tool_name)
        return globals_dict

    def _make_tool_wrapper(self, tool_name: str) -> Callable[..., dict[str, Any]]:
        def wrapper(**kwargs: Any) -> dict[str, Any]:
            return self.workspace.emit_tool_call(tool_name, kwargs)

        wrapper.__name__ = tool_name
        wrapper.__doc__ = f"Queue CAR-bench tool call {tool_name}(**kwargs)."
        return wrapper

    def _safe_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in ALLOWED_IMPORTS:
            return ALLOWED_IMPORTS[name]
        raise ImportError(
            f"Import {name!r} is not available. Use ws, scratchpad, respond(...), "
            "and the preloaded CAR-bench tool functions."
        )

    def run(self, code: str) -> ExecutionResult:
        self.workspace.reset_actions()
        stdout = io.StringIO()
        error = None
        try:
            with contextlib.redirect_stdout(stdout):
                exec(code, self._globals, self._globals)
        except Exception as exc:
            error = {"type": exc.__class__.__name__, "message": str(exc)}
        return ExecutionResult(
            stdout=stdout.getvalue(),
            error=error,
            tool_calls=list(self.workspace._pending_tool_calls),
            response_text=self.workspace._response_text,
        )


def format_observation(result: ExecutionResult, scratchpad: dict[str, Any]) -> str:
    parts = [
        f"STDOUT\n{result.stdout.strip()}" if result.stdout.strip() else "STDOUT\n(no output)"
    ]
    if result.error:
        parts.append(f"ERROR\n{result.error['type']}: {result.error['message']}")
    if result.tool_calls:
        parts.append(f"QUEUED_TOOL_CALLS\n{json.dumps(result.tool_calls, indent=2, ensure_ascii=True)}")
    if result.response_text:
        parts.append(f"RESPONSE_TEXT\n{result.response_text}")
    if scratchpad:
        parts.append(f"SCRATCHPAD\n{json.dumps(scratchpad, indent=2, ensure_ascii=True)}")
    return "\n\n".join(parts)
