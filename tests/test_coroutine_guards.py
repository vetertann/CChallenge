"""Focused tests for the four reliability guards added to the coroutine agent:

1. Mutation-outcome guard (no false success after a failed side effect).
2. Active-navigation guard (no set_new_navigation against an active route).
3. Policy date/time + location exposure.
4. Auto-persistence of grounded entities.
"""

import json
import unittest

from track_1_agent_coroutine_under_test.coroutine_repl import (
    BlockingPythonExecutor,
    CoroutineWorkspace,
    ResponseReady,
)


def tool_schema(name: str, properties: dict, *, required=None, description="") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


class ScriptedBridge:
    """Returns a configured (status, result) per tool name."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.requests: list[list[dict]] = []
        self._call_counts: dict[str, int] = {}

    def request_tool_calls(self, calls: list[dict]) -> list[dict]:
        self.requests.append(calls)
        out = []
        for index, call in enumerate(calls):
            name = call["tool_name"]
            configured = self.responses.get(name, ("SUCCESS", {}))
            if isinstance(configured, list):
                count = self._call_counts.get(name, 0)
                self._call_counts[name] = count + 1
                status, result = configured[min(count, len(configured) - 1)]
            else:
                status, result = configured
            out.append(
                {
                    "tool_name": name,
                    "tool_call_id": f"call-{index}",
                    "content": json.dumps({"status": status, "result": result}),
                }
            )
        return out


POLICY_TEXT = (
    'CURRENT_LOCATION = {"id": "loc_home_1", "name": "Munich"}\n'
    'DATETIME = {"year": 2025, "month": 6, "day": 6, "hour": 14, "minute": 30}\n'
)


class GuardTests(unittest.TestCase):
    def make(self, responses, tools, policy=POLICY_TEXT):
        bridge = ScriptedBridge(responses)
        ws = CoroutineWorkspace(bridge)
        ws.policy = policy
        ws.available_tools = tools
        executor = BlockingPythonExecutor(ws)
        return ws, executor

    # --- 1. mutation-outcome guard ---------------------------------------

    def test_failed_mutation_blocks_success_text(self):
        ws, ex = self.make(
            {"set_seat_heating": ("FAILURE", {})},
            {"set_seat_heating": tool_schema(
                "set_seat_heating",
                {"level": {"type": "integer"}, "seat_zone": {"type": "string"}})},
        )
        result = ex.run(
            "set_seat_heating(level=2, seat_zone='DRIVER')\nrespond('Seat heating is on.')"
        )
        self.assertIsNotNone(result.response_text)
        self.assertNotIn("Seat heating is on", result.response_text)
        self.assertIn("couldn't complete", result.response_text.lower())

    def test_successful_retry_clears_block(self):
        # First call fails, second (same block) succeeds -> success text allowed.
        responses = {"set_fan_speed": ("SUCCESS", {})}
        ws, ex = self.make(
            responses,
            {"set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}})},
        )
        # Simulate a prior failure recorded, then a success clears it.
        ws._record_mutation_outcomes(
            [{"tool_name": "set_fan_speed", "status": "FAILURE"}],
            [{"tool_name": "set_fan_speed", "arguments": {"level": 1}}],
        )
        result = ex.run("set_fan_speed(level=3)\nrespond('Fan set to 3.')")
        self.assertEqual(result.response_text, "Fan set to 3.")

    def test_local_more_facts_block_is_not_mutation_failure(self):
        ws, _ = self.make({}, {})

        ws._record_mutation_outcomes(
            [
                {
                    "tool_name": "send_email",
                    "status": "NEEDS_MORE_FACTS",
                    "result": {"helper": "long_route_email_charging_fact_guard"},
                }
            ],
            [
                {
                    "tool_name": "send_email",
                    "arguments": {"email_addresses": ["grace@example.com"]},
                }
            ],
        )

        self.assertIsNone(ws._unacknowledged_mutation_failure_message())

    def test_failed_mutation_survives_next_python_block(self):
        ws, ex = self.make(
            {"set_fan_speed": ("FAILURE", {})},
            {"set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}})},
        )
        ex.run("set_fan_speed(level=3)")
        result = ex.run("respond('Fan set to 3.')")
        self.assertIn("couldn't complete", result.response_text.lower())

    def test_new_user_turn_clears_prior_mutation_failure(self):
        ws, ex = self.make(
            {"set_fan_speed": ("FAILURE", {})},
            {"set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}})},
        )
        ex.run("set_fan_speed(level=3)")
        ws.observe_user("What time is it?")
        result = ex.run("respond('It is 2:30 PM.')")
        self.assertEqual(result.response_text, "It is 2:30 PM.")

    def test_executor_repairs_trailing_unmatched_brace(self):
        ws, ex = self.make({}, {})

        result = ex.run(
            "parts = []\n"
            "parts.append('charging station found')\n"
            "respond(', '.join(parts))\n"
            "}"
        )

        self.assertEqual(result.response_text, "charging station found")

    def test_navigation_set_claim_without_mutation_reports_missing_control(self):
        ws, ex = self.make({}, {})
        result = ex.run("respond('Navigation set: first to the charger, then Hamburg.')")

        self.assertIn("set_new_navigation", result.response_text)
        self.assertNotIn("Navigation set", result.response_text)

    def test_navigation_is_now_set_claim_without_mutation_reports_missing_control(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "respond('Charging will take 28 minutes. Navigation is now set: first to the charger, then Hamburg.')"
        )

        self.assertIn("set_new_navigation", result.response_text)
        self.assertNotIn("Navigation is now set", result.response_text)

    def test_navigation_set_claim_with_descriptive_phrase_requires_mutation(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "respond(\"I've set a two-leg navigation: first to the charger, then Hamburg.\")"
        )

        self.assertIn("set_new_navigation", result.response_text)
        self.assertNotIn("two-leg navigation", result.response_text)

    def test_navigation_leg_setup_claim_without_mutation_reports_missing_control(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "respond(\"I've set up the first leg to the Ionity charging station. "
            "For the next leg, should I use the fastest route?\")"
        )

        self.assertIn("set_new_navigation", result.response_text)
        self.assertNotIn("first leg", result.response_text)

    def test_navigation_set_claim_allowed_after_successful_navigation_mutation(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}

        result = ex.run(
            "set_new_navigation(route_ids=['route_1'])\n"
            "respond('Navigation set.')"
        )

        self.assertEqual(result.response_text, "Navigation set.")

    def test_navigation_edit_success_allows_later_set_claim(self):
        ws, ex = self.make(
            {
                "navigation_delete_waypoint": (
                    "SUCCESS",
                    {
                        "waypoint_deleted": True,
                        "new_waypoints": ["loc_wie", "loc_par"],
                        "new_routes": ["route_fastest"],
                    },
                )
            },
            {
                "navigation_delete_waypoint": tool_schema(
                    "navigation_delete_waypoint",
                    {
                        "waypoint_id_to_delete": {"type": "string"},
                        "route_id_without_waypoint": {"type": "string"},
                    },
                    required=["waypoint_id_to_delete", "route_id_without_waypoint"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )

        ex.run(
            "navigation_delete_waypoint("
            "waypoint_id_to_delete='loc_nur', route_id_without_waypoint='route_fastest')\n"
            "respond('Nuremberg removed.')"
        )
        ws.observe_user("Is it set now?")
        result = ex.run("respond('Navigation is set to Paris now.')")

        self.assertEqual(result.response_text, "Navigation is set to Paris now.")

    def test_email_sent_claim_without_send_email_is_rewritten(self):
        ws, ex = self.make(
            {},
            {
                "send_email": tool_schema(
                    "send_email",
                    {
                        "email_addresses": {"type": "array", "items": {"type": "string"}},
                        "content_message": {"type": "string"},
                    },
                    required=["email_addresses", "content_message"],
                )
            },
        )

        result = ex.run("respond('Email sent.')")

        self.assertIn("haven't sent the email yet", result.response_text)
        self.assertNotIn("Email sent.", result.response_text)

    def test_email_sent_claim_allowed_after_successful_send_email(self):
        ws, ex = self.make(
            {"send_email": ("SUCCESS", {})},
            {
                "send_email": tool_schema(
                    "send_email",
                    {
                        "email_addresses": {"type": "array", "items": {"type": "string"}},
                        "content_message": {"type": "string"},
                    },
                    required=["email_addresses", "content_message"],
                )
            },
        )

        result = ex.run(
            "send_email(email_addresses=['alex@example.com'], content_message='Hi')\n"
            "respond('Email sent.')"
        )

        self.assertEqual(result.response_text, "Email sent.")
        self.assertEqual(
            ws.scratchpad["entities"]["last_successful_email_send"]["arguments"]["email_addresses"],
            ["alex@example.com"],
        )

    def test_email_sent_claim_waits_for_pending_confirmation(self):
        ws, ex = self.make(
            {},
            {
                "send_email": tool_schema(
                    "send_email",
                    {
                        "email_addresses": {"type": "array", "items": {"type": "string"}},
                        "content_message": {"type": "string"},
                    },
                    required=["email_addresses", "content_message"],
                    description="REQUIRES_CONFIRMATION Send an email.",
                )
            },
        )
        ws.remember(
            "pending_confirmation",
            {
                "on_confirm_calls": [
                    {
                        "tool_name": "send_email",
                        "arguments": {
                            "email_addresses": ["alex@example.com"],
                            "content_message": "Hi",
                        },
                    }
                ]
            },
        )

        result = ex.run("respond('Email sent.')")

        self.assertIn("still needs your confirmation", result.response_text)

    def test_clean_mutation_allows_success_text(self):
        ws, ex = self.make(
            {"set_fan_speed": ("SUCCESS", {})},
            {"set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}})},
        )
        result = ex.run("set_fan_speed(level=3)\nrespond('Fan set to 3.')")
        self.assertEqual(result.response_text, "Fan set to 3.")

    def test_high_beam_helper_attempts_setter_when_fog_state_unknown(self):
        ws, _ = self.make(
            {
                "get_exterior_lights_status": (
                    "SUCCESS",
                    {
                        "fog_lights": "unknown",
                        "head_lights_high_beams": False,
                        "head_lights_low_beams": True,
                    },
                ),
                "set_head_lights_high_beams": (
                    "SUCCESS",
                    {"head_lights_high_beams": True},
                ),
            },
            {
                "get_exterior_lights_status": tool_schema("get_exterior_lights_status", {}),
                "set_head_lights_high_beams": tool_schema(
                    "set_head_lights_high_beams",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        result = ws.set_high_beams_on_safe()

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["message"], "High beams turned on.")
        self.assertEqual(
            [[call["tool_name"] for call in request] for request in ws.bridge.requests],
            [["get_exterior_lights_status"], ["set_head_lights_high_beams"]],
        )
        self.assertEqual(
            ws.bridge.requests[-1][0]["arguments"],
            {"on": True},
        )
        self.assertIn(
            "result.get_exterior_lights_status.fog_lights",
            ws.scratchpad["facts"]["last_helper_report"]["unknown_response_fields"],
        )

    def test_high_beam_helper_confirmation_mentions_unknown_fog_state(self):
        ws, ex = self.make(
            {
                "get_exterior_lights_status": (
                    "SUCCESS",
                    {
                        "fog_lights": "unknown",
                        "head_lights_high_beams": False,
                        "head_lights_low_beams": True,
                    },
                ),
                "set_head_lights_high_beams": (
                    "SUCCESS",
                    {"head_lights_high_beams": True},
                ),
            },
            {
                "get_exterior_lights_status": tool_schema("get_exterior_lights_status", {}),
                "set_head_lights_high_beams": tool_schema(
                    "set_head_lights_high_beams",
                    {"on": {"type": "boolean"}},
                    required=["on"],
                    description="REQUIRES_CONFIRMATION, turns high beams on or off.",
                ),
            },
        )

        with self.assertRaises(ResponseReady):
            ws.set_high_beams_on_safe()

        self.assertIn("fog-light status is unavailable", ws._response_text or "")
        self.assertIn("high beams are currently off", ws._response_text or "")
        self.assertIn("on=True", ws._response_text or "")
        self.assertEqual(
            [[call["tool_name"] for call in request] for request in ws.bridge.requests],
            [["get_exterior_lights_status"]],
        )
        pending = ws.scratchpad["facts"]["pending_confirmation"]
        self.assertEqual(pending["response_on_success"], "High beams turned on.")
        self.assertIn(
            "result.get_exterior_lights_status.fog_lights",
            pending["unknown_response_fields"],
        )

        ws.observe_user("Yes, proceed.")
        result = ex.run("handle_pending_confirmation()")

        self.assertEqual(result.response_text, "High beams turned on.")
        self.assertEqual(
            self._emitted(ws, "set_head_lights_high_beams"),
            {"on": True},
        )

    def test_high_beam_helper_blocks_when_fog_lights_known_on(self):
        ws, _ = self.make(
            {
                "get_exterior_lights_status": (
                    "SUCCESS",
                    {
                        "fog_lights": True,
                        "head_lights_high_beams": False,
                    },
                ),
            },
            {
                "get_exterior_lights_status": tool_schema("get_exterior_lights_status", {}),
                "set_head_lights_high_beams": tool_schema(
                    "set_head_lights_high_beams",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        with self.assertRaises(ResponseReady):
            ws.set_high_beams_on_safe()
        self.assertEqual(
            [[call["tool_name"] for call in request] for request in ws.bridge.requests],
            [["get_exterior_lights_status"]],
        )
        self.assertIn("policy 014", ws._response_text or "")

    def test_fog_lights_helper_confirms_when_weather_and_light_state_unknown(self):
        ws, ex = self.make(
            {
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "unknown"}}),
                "get_exterior_lights_status": (
                    "SUCCESS",
                    {
                        "fog_lights": False,
                        "head_lights_low_beams": "unknown",
                        "head_lights_high_beams": "unknown",
                    },
                ),
                "set_head_lights_low_beams": ("SUCCESS", {}),
                "set_head_lights_high_beams": ("SUCCESS", {}),
                "set_fog_lights": ("SUCCESS", {}),
            },
            {
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                ),
                "get_exterior_lights_status": tool_schema("get_exterior_lights_status", {}),
                "set_head_lights_low_beams": tool_schema(
                    "set_head_lights_low_beams",
                    {"on": {"type": "boolean"}},
                ),
                "set_head_lights_high_beams": tool_schema(
                    "set_head_lights_high_beams",
                    {"on": {"type": "boolean"}},
                    required=["on"],
                    description="REQUIRES_CONFIRMATION, turns high beams on or off.",
                ),
                "set_fog_lights": tool_schema("set_fog_lights", {"on": {"type": "boolean"}}),
            },
        )

        with self.assertRaises(ResponseReady):
            ws.set_fog_lights_on_safe()

        self.assertIn("weather condition is unavailable", ws._response_text or "")
        self.assertIn("low-beam status is unavailable", ws._response_text or "")
        self.assertIn("high-beam status is unavailable", ws._response_text or "")
        pending = ws.scratchpad["facts"]["pending_confirmation"]
        self.assertEqual(
            [call[0] for call in pending["on_confirm_calls"]],
            ["set_head_lights_low_beams", "set_head_lights_high_beams", "set_fog_lights"],
        )

        ws.observe_user("yes")
        result = ex.run("handle_pending_confirmation()")

        self.assertIn("turned on the fog lights", result.response_text)
        self.assertEqual(self._emitted(ws, "set_head_lights_low_beams"), {"on": True})
        self.assertEqual(self._emitted(ws, "set_head_lights_high_beams"), {"on": False})
        self.assertEqual(self._emitted(ws, "set_fog_lights"), {"on": True})

    def test_exterior_lights_visibility_intent_uses_fog_policy_path(self):
        ws, _ = self.make(
            {
                "get_weather": (
                    "SUCCESS",
                    {"current_slot": {"condition": "cloudy_and_thunderstorm"}},
                ),
                "get_exterior_lights_status": (
                    "SUCCESS",
                    {
                        "fog_lights": False,
                        "head_lights_low_beams": True,
                        "head_lights_high_beams": False,
                    },
                ),
                "set_fog_lights": ("SUCCESS", {}),
            },
            {
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                ),
                "get_exterior_lights_status": tool_schema("get_exterior_lights_status", {}),
                "set_fog_lights": tool_schema("set_fog_lights", {"on": {"type": "boolean"}}),
            },
        )

        result = ws.set_exterior_lights_safe(intent="improve_visibility")

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(
            [[call["tool_name"] for call in request] for request in ws.bridge.requests],
            [["get_weather", "get_exterior_lights_status"], ["set_fog_lights"]],
        )
        self.assertEqual(self._emitted(ws, "set_fog_lights"), {"on": True})

    def test_exterior_lights_turn_on_headlights_prompts_high_beams_when_low_on(self):
        ws, ex = self.make(
            {
                "get_exterior_lights_status": (
                    "SUCCESS",
                    {
                        "fog_lights": False,
                        "head_lights_low_beams": True,
                        "head_lights_high_beams": False,
                    },
                ),
                "set_head_lights_high_beams": (
                    "SUCCESS",
                    {"head_lights_high_beams": True},
                ),
            },
            {
                "get_exterior_lights_status": tool_schema("get_exterior_lights_status", {}),
                "set_head_lights_high_beams": tool_schema(
                    "set_head_lights_high_beams",
                    {"on": {"type": "boolean"}},
                    required=["on"],
                    description="REQUIRES_CONFIRMATION, turns high beams on or off.",
                ),
            },
        )

        with self.assertRaises(ResponseReady):
            ws.set_exterior_lights_safe(intent="turn_on_headlights")

        self.assertIn("low-beam headlights are already on", ws._response_text or "")
        self.assertIn("on=True", ws._response_text or "")
        self.assertEqual(
            [[call["tool_name"] for call in request] for request in ws.bridge.requests],
            [["get_exterior_lights_status"]],
        )

        ws.observe_user("yes")
        result = ex.run("handle_pending_confirmation()")

        self.assertEqual(result.response_text, "High beams turned on.")
        self.assertEqual(self._emitted(ws, "set_head_lights_high_beams"), {"on": True})

    def test_exterior_lights_turn_off_only_lights_known_on(self):
        ws, _ = self.make(
            {
                "get_exterior_lights_status": (
                    "SUCCESS",
                    {
                        "fog_lights": False,
                        "head_lights_low_beams": True,
                        "head_lights_high_beams": False,
                    },
                ),
                "set_head_lights_low_beams": ("SUCCESS", {}),
            },
            {
                "get_exterior_lights_status": tool_schema("get_exterior_lights_status", {}),
                "set_fog_lights": tool_schema("set_fog_lights", {"on": {"type": "boolean"}}),
                "set_head_lights_low_beams": tool_schema(
                    "set_head_lights_low_beams",
                    {"on": {"type": "boolean"}},
                ),
                "set_head_lights_high_beams": tool_schema(
                    "set_head_lights_high_beams",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        result = ws.set_exterior_lights_safe(intent="turn_off_exterior_lights")

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(
            [[call["tool_name"] for call in request] for request in ws.bridge.requests],
            [["get_exterior_lights_status"], ["set_head_lights_low_beams"]],
        )
        self.assertEqual(self._emitted(ws, "set_head_lights_low_beams"), {"on": False})
        self.assertIsNone(self._emitted(ws, "set_fog_lights"))
        self.assertIsNone(self._emitted(ws, "set_head_lights_high_beams"))

    def test_climate_comfort_options_for_too_warm_have_no_side_effects(self):
        ws, ex = self.make({}, {})

        result = ex.run("present_climate_comfort_options(intent='too_warm')")

        self.assertIn("turn down seat heating", result.response_text)
        self.assertIn("Which would you prefer", result.response_text)
        self.assertEqual(ws.bridge.requests, [])
        self.assertEqual(
            ws.scratchpad["gates"]["present_climate_comfort_options"]["status"],
            "NEEDS_CLARIFICATION",
        )

    def test_climate_comfort_options_for_stuffy_air_have_no_side_effects(self):
        ws, ex = self.make({}, {})

        result = ex.run("present_climate_comfort_options(intent='stuffy_air')")

        self.assertIn("increasing the fan speed", result.response_text)
        self.assertIn("by how much", result.response_text)
        self.assertEqual(ws.bridge.requests, [])

    def test_comfort_followup_can_set_all_seat_heating_explicitly(self):
        ws, ex = self.make(
            {"set_seat_heating": ("SUCCESS", {})},
            {
                "set_seat_heating": tool_schema(
                    "set_seat_heating",
                    {"seat_zone": {"type": "string"}, "level": {"type": "integer"}},
                ),
            },
        )

        ex.run("set_seat_heating(seat_zone='ALL_ZONES', level=1)")

        self.assertEqual(
            self._emitted(ws, "set_seat_heating"),
            {"seat_zone": "ALL_ZONES", "level": 1},
        )

    def test_raw_sunroof_open_weather_unknown_routes_to_confirmation(self):
        ws, ex = self.make(
            {
                "get_sunroof_and_sunshade_position": (
                    "SUCCESS",
                    {"sunroof_position": 0, "sunshade_position": 100},
                ),
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "unknown"}}),
                "open_close_sunroof": ("SUCCESS", {}),
            },
            {
                "get_sunroof_and_sunshade_position": tool_schema(
                    "get_sunroof_and_sunshade_position",
                    {},
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                ),
                "open_close_sunroof": tool_schema(
                    "open_close_sunroof",
                    {"percentage": {"type": "number"}},
                ),
            },
        )

        result = ex.run("open_close_sunroof(percentage=50)")

        self.assertIn("weather condition is unavailable", result.response_text)
        self.assertIn("Please confirm with yes", result.response_text)
        self.assertIsNone(self._emitted(ws, "open_close_sunroof"))

        ws.observe_user("yes")
        confirmed = ex.run("handle_pending_confirmation()")

        self.assertEqual(confirmed.response_text, "Sunroof opened to 50%.")
        self.assertEqual(self._emitted(ws, "open_close_sunroof"), {"percentage": 50})

    def test_raw_sunroof_full_open_without_explicit_target_asks_percentage(self):
        ws, ex = self.make(
            {
                "get_sunroof_and_sunshade_position": (
                    "SUCCESS",
                    {"sunroof_position": 0, "sunshade_position": 100},
                ),
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "cloudy"}}),
                "open_close_sunroof": ("SUCCESS", {}),
            },
            {
                "get_sunroof_and_sunshade_position": tool_schema(
                    "get_sunroof_and_sunshade_position",
                    {},
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                ),
                "open_close_sunroof": tool_schema(
                    "open_close_sunroof",
                    {"percentage": {"type": "number"}},
                ),
            },
        )

        result = ex.run("open_close_sunroof(percentage=100)")

        self.assertEqual(result.response_text, "What percentage should I set the sunroof to?")
        self.assertIsNone(self._emitted(ws, "get_weather"))
        self.assertIsNone(self._emitted(ws, "open_close_sunroof"))
        self.assertNotIn("pending_confirmation", ws.scratchpad["facts"])

    def test_raw_window_open_above_25_with_unknown_ac_asks_confirmation(self):
        ws, ex = self.make(
            {
                "get_climate_settings": ("SUCCESS", {"air_conditioning": "unknown"}),
                "open_close_window": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "open_close_window": tool_schema(
                    "open_close_window",
                    {"window": {"type": "string"}, "percentage": {"type": "number"}},
                ),
            },
        )

        result = ex.run("open_close_window(window='DRIVER', percentage=50)")

        self.assertIn("AC status is unavailable", result.response_text)
        self.assertIn("percentage=50", result.response_text)
        self.assertIsNone(self._emitted(ws, "open_close_window"))

        ws.observe_user("yes")
        confirmed = ex.run("handle_pending_confirmation()")

        self.assertEqual(confirmed.response_text, "Window DRIVER set to 50%.")
        self.assertEqual(
            self._emitted(ws, "open_close_window"),
            {"window": "DRIVER", "percentage": 50},
        )

    def test_raw_window_full_open_without_explicit_target_asks_first(self):
        ws, ex = self.make(
            {
                "get_climate_settings": ("SUCCESS", {"air_conditioning": False}),
                "open_close_window": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "open_close_window": tool_schema(
                    "open_close_window",
                    {"window": {"type": "string"}, "percentage": {"type": "number"}},
                )
            },
        )
        ws.observe_user("Can you open all the windows for me?")

        result = ex.run("open_close_window(window='ALL', percentage=100)")

        self.assertIn("What percentage", result.response_text)
        self.assertIsNone(self._emitted(ws, "open_close_window"))

    def test_window_safe_helper_allows_explicit_full_target_argument(self):
        ws, ex = self.make(
            {
                "get_climate_settings": ("SUCCESS", {"air_conditioning": False}),
                "open_close_window": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "open_close_window": tool_schema(
                    "open_close_window",
                    {"window": {"type": "string"}, "percentage": {"type": "number"}},
                ),
            },
        )
        ex.run("open_close_window_safe(window='ALL', percentage=100, target_is_explicit=True)")

        self.assertEqual(
            self._emitted(ws, "open_close_window"),
            {"window": "ALL", "percentage": 100},
        )

    def test_raw_ac_on_delegates_to_policy_helper(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {"air_conditioning": False, "fan_speed": 0},
                ),
                "get_vehicle_window_positions": (
                    "SUCCESS",
                    {"window_driver_position": "unknown"},
                ),
                "open_close_window": ("SUCCESS", {}),
                "set_fan_speed": ("SUCCESS", {}),
                "set_air_conditioning": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "open_close_window": tool_schema(
                    "open_close_window",
                    {"window": {"type": "string"}, "percentage": {"type": "number"}},
                ),
                "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
                "set_air_conditioning": tool_schema(
                    "set_air_conditioning",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        result = ex.run("set_air_conditioning(on=True)\nrespond('AC is on.')")

        self.assertEqual(self._emitted(ws, "open_close_window"), {"window": "DRIVER", "percentage": 0})
        self.assertEqual(self._emitted(ws, "set_fan_speed"), {"level": 1})
        self.assertEqual(self._emitted(ws, "set_air_conditioning"), {"on": True})
        self.assertIn("driver window", result.response_text)
        self.assertIn("unknown position", result.response_text)

    def test_raw_all_defrost_delegates_to_safe_helper(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {
                        "air_conditioning": False,
                        "fan_speed": 0,
                        "fan_airflow_direction": "HEAD",
                    },
                ),
                "get_vehicle_window_positions": (
                    "SUCCESS",
                    {"window_passenger_position": "unknown"},
                ),
                "set_window_defrost": ("SUCCESS", {}),
                "set_fan_speed": ("SUCCESS", {}),
                "set_fan_airflow_direction": ("SUCCESS", {}),
                "open_close_window": ("SUCCESS", {}),
                "set_air_conditioning": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_window_defrost": tool_schema(
                    "set_window_defrost",
                    {"on": {"type": "boolean"}, "defrost_window": {"type": "string"}},
                ),
                "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
                "set_fan_airflow_direction": tool_schema(
                    "set_fan_airflow_direction",
                    {"direction": {"type": "string"}},
                ),
                "open_close_window": tool_schema(
                    "open_close_window",
                    {"window": {"type": "string"}, "percentage": {"type": "number"}},
                ),
                "set_air_conditioning": tool_schema(
                    "set_air_conditioning",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        result = ex.run(
            "set_window_defrost(on=True, defrost_window='ALL')\n"
            "respond('All-window defrost is now on.')"
        )

        self.assertEqual(
            self._emitted(ws, "set_window_defrost"),
            {"on": True, "defrost_window": "ALL"},
        )
        self.assertEqual(
            self._emitted(ws, "open_close_window"),
            {"window": "PASSENGER", "percentage": 0},
        )
        self.assertEqual(
            self._emitted(ws, "set_fan_airflow_direction"),
            {"direction": "WINDSHIELD"},
        )
        self.assertEqual(self._emitted(ws, "set_air_conditioning"), {"on": True})
        self.assertIn("passenger window", result.response_text)
        self.assertIn("unknown position", result.response_text)

    def test_ac_helper_closes_unknown_controllable_window_then_turns_ac_on(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {"air_conditioning": False, "fan_speed": 0},
                ),
                "get_vehicle_window_positions": (
                    "SUCCESS",
                    {
                        "window_driver_position": 25,
                        "window_passenger_position": 20,
                        "window_driver_rear_position": 20,
                        "window_passenger_rear_position": "unknown",
                    },
                ),
                "open_close_window": ("SUCCESS", {}),
                "set_fan_speed": ("SUCCESS", {}),
                "set_air_conditioning": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "open_close_window": tool_schema(
                    "open_close_window",
                    {"window": {"type": "string"}, "percentage": {"type": "number"}},
                ),
                "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
                "set_air_conditioning": tool_schema(
                    "set_air_conditioning",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        result = ex.run(
            "set_air_conditioning_on_safe()\n"
            "respond('Air conditioning is now on.')"
        )

        emitted = [
            (call["tool_name"], call["arguments"])
            for batch in ws.bridge.requests
            for call in batch
        ]
        self.assertIn(("open_close_window", {"window": "DRIVER", "percentage": 0}), emitted)
        self.assertIn(
            ("open_close_window", {"window": "PASSENGER_REAR", "percentage": 0}),
            emitted,
        )
        self.assertIn(("set_fan_speed", {"level": 1}), emitted)
        self.assertIn(("set_air_conditioning", {"on": True}), emitted)
        self.assertIn("passenger rear window", result.response_text)
        self.assertIn("unknown position", result.response_text)
        self.assertEqual(
            ws.scratchpad["facts"]["last_helper_report"]["unknown_windows"][0]["tool_window"],
            "PASSENGER_REAR",
        )

    def test_ac_helper_sets_preferred_air_circulation_when_requested(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {
                        "air_conditioning": False,
                        "fan_speed": 0,
                        "air_circulation": "RECIRCULATION",
                    },
                ),
                "get_vehicle_window_positions": (
                    "SUCCESS",
                    {
                        "window_driver_position": 0,
                        "window_passenger_position": 0,
                        "window_driver_rear_position": 0,
                        "window_passenger_rear_position": 0,
                    },
                ),
                "set_fan_speed": ("SUCCESS", {}),
                "set_air_conditioning": ("SUCCESS", {}),
                "set_air_circulation": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
                "set_air_conditioning": tool_schema(
                    "set_air_conditioning",
                    {"on": {"type": "boolean"}},
                ),
                "set_air_circulation": tool_schema(
                    "set_air_circulation",
                    {"mode": {"type": "string"}},
                ),
            },
        )
        ws.observe_user(
            "Turn on the air conditioning and set air circulation to my preferred mode."
        )
        ws.scratchpad["entities"]["user_preferences"] = {
            "preferences": {
                "vehicle_settings": {
                    "climate_control": [
                        "prefers air circulation on AUTO, only when air conditioning is on, "
                        "then prefers air circulation on FRESH_AIR"
                    ]
                }
            },
            "summary": [
                "vehicle_settings.climate_control: prefers air circulation on AUTO, only "
                "when air conditioning is on, then prefers air circulation on FRESH_AIR"
            ],
        }

        result = ex.run(
            "set_air_conditioning_on_safe(use_preferred_air_circulation=True)\n"
            "respond('AC and preferred circulation are set.')"
        )

        emitted = [
            (call["tool_name"], call["arguments"])
            for batch in ws.bridge.requests
            for call in batch
        ]
        self.assertIn(("set_air_conditioning", {"on": True}), emitted)
        self.assertIn(("set_air_circulation", {"mode": "FRESH_AIR"}), emitted)
        self.assertEqual(
            ws.scratchpad["facts"]["preferred_air_circulation_mode"],
            "FRESH_AIR",
        )

    def test_preferred_air_circulation_no_longer_repairs_raw_call_from_user_text(self):
        ws, ex = self.make(
            {"set_air_circulation": ("SUCCESS", {})},
            {
                "set_air_circulation": tool_schema(
                    "set_air_circulation",
                    {"mode": {"type": "string"}},
                )
            },
        )
        ws.observe_user(
            "Turn on AC and set air circulation to my preferred mode."
        )
        ws.scratchpad["entities"]["user_preferences"] = {
            "preferences": {
                "vehicle_settings": {
                    "climate_control": [
                        "prefers air circulation on AUTO, only when air conditioning is on, "
                        "then prefers air circulation on FRESH_AIR"
                    ]
                }
            }
        }

        result = ex.run(
            "set_air_circulation(mode='AUTO')\n"
            "respond('Air circulation is set to auto.')"
        )

        self.assertEqual(
            self._emitted(ws, "set_air_circulation"),
            {"mode": "AUTO"},
        )
        self.assertNotIn("air_circulation_preference_guard", ws.scratchpad["gates"])
        self.assertEqual(result.response_text, "Air circulation is set to auto.")

    def test_ac_helper_unknown_window_still_requires_window_control(self):
        ws, _ = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {"air_conditioning": False, "fan_speed": 0},
                ),
                "get_vehicle_window_positions": (
                    "SUCCESS",
                    {
                        "window_driver_position": 25,
                        "window_passenger_position": 20,
                        "window_driver_rear_position": 20,
                        "window_passenger_rear_position": "unknown",
                    },
                ),
                "set_fan_speed": ("SUCCESS", {}),
                "set_air_conditioning": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
                "set_air_conditioning": tool_schema(
                    "set_air_conditioning",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        with self.assertRaises(ResponseReady):
            ws.set_air_conditioning_on_safe()

        self.assertIn("open_close_window", ws._response_text or "")
        self.assertIsNone(self._emitted(ws, "set_air_conditioning"))

    def test_defrost_helper_closes_unknown_controllable_windows_then_turns_on(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {
                        "air_conditioning": False,
                        "fan_speed": 0,
                        "fan_airflow_direction": "HEAD",
                    },
                ),
                "get_vehicle_window_positions": (
                    "SUCCESS",
                    {
                        "window_driver_position": "unknown",
                        "window_passenger_position": "unknown",
                        "window_driver_rear_position": 25,
                        "window_passenger_rear_position": 100,
                    },
                ),
                "set_window_defrost": ("SUCCESS", {}),
                "set_fan_speed": ("SUCCESS", {}),
                "set_fan_airflow_direction": ("SUCCESS", {}),
                "open_close_window": ("SUCCESS", {}),
                "set_air_conditioning": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_window_defrost": tool_schema(
                    "set_window_defrost",
                    {"on": {"type": "boolean"}, "defrost_window": {"type": "string"}},
                ),
                "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
                "set_fan_airflow_direction": tool_schema(
                    "set_fan_airflow_direction",
                    {"direction": {"type": "string"}},
                ),
                "open_close_window": tool_schema(
                    "open_close_window",
                    {"window": {"type": "string"}, "percentage": {"type": "number"}},
                ),
                "set_air_conditioning": tool_schema(
                    "set_air_conditioning",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        result = ex.run(
            "defrost_front_window()\n"
            "respond('Front window defrost is now on.')"
        )

        emitted = [
            (call["tool_name"], call["arguments"])
            for batch in ws.bridge.requests
            for call in batch
        ]
        self.assertIn(("open_close_window", {"window": "ALL", "percentage": 0}), emitted)
        self.assertIn(("set_fan_speed", {"level": 2}), emitted)
        self.assertIn(("set_fan_airflow_direction", {"direction": "WINDSHIELD"}), emitted)
        self.assertIn(("set_air_conditioning", {"on": True}), emitted)
        self.assertIn(("set_window_defrost", {"on": True, "defrost_window": "FRONT"}), emitted)
        self.assertIn("driver and passenger windows", result.response_text)
        self.assertIn("unknown positions", result.response_text)
        self.assertEqual(
            [item["tool_window"] for item in ws.scratchpad["facts"]["last_helper_report"]["unknown_windows"]],
            ["DRIVER", "PASSENGER"],
        )

    def test_defrost_helper_preserves_existing_windshield_airflow(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {
                        "air_conditioning": True,
                        "fan_speed": 2,
                        "fan_airflow_direction": "WINDSHIELD_HEAD",
                    },
                ),
                "get_vehicle_window_positions": ("SUCCESS", {}),
                "set_window_defrost": ("SUCCESS", {}),
                "set_fan_airflow_direction": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_window_defrost": tool_schema(
                    "set_window_defrost",
                    {"on": {"type": "boolean"}, "defrost_window": {"type": "string"}},
                ),
                "set_fan_airflow_direction": tool_schema(
                    "set_fan_airflow_direction",
                    {"direction": {"type": "string"}},
                ),
            },
        )

        ex.run("defrost_front_window()")

        self.assertIsNone(self._emitted(ws, "set_fan_airflow_direction"))

    def test_defrost_helper_sets_windshield_when_airflow_lacks_windshield(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {
                        "air_conditioning": True,
                        "fan_speed": 2,
                        "fan_airflow_direction": "FEET",
                    },
                ),
                "get_vehicle_window_positions": ("SUCCESS", {}),
                "set_window_defrost": ("SUCCESS", {}),
                "set_fan_airflow_direction": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_window_defrost": tool_schema(
                    "set_window_defrost",
                    {"on": {"type": "boolean"}, "defrost_window": {"type": "string"}},
                ),
                "set_fan_airflow_direction": tool_schema(
                    "set_fan_airflow_direction",
                    {"direction": {"type": "string"}},
                ),
            },
        )

        ex.run("defrost_front_window()")

        self.assertEqual(
            self._emitted(ws, "set_fan_airflow_direction"),
            {"direction": "WINDSHIELD"},
        )

    def test_defrost_helper_uses_stored_windshield_feet_airflow_preference(self):
        ws, ex = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {
                        "air_conditioning": True,
                        "fan_speed": 2,
                        "fan_airflow_direction": "FEET",
                    },
                ),
                "get_vehicle_window_positions": ("SUCCESS", {}),
                "set_window_defrost": ("SUCCESS", {}),
                "set_fan_airflow_direction": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_window_defrost": tool_schema(
                    "set_window_defrost",
                    {"on": {"type": "boolean"}, "defrost_window": {"type": "string"}},
                ),
                "set_fan_airflow_direction": tool_schema(
                    "set_fan_airflow_direction",
                    {"direction": {"type": "string"}},
                ),
            },
        )
        ws.scratchpad["entities"]["user_preferences"] = {
            "preferences": {
                "vehicle_settings": {
                    "climate_control": [
                        "user always wants airflow direction to include FEET, "
                        "if window should be defrosted, then the user wants WINDSHIELD_FEET"
                    ]
                }
            }
        }

        ex.run("defrost_front_window()")

        self.assertEqual(
            self._emitted(ws, "set_fan_airflow_direction"),
            {"direction": "WINDSHIELD_FEET"},
        )

    def test_defrost_helper_unknown_window_still_requires_window_control(self):
        ws, _ = self.make(
            {
                "get_climate_settings": (
                    "SUCCESS",
                    {
                        "air_conditioning": False,
                        "fan_speed": 0,
                        "fan_airflow_direction": "HEAD",
                    },
                ),
                "get_vehicle_window_positions": (
                    "SUCCESS",
                    {
                        "window_driver_position": "unknown",
                        "window_passenger_position": 20,
                        "window_driver_rear_position": 20,
                        "window_passenger_rear_position": 20,
                    },
                ),
                "set_window_defrost": ("SUCCESS", {}),
                "set_fan_speed": ("SUCCESS", {}),
                "set_fan_airflow_direction": ("SUCCESS", {}),
                "set_air_conditioning": ("SUCCESS", {}),
            },
            {
                "get_climate_settings": tool_schema("get_climate_settings", {}),
                "get_vehicle_window_positions": tool_schema("get_vehicle_window_positions", {}),
                "set_window_defrost": tool_schema(
                    "set_window_defrost",
                    {"on": {"type": "boolean"}, "defrost_window": {"type": "string"}},
                ),
                "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
                "set_fan_airflow_direction": tool_schema(
                    "set_fan_airflow_direction",
                    {"direction": {"type": "string"}},
                ),
                "set_air_conditioning": tool_schema(
                    "set_air_conditioning",
                    {"on": {"type": "boolean"}},
                ),
            },
        )

        with self.assertRaises(ResponseReady):
            ws.defrost_front_window()

        self.assertIn("open_close_window", ws._response_text or "")
        self.assertIsNone(self._emitted(ws, "set_window_defrost"))

    # --- 2. active-navigation guard --------------------------------------

    def _nav_schema(self):
        return tool_schema(
            "set_new_navigation",
            {"route_ids": {"type": "array", "items": {"type": "string"}}},
        )

    def test_set_new_navigation_blocked_when_active(self):
        ws, ex = self.make({}, {"set_new_navigation": self._nav_schema()})
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": True}
        result = ex.run(
            "r = set_new_navigation(route_ids=['route_9'])\n"
            "respond(r['status'])"
        )
        # Runtime enforces the FACT (active route) but does not pick the edit.
        self.assertEqual(result.response_text, "NEEDS_ACTIVE_ROUTE_EDIT")
        # The invalid call must never reach the bridge.
        self.assertEqual(ws.bridge.requests, [])

    def test_distance_by_soc_dynamic_key_aliased(self):
        ws, ex = self.make(
            {"get_distance_by_soc": ("SUCCESS", {"distance_km_for_85.0_until_0.0_percent_soc": "323.0km"})},
            {"get_distance_by_soc": tool_schema(
                "get_distance_by_soc",
                {"initial_state_of_charge": {"type": "integer"}, "final_state_of_charge": {"type": "integer"}})},
        )
        result = ex.run(
            "r = get_distance_by_soc(initial_state_of_charge=85, final_state_of_charge=0)\n"
            "respond(str(r.get('distance_km')))"
        )
        self.assertEqual(result.response_text, "323")
        self.assertEqual(
            ws.scratchpad["entities"]["last_distance_by_soc"]["distance_km"],
            323,
        )
        self.assertEqual(
            ws.scratchpad["entities"]["last_distance_by_soc"]["distance_raw"],
            "323.0km",
        )

    def test_first_number_value_accepts_normalized_distance_dict(self):
        ws, ex = self.make(
            {"get_distance_by_soc": ("SUCCESS", {"distance_km_for_100.0_until_0.0_percent_soc": "507.0km"})},
            {"get_distance_by_soc": tool_schema(
                "get_distance_by_soc",
                {"initial_state_of_charge": {"type": "integer"}, "final_state_of_charge": {"type": "integer"}})},
        )
        result = ex.run(
            "distance = get_distance_by_soc_value(initial_state_of_charge=100, final_state_of_charge=0)\n"
            "respond(str(first_number_value(distance)))"
        )
        self.assertEqual(result.response_text, "507.0")

    def test_remaining_range_numeric_alias(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 98, "remaining_range": "466.0km"},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )
        result = ex.run(
            "status = get_charging_specs_and_status()\n"
            "respond(str(status['remaining_range_km']))"
        )
        self.assertEqual(result.response_text, "466")

    def test_remaining_range_raw_key_is_numeric_in_model_result_and_scratchpad(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 35, "remaining_range": "155.0km"},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )

        result = ex.run(
            "status = get_charging_specs_and_status()\n"
            "stored = scratchpad['entities']['last_charging_specs_and_status']\n"
            "respond('|'.join([\n"
            "    str(status['remaining_range'] >= 100),\n"
            "    str(status['remaining_range_km']),\n"
            "    str(stored['remaining_range'] >= 100),\n"
            "    str(stored['remaining_range_km']),\n"
            "    str(status['remaining_range_raw']),\n"
            "]))"
        )
        self.assertEqual(result.response_text, "True|155|True|155|155.0km")

    def test_unparseable_remaining_range_is_unknown_range(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 70, "remaining_range": "not available"},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )
        ws.observe_user("Can I drive to Hamburg with my current range?")

        result = ex.run(
            "status = get_charging_specs_and_status()\n"
            "if status['remaining_range_km'] >= 100:\n"
            "    respond('You have enough range.')\n"
            "else:\n"
            "    respond('You do not have enough range.')"
        )

        self.assertIn("did not provide the remaining range", result.response_text)
        self.assertEqual(
            ws.scratchpad["entities"]["last_charging_specs_and_status"][
                "remaining_range_raw"
            ],
            "not available",
        )

    def test_unknown_remaining_range_blocks_downstream_charging_math(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 70, "remaining_range": "unknown"},
                ),
                "calculate_charging_time_by_soc": (
                    "SUCCESS",
                    {"time_from_70_until_100_percent_soc": "10min"},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
                "calculate_charging_time_by_soc": tool_schema(
                    "calculate_charging_time_by_soc",
                    {
                        "charging_station_id": {"type": "string"},
                        "charging_station_plug_id": {"type": "string"},
                        "start_state_of_charge": {"type": "integer"},
                        "target_state_of_charge": {"type": "integer"},
                    },
                ),
            },
        )
        ws.observe_user("Is my remaining range enough, and how long to charge to 100%?")

        result = ex.run(
            "get_charging_specs_and_status()\n"
            "calculate_charging_time_by_soc("
            "charging_station_id='poi_cha_1', "
            "charging_station_plug_id='plug_1', "
            "start_state_of_charge=70, "
            "target_state_of_charge=100)"
        )

        self.assertIn("did not provide the remaining range", result.response_text)
        self.assertIn("complete charging-stop planning", result.response_text)
        emitted_tools = [
            call["tool_name"] for batch in ws.bridge.requests for call in batch
        ]
        self.assertEqual(emitted_tools, ["get_charging_specs_and_status"])

    def test_unknown_remaining_range_repairs_unknown_km_response(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 70, "remaining_range": "unknown"},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )
        ws.observe_user("Is my remaining range enough for this trip?")

        result = ex.run(
            "get_charging_specs_and_status()\n"
            "respond('Your remaining range is only unknown km, which is insufficient.')"
        )

        self.assertIn("did not provide the remaining range", result.response_text)
        self.assertNotIn("unknown km", result.response_text)

    def test_unknown_remaining_range_repairs_sufficient_range_claim(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 70, "remaining_range": "unknown"},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )
        ws.observe_user("Can you include travel and charging details in an email?")

        result = ex.run(
            "get_charging_specs_and_status()\n"
            "respond('The current battery range is sufficient for the trip without charging stops.')"
        )

        self.assertIn("did not provide the remaining range", result.response_text)
        self.assertIn("complete charging-stop planning", result.response_text)
        self.assertNotIn("sufficient", result.response_text)

    def test_missing_remaining_range_alias_blocks_direct_model_access(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 70},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )
        ws.observe_user("Do I have enough remaining range for this trip?")

        result = ex.run(
            "status = get_charging_specs_and_status()\n"
            "respond(f\"Your remaining range is {status['remaining_range_km']} km.\")"
        )

        self.assertIn("did not provide the remaining range", result.response_text)
        self.assertIn("charging_range_unknown", ws.scratchpad["gates"])

    def test_none_remaining_range_alias_blocks_direct_model_math(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 70, "remaining_range": None},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )
        ws.observe_user("Can I drive to Hamburg with my current range?")

        result = ex.run(
            "status = get_charging_specs_and_status()\n"
            "if status['remaining_range_km'] >= 100:\n"
            "    respond('You have enough range.')\n"
            "else:\n"
            "    respond('You do not have enough range.')"
        )

        self.assertIn("did not provide the remaining range", result.response_text)
        emitted_tools = [
            call["tool_name"] for batch in ws.bridge.requests for call in batch
        ]
        self.assertEqual(emitted_tools, ["get_charging_specs_and_status"])

    def test_route_display_includes_toll_disclosure(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_toll",
                                "start_id": "loc_start",
                                "destination_id": "loc_dest",
                                "name_via": "A1",
                                "distance_km": 10,
                                "duration_hours": 0,
                                "duration_minutes": 12,
                                "road_types": ["highway", "includes toll road"],
                                "alias": ["fastest", "shortest"],
                            }
                        ]
                    },
                )
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
            },
        )
        result = ex.run(
            "routes = get_route_options(start_id='loc_start', destination_id='loc_dest')\n"
            "respond(routes['routes'][0]['display'])"
        )
        self.assertIn("includes toll roads", result.response_text)

    def test_unknown_route_options_abort_with_lookup_limitation(self):
        ws, ex = self.make(
            {
                "get_location_id_by_location_name": (
                    "SUCCESS",
                    {"id": "loc_mil_253463"},
                ),
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": "unknown"},
                ),
            },
            {
                "get_location_id_by_location_name": tool_schema(
                    "get_location_id_by_location_name",
                    {"location": {"type": "string"}},
                    required=["location"],
                ),
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
            },
        )

        ex.run("get_location_id_by_location_name(location='Milan')")
        ws.remember_entity(
            "locations_by_id",
            {
                "loc_mil_253463": {"id": "loc_mil_253463", "name": "Milan"},
                "loc_pra_198238": {"id": "loc_pra_198238", "name": "Prague"},
            },
        )
        result = ex.run(
            "get_routes_from_start_to_destination("
            "start_id='loc_mil_253463', destination_id='loc_pra_198238')"
        )

        self.assertIsNone(result.error)
        self.assertIn("I can't determine whether the current range is enough", result.response_text)
        self.assertIn("I looked it up", result.response_text)
        self.assertIn("route options or distance from Milan to Prague", result.response_text)
        self.assertEqual(
            [
                call["tool_name"]
                for request in ws.bridge.requests
                for call in request
                if call["tool_name"] == "get_routes_from_start_to_destination"
            ],
            ["get_routes_from_start_to_destination"],
        )

    def test_get_route_options_unknown_routes_abort_with_lookup_limitation(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": "unknown"},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
            },
        )
        ws.remember_entity(
            "locations_by_id",
            {
                "loc_mil_253463": {"id": "loc_mil_253463", "name": "Milan"},
                "loc_pra_198238": {"id": "loc_pra_198238", "name": "Prague"},
            },
        )

        result = ex.run(
            "get_route_options("
            "start_id='loc_mil_253463', destination_id='loc_pra_198238')"
        )

        self.assertIsNone(result.error)
        self.assertIn("route options or distance from Milan to Prague", result.response_text)

    def test_get_route_options_adds_route_presentation_obligation(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "R_fast",
                                "start_id": "loc_start",
                                "destination_id": "loc_dest",
                                "name_via": "A1",
                                "distance_km": 10,
                                "duration_hours": 0,
                                "duration_minutes": 10,
                                "alias": ["fastest", "first"],
                            },
                            {
                                "route_id": "R_short",
                                "start_id": "loc_start",
                                "destination_id": "loc_dest",
                                "name_via": "B2",
                                "distance_km": 8,
                                "duration_hours": 0,
                                "duration_minutes": 12,
                                "alias": ["shortest", "second"],
                            },
                        ],
                    },
                )
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
            },
        )

        result = ex.run(
            "get_route_options(start_id='loc_start', destination_id='loc_dest')\n"
            "respond('Route information ready.')"
        )

        self.assertIn("Route information ready.", result.response_text)
        self.assertIn("The fastest route", result.response_text)
        self.assertIn("other option", result.response_text)
        self.assertIn("Would you like details", result.response_text)
        self.assertNotIn("navigate", result.response_text.lower())

    def test_select_poi_keeps_explicit_named_station_for_plug_selection(self):
        ws, ex = self.make({}, {})

        result = ex.run(
            "pois = [\n"
            "    {'id': 'poi_ionity', 'name': 'Ionity', 'category': 'charging_stations',\n"
            "     'charging_plugs': [{'plug_id': 'plug_ionity', 'power_type': 'DC', 'power_kw': 100, 'availability': 'available'}]},\n"
            "    {'id': 'poi_tesla', 'name': 'Tesla Supercharger', 'category': 'charging_stations',\n"
            "     'charging_plugs': [{'plug_id': 'plug_tesla', 'power_type': 'DC', 'power_kw': 350, 'availability': 'available'}]},\n"
            "]\n"
            "selected = select_poi(pois, name='Ionity')\n"
            "plug = select_charging_plug(pois=[selected['poi']])\n"
            "respond(plug['charging_station_id'] + '|' + plug['charging_station_plug_id'])"
        )

        self.assertEqual(result.response_text, "poi_ionity|plug_ionity")
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_poi"]["poi_id"],
            "poi_ionity",
        )

    def test_select_poi_records_role_specific_alias(self):
        ws, ex = self.make({}, {})

        result = ex.run(
            "pois = [\n"
            "    {'id': 'poi_ionity', 'name': 'Ionity', 'category': 'charging_stations'},\n"
            "    {'id': 'poi_burger', 'name': 'Burger Stop', 'category': 'fast_food'},\n"
            "]\n"
            "selected = select_poi(pois, name='Ionity', role='charging_stop')\n"
            "respond(selected['poi_id'] + '|' + "
            "scratchpad['entities']['selected_charging_stop_poi']['poi_id'])"
        )

        self.assertEqual(result.response_text, "poi_ionity|poi_ionity")
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_stop_poi"]["role"],
            "charging_stop",
        )
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_poi"]["poi_id"],
            "poi_ionity",
        )

    def test_select_charging_plug_persists_selected_station_identity(self):
        ws, ex = self.make({}, {})

        result = ex.run(
            "pois = [\n"
            "    {'id': 'poi_fast', 'name': 'Fast Charge', 'category': 'charging_stations',\n"
            "     'navigation_id': 'poi_fast_nav',\n"
            "     'charging_plugs': [{'plug_id': 'plug_fast', 'power_type': 'DC', 'power_kw': 250}]},\n"
            "]\n"
            "plug = select_charging_plug(pois)\n"
            "respond(plug['charging_station_id'] + '|' + scratchpad['entities']['selected_charging_poi']['poi_id'])"
        )

        self.assertEqual(result.response_text, "poi_fast|poi_fast")
        self.assertEqual(
            ws.scratchpad["entities"]["selected_poi"]["navigation_id"],
            "poi_fast_nav",
        )

    def test_charging_time_uses_explicitly_named_station(self):
        ionity = {
            "id": "poi_ionity",
            "poi_id": "poi_ionity",
            "navigation_id": "poi_ionity",
            "name": "Ionity",
            "category": "charging_stations",
            "charging_plugs": [
                {"plug_id": "plug_ionity_ac", "power_kw": 11, "power_type": "AC"},
                {"plug_id": "plug_ionity_dc", "power_kw": 100, "power_type": "DC"},
            ],
        }
        tesla = {
            "id": "poi_tesla",
            "poi_id": "poi_tesla",
            "navigation_id": "poi_tesla",
            "name": "Tesla Supercharger",
            "category": "charging_stations",
            "charging_plugs": [
                {"plug_id": "plug_tesla_dc", "power_kw": 350, "power_type": "DC"},
            ],
        }
        ws, ex = self.make(
            {"calculate_charging_time_by_soc": ("SUCCESS", {"minutes": 19})},
            {
                "calculate_charging_time_by_soc": tool_schema(
                    "calculate_charging_time_by_soc",
                    {
                        "charging_station_id": {"type": "string"},
                        "charging_station_plug_id": {"type": "string"},
                        "start_state_of_charge": {"type": "number"},
                        "target_state_of_charge": {"type": "number"},
                    },
                )
            },
        )
        ws.scratchpad["entities"]["last_pois"] = [ionity, tesla]
        ws.scratchpad["entities"]["pois_by_id"] = {
            "poi_ionity": ionity,
            "poi_tesla": tesla,
        }
        ex.run(
            "poi = select_poi(pois=last_pois, name='Ionity')\n"
            "plug = select_charging_plug(pois=[poi['poi']])\n"
            "calculate_charging_time_by_soc("
            "charging_station_id=poi['poi_id'], "
            "charging_station_plug_id=plug['charging_station_plug_id'], "
            "start_state_of_charge=35, "
            "target_state_of_charge=95)"
        )

        args = self._emitted(ws, "calculate_charging_time_by_soc")
        self.assertEqual(args["charging_station_id"], "poi_ionity")
        self.assertEqual(args["charging_station_plug_id"], "plug_ionity_dc")
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_poi"]["poi"]["name"],
            "Ionity",
        )

    def test_charging_time_persists_known_station_as_selected_charging_poi(self):
        ionity = {
            "id": "poi_ionity",
            "poi_id": "poi_ionity",
            "navigation_id": "poi_ionity",
            "name": "Ionity",
            "category": "charging_stations",
            "charging_plugs": [
                {"plug_id": "plug_ionity_dc", "power_kw": 100, "power_type": "DC"},
            ],
        }
        ws, ex = self.make(
            {
                "calculate_charging_time_by_soc": (
                    "SUCCESS",
                    {"time_from_35_until_95_percent_soc": "28min"},
                )
            },
            {
                "calculate_charging_time_by_soc": tool_schema(
                    "calculate_charging_time_by_soc",
                    {
                        "charging_station_id": {"type": "string"},
                        "charging_station_plug_id": {"type": "string"},
                        "start_state_of_charge": {"type": "number"},
                        "target_state_of_charge": {"type": "number"},
                    },
                )
            },
        )
        ws.scratchpad["entities"]["pois_by_id"] = {"poi_ionity": ionity}

        ex.run(
            "calculate_charging_time_by_soc("
            "charging_station_id='poi_ionity', "
            "charging_station_plug_id='plug_ionity_dc', "
            "start_state_of_charge=35, "
            "target_state_of_charge=95)"
        )

        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_poi"]["navigation_id"],
            "poi_ionity",
        )
        self.assertEqual(
            ws.scratchpad["selected_charging_poi"]["navigation_id"],
            "poi_ionity",
        )
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_plan"]["name"],
            "Ionity",
        )

    def test_selected_entity_aliases_are_available_at_scratchpad_top_level(self):
        ws, ex = self.make({}, {})
        ws.remember_entity(
            "selected_charging_poi",
            {"navigation_id": "poi_ionity", "name": "Ionity"},
        )
        ws.remember_entity(
            "last_location_lookup",
            {"location_id": "loc_hamburg", "name": "Hamburg"},
        )
        ws.remember_entity(
            "last_routes",
            [{"route_id": "route_hamburg_second", "destination_id": "loc_hamburg"}],
        )
        ws.remember_entity(
            "selected_charging_plug",
            {"charging_station_plug_id": "plug_ionity_dc", "power_kw": 100},
        )

        result = ex.run(
            "respond(scratchpad['selected_charging_poi']['navigation_id'] + '|' + "
            "scratchpad['last_location_lookup']['location_id'] + '|' + "
            "scratchpad['selected_charging_plug']['charging_station_plug_id'] + '|' + "
            "scratchpad['last_routes'][0]['route_id'])"
        )

        self.assertEqual(
            result.response_text,
            "poi_ionity|loc_hamburg|plug_ionity_dc|route_hamburg_second",
        )

        result = ex.run(
            "respond(selected_charging_poi['navigation_id'] + '|' + "
            "last_location_lookup['location_id'] + '|' + "
            "selected_charging_plug['charging_station_plug_id'] + '|' + "
            "last_routes[0]['route_id'])"
        )

        self.assertEqual(
            result.response_text,
            "poi_ionity|loc_hamburg|plug_ionity_dc|route_hamburg_second",
        )

    def test_route_endpoint_uses_selected_charging_stop(self):
        ionity = {
            "id": "poi_ionity",
            "poi_id": "poi_ionity",
            "navigation_id": "poi_ionity",
            "name": "Ionity",
            "category": "charging_stations",
        }
        ws, ex = self.make(
            {"get_routes_from_start_to_destination": ("SUCCESS", {"routes": []})},
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                )
            },
        )
        ws.remember_entity(
            "selected_charging_poi",
            {
                "status": "SUCCESS",
                "poi": ionity,
                "poi_id": "poi_ionity",
                "navigation_id": "poi_ionity",
                "name": "Ionity",
            },
        )
        ws.observe_user("Set navigation through the charging stop.")

        ex.run(
            "get_routes_from_start_to_destination("
            "start_id='loc_war', destination_id='poi_cha_tesla')"
        )

        args = self._emitted(ws, "get_routes_from_start_to_destination")
        self.assertEqual(args["destination_id"], "poi_ionity")

    def test_set_new_navigation_via_stop_selects_each_leg_and_calls_guard(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "R_start_stop_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "poi_stop",
                                    "name_via": "A1",
                                    "distance_km": 4,
                                    "duration_hours": 0,
                                    "duration_minutes": 5,
                                    "alias": ["fastest", "first"],
                                },
                                {
                                    "route_id": "R_start_stop_second",
                                    "start_id": "loc_home_1",
                                    "destination_id": "poi_stop",
                                    "name_via": "B2",
                                    "distance_km": 3,
                                    "duration_hours": 0,
                                    "duration_minutes": 6,
                                    "alias": ["second", "shortest"],
                                },
                            ],
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "R_stop_dest_fast",
                                    "start_id": "poi_stop",
                                    "destination_id": "loc_dest",
                                    "name_via": "A9",
                                    "distance_km": 100,
                                    "duration_hours": 1,
                                    "duration_minutes": 20,
                                    "alias": ["fastest", "first"],
                                },
                                {
                                    "route_id": "R_stop_dest_second",
                                    "start_id": "poi_stop",
                                    "destination_id": "loc_dest",
                                    "name_via": "B432, B132",
                                    "distance_km": 102,
                                    "duration_hours": 1,
                                    "duration_minutes": 25,
                                    "alias": ["second"],
                                },
                            ],
                        },
                    ),
                ],
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}

        result = ex.run(
            "r = set_new_navigation_via_stop(\n"
            "    stop_id='poi_stop',\n"
            "    final_destination_id='loc_dest',\n"
            "    route_to_stop_prefer='fastest',\n"
            "    route_to_final_alias='second',\n"
            ")\n"
            "respond('|'.join(r['route_ids']))"
        )

        args = self._emitted(ws, "set_new_navigation")
        self.assertEqual(
            args["route_ids"],
            ["R_start_stop_fast", "R_stop_dest_second"],
        )
        self.assertIn("R_start_stop_fast|R_stop_dest_second", result.response_text)

    def test_set_navigation_via_route_stop_with_open_poi_searches_window_and_sets_route(self):
        policy = (
            'CURRENT_LOCATION = {"id": "loc_monaco", "name": "Monaco"}\n'
            'DATETIME = {"year": 2025, "month": 11, "day": 18, "hour": 17, "minute": 10}\n'
        )
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_main_fast",
                                    "start_id": "loc_monaco",
                                    "destination_id": "loc_final",
                                    "name_via": "A1",
                                    "distance_km": 596.53,
                                    "duration_hours": 7,
                                    "duration_minutes": 19,
                                    "alias": ["fastest", "first", "shortest"],
                                },
                                {
                                    "route_id": "route_main_second",
                                    "start_id": "loc_monaco",
                                    "destination_id": "loc_final",
                                    "distance_km": 600,
                                    "duration_hours": 7,
                                    "duration_minutes": 40,
                                    "alias": ["second"],
                                },
                            ],
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_to_stop_fast",
                                    "start_id": "loc_monaco",
                                    "destination_id": "poi_charger_200",
                                    "distance_km": 202,
                                    "duration_hours": 2,
                                    "duration_minutes": 30,
                                    "alias": ["fastest", "first", "shortest"],
                                }
                            ],
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_stop_final_fast",
                                    "start_id": "poi_charger_200",
                                    "destination_id": "loc_final",
                                    "distance_km": 399,
                                    "duration_hours": 4,
                                    "duration_minutes": 55,
                                    "alias": ["fastest", "first", "shortest"],
                                }
                            ],
                        },
                    ),
                ],
                "search_poi_along_the_route": [
                    (
                        "SUCCESS",
                        {
                            "pois_found_along_route": [
                                {
                                    "id": "poi_food_150",
                                    "name": "Closed Burger",
                                    "category": "fast_food",
                                    "opening_hours": "06:00h - 18:00h",
                                    "route_positions": {
                                        "route_main_fast": {"at_route_kilometer": 150.0}
                                    },
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "pois_found_along_route": [
                                {
                                    "id": "poi_charger_150",
                                    "name": "Early Charge",
                                    "category": "charging_stations",
                                    "opening_hours": "00:00h - 24:00h",
                                    "route_positions": {
                                        "route_main_fast": {"at_route_kilometer": 150.0}
                                    },
                                    "charging_plugs": [
                                        {
                                            "plug_id": "plug_150",
                                            "power_type": "DC",
                                            "power_kw": 150,
                                            "availability": "occupied",
                                        }
                                    ],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "pois_found_along_route": [
                                {
                                    "id": "poi_food_200",
                                    "name": "Open Burger",
                                    "category": "fast_food",
                                    "opening_hours": "06:00h - 23:00h",
                                    "route_positions": {
                                        "route_main_fast": {"at_route_kilometer": 200.0}
                                    },
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "pois_found_along_route": [
                                {
                                    "id": "poi_charger_200",
                                    "name": "Dinner Charge",
                                    "category": "charging_stations",
                                    "opening_hours": "00:00h - 24:00h",
                                    "route_positions": {
                                        "route_main_fast": {"at_route_kilometer": 200.0}
                                    },
                                    "charging_plugs": [
                                        {
                                            "plug_id": "plug_200",
                                            "power_type": "DC",
                                            "power_kw": 300,
                                            "availability": "occupied",
                                        }
                                    ],
                                }
                            ]
                        },
                    ),
                ],
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                    required=["start_id", "destination_id"],
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                    required=["route_id", "category_poi", "at_kilometer"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
            policy=policy,
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}

        result = ex.run(
            "plan = set_navigation_via_route_stop_with_open_poi(\n"
            "    destination_id='loc_final',\n"
            "    stop_category_poi='charging_stations',\n"
            "    companion_category_poi='fast_food',\n"
            "    window_start_hour=19,\n"
            "    window_start_minute=0,\n"
            "    window_end_hour=19,\n"
            "    window_end_minute=45,\n"
            ")\n"
            "respond(plan['selected_stop']['name'] + '|' + "
            "plan['selected_companion_poi']['name'] + '|' + "
            "','.join(plan['route_ids']))"
        )

        self.assertIn("Dinner Charge|Open Burger|route_to_stop_fast,route_stop_final_fast", result.response_text)
        report = ws.scratchpad["facts"]["last_helper_report"]
        self.assertEqual(report["name"], "set_navigation_via_route_stop_with_open_poi")
        self.assertTrue(report["selected_stop_is_navigation_waypoint"])
        self.assertFalse(report["selected_companion_is_navigation_waypoint"])
        self.assertEqual(report["stop_category_poi"], "charging_stations")
        self.assertEqual(report["companion_category_poi"], "fast_food")
        self.assertIn("Dinner Charge as the intermediate charging_stations stop", report["message"])
        self.assertIn("Open Burger is the matching fast_food POI", report["message"])
        calls = [call for batch in ws.bridge.requests for call in batch]
        search_args = [
            call["arguments"]
            for call in calls
            if call["tool_name"] == "search_poi_along_the_route"
        ]
        self.assertEqual(
            search_args,
            [
                {
                    "route_id": "route_main_fast",
                    "category_poi": "fast_food",
                    "at_kilometer": 150.0,
                },
                {
                    "route_id": "route_main_fast",
                    "category_poi": "charging_stations",
                    "at_kilometer": 150.0,
                },
                {
                    "route_id": "route_main_fast",
                    "category_poi": "fast_food",
                    "at_kilometer": 200.0,
                },
                {
                    "route_id": "route_main_fast",
                    "category_poi": "charging_stations",
                    "at_kilometer": 200.0,
                },
            ],
        )
        self.assertEqual(
            self._emitted(ws, "set_new_navigation"),
            {"route_ids": ["route_to_stop_fast", "route_stop_final_fast"]},
        )
        self.assertEqual(
            ws.scratchpad["entities"]["selected_route_stop_poi"]["poi_id"],
            "poi_charger_200",
        )
        self.assertEqual(
            ws.scratchpad["entities"]["selected_companion_poi"]["poi_id"],
            "poi_food_200",
        )
        self.assertEqual(
            ws.scratchpad["entities"]["selected_poi"]["poi_id"],
            "poi_charger_200",
        )

    def test_get_weather_at_route_arrival_uses_route_duration(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_1",
                                "start_id": "loc_home_1",
                                "destination_id": "loc_dest",
                                "name_via": "A1",
                                "distance_km": 100,
                                "duration_hours": 3,
                                "duration_minutes": 45,
                                "alias": ["fastest", "shortest"],
                            }
                        ]
                    },
                ),
                "get_weather": (
                    "SUCCESS",
                    {"current_slot": {"condition": "rain"}},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
            },
        )
        result = ex.run(
            "weather = get_weather_at_route_arrival(location_or_poi_id='loc_dest')\n"
            "respond(weather['result']['current_slot']['condition'])"
        )
        self.assertEqual(result.response_text, "rain")
        weather_args = self._emitted(ws, "get_weather")
        self.assertEqual(weather_args["time_hour_24hformat"], 18)
        self.assertEqual(weather_args["time_minutes"], 15)

    def test_weather_conditioned_navigation_uses_fallback_route_preference(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_primary",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_primary",
                                    "distance_km": 100,
                                    "duration_hours": 3,
                                    "duration_minutes": 45,
                                    "alias": ["fastest", "shortest"],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_fallback_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 140,
                                    "duration_hours": 2,
                                    "duration_minutes": 0,
                                    "alias": ["fastest", "first"],
                                },
                                {
                                    "route_id": "route_fallback_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 90,
                                    "duration_hours": 2,
                                    "duration_minutes": 30,
                                    "alias": ["shortest", "second"],
                                },
                            ]
                        },
                    ),
                ],
                "get_weather": (
                    "SUCCESS",
                    {"current_slot": {"condition": "rain"}},
                ),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )
        result = ex.run(
            "r = set_navigation_conditioned_on_arrival_weather(\n"
            "    primary_destination_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    avoid_conditions=['rain', 'hail'],\n"
            "    route_prefer='shortest',\n"
            ")\n"
            "respond(r['branch'] + '|' + r['route_id'])"
        )
        self.assertTrue(result.response_text.startswith("fallback|route_fallback_short"))
        self.assertEqual(
            self._emitted(ws, "set_new_navigation"),
            {"route_ids": ["route_fallback_short"]},
        )
        weather_args = self._emitted(ws, "get_weather")
        self.assertEqual(weather_args["location_or_poi_id"], "loc_primary")
        self.assertEqual(weather_args["time_hour_24hformat"], 18)
        self.assertEqual(weather_args["time_minutes"], 15)

    def test_navigate_by_arrival_weather_alias_uses_same_protocol(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_primary_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_primary",
                                    "distance_km": 120,
                                    "duration_hours": 1,
                                    "duration_minutes": 0,
                                    "alias": ["fastest", "shortest"],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_fallback_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 80,
                                    "duration_hours": 1,
                                    "duration_minutes": 50,
                                    "alias": ["shortest"],
                                },
                                {
                                    "route_id": "route_fallback_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 90,
                                    "duration_hours": 1,
                                    "duration_minutes": 20,
                                    "alias": ["fastest"],
                                },
                            ]
                        },
                    ),
                ],
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "hail"}}),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )

        result = ex.run(
            "r = navigate_by_arrival_weather(\n"
            "    primary_destination_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    avoid_conditions=['rain', 'hail'],\n"
            "    route_prefer='shortest',\n"
            ")\n"
            "respond(r['branch'] + '|' + r['route_id'])"
        )

        self.assertTrue(result.response_text.startswith("fallback|route_fallback_short"))
        self.assertEqual(
            self._emitted(ws, "set_new_navigation"),
            {"route_ids": ["route_fallback_short"]},
        )

    def test_navigate_to_poi_by_arrival_weather_blocks_before_poi_search(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_primary_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_primary",
                                    "distance_km": 120,
                                    "duration_hours": 1,
                                    "duration_minutes": 45,
                                    "alias": ["shortest"],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_fallback_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 92,
                                    "duration_hours": 1,
                                    "duration_minutes": 10,
                                    "alias": ["fastest"],
                                },
                                {
                                    "route_id": "route_fallback_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 86,
                                    "duration_hours": 1,
                                    "duration_minutes": 15,
                                    "alias": ["shortest"],
                                },
                            ]
                        },
                    ),
                ],
                "get_weather": (
                    "SUCCESS",
                    {"current_slot": {"condition": "cloudy_and_rain_and_hail"}},
                ),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                    required=["location_id", "category_poi"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )

        result = ex.run(
            "r = navigate_to_poi_by_arrival_weather(\n"
            "    primary_location_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    category_poi='charging_stations',\n"
            "    avoid_conditions=['rain', 'hail'],\n"
            "    poi_prefer='fastest_charging',\n"
            "    route_prefer='shortest',\n"
            ")\n"
            "respond(r['branch'] + '|' + r['route_id'])"
        )

        self.assertTrue(result.response_text.startswith("fallback|route_fallback_short"))
        self.assertIn(
            "Arrival weather at loc_primary is cloudy_and_rain_and_hail, so "
            "navigation is set to loc_fallback using the shortest route.",
            result.response_text,
        )
        emitted_names = [
            call["tool_name"] for batch in ws.bridge.requests for call in batch
        ]
        self.assertNotIn("search_poi_at_location", emitted_names)
        self.assertEqual(
            self._emitted(ws, "set_new_navigation"),
            {"route_ids": ["route_fallback_short"]},
        )

    def test_navigate_to_poi_unless_arrival_weather_alias_blocks_before_poi_search(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_primary",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_primary",
                                    "distance_km": 120,
                                    "duration_hours": 1,
                                    "duration_minutes": 45,
                                    "alias": ["fastest", "shortest"],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_fallback_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 86,
                                    "duration_hours": 1,
                                    "duration_minutes": 15,
                                    "alias": ["shortest"],
                                },
                                {
                                    "route_id": "route_fallback_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 92,
                                    "duration_hours": 1,
                                    "duration_minutes": 10,
                                    "alias": ["fastest"],
                                },
                            ]
                        },
                    ),
                ],
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "rain"}}),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )

        result = ex.run(
            "r = navigate_to_poi_unless_arrival_weather(\n"
            "    primary_location_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    category_poi='charging_stations',\n"
            "    avoid_conditions=['rain'],\n"
            "    route_prefer='shortest',\n"
            ")\n"
            "respond(r['branch'] + '|' + r['route_id'])"
        )

        self.assertTrue(result.response_text.startswith("fallback|route_fallback_short"))
        self.assertIn(
            "Arrival weather at loc_primary is rain, so navigation is set to "
            "loc_fallback using the shortest route.",
            result.response_text,
        )
        emitted_names = [
            call["tool_name"] for batch in ws.bridge.requests for call in batch
        ]
        self.assertNotIn("search_poi_at_location", emitted_names)
        self.assertEqual(
            self._emitted(ws, "set_new_navigation"),
            {"route_ids": ["route_fallback_short"]},
        )

    def test_weather_navigation_without_route_preference_does_not_default_fastest(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_primary",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_primary",
                                    "distance_km": 120,
                                    "duration_hours": 1,
                                    "duration_minutes": 45,
                                    "alias": ["fastest", "shortest"],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_fallback_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 92,
                                    "duration_hours": 1,
                                    "duration_minutes": 10,
                                    "alias": ["fastest"],
                                },
                                {
                                    "route_id": "route_fallback_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 86,
                                    "duration_hours": 1,
                                    "duration_minutes": 15,
                                    "alias": ["shortest"],
                                },
                            ]
                        },
                    ),
                ],
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "rain"}}),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )

        result = ex.run(
            "r = navigate_by_arrival_weather(\n"
            "    primary_destination_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    avoid_conditions=['rain'],\n"
            ")\n"
            "respond(r['status'] + '|' + r['branch'] + '|' + r['segment'])"
        )

        self.assertTrue(
            result.response_text.startswith(
                "ROUTE_SELECTION_REQUIRED|fallback|fallback_destination"
            )
        )
        self.assertIn(
            "Route choice to loc_fallback is still unresolved",
            result.response_text,
        )
        self.assertIn("fastest:", result.response_text)
        self.assertIn("shortest:", result.response_text)
        self.assertNotIn("Would you like details about the route options", result.response_text)
        self.assertIsNone(self._emitted(ws, "set_new_navigation"))

    def test_poi_weather_navigation_without_route_preference_does_not_default_fastest(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_primary",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_primary",
                                    "distance_km": 120,
                                    "duration_hours": 1,
                                    "duration_minutes": 45,
                                    "alias": ["fastest", "shortest"],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_fallback_fast",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 92,
                                    "duration_hours": 1,
                                    "duration_minutes": 10,
                                    "alias": ["fastest"],
                                },
                                {
                                    "route_id": "route_fallback_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_fallback",
                                    "distance_km": 86,
                                    "duration_hours": 1,
                                    "duration_minutes": 15,
                                    "alias": ["shortest"],
                                },
                            ]
                        },
                    ),
                ],
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "rain"}}),
                "search_poi_at_location": ("SUCCESS", {"pois": []}),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {"location_id": {"type": "string"}, "category_poi": {"type": "string"}},
                    required=["location_id", "category_poi"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )

        result = ex.run(
            "r = navigate_to_poi_unless_arrival_weather(\n"
            "    primary_location_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    category_poi='charging_stations',\n"
            "    avoid_conditions=['rain'],\n"
            ")\n"
            "respond(r['status'] + '|' + r['branch'] + '|' + r['segment'])"
        )

        self.assertTrue(
            result.response_text.startswith(
                "ROUTE_SELECTION_REQUIRED|fallback|fallback_destination"
            )
        )
        self.assertIn(
            "Route choice to loc_fallback is still unresolved",
            result.response_text,
        )
        self.assertIn("fastest:", result.response_text)
        self.assertIn("shortest:", result.response_text)
        self.assertNotIn("Would you like details about the route options", result.response_text)
        emitted_names = [
            call["tool_name"] for batch in ws.bridge.requests for call in batch
        ]
        self.assertNotIn("search_poi_at_location", emitted_names)
        self.assertIsNone(self._emitted(ws, "set_new_navigation"))

    def test_navigate_to_poi_by_arrival_weather_selects_fastest_charger_when_clear(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": [
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_primary_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "loc_primary",
                                    "distance_km": 120,
                                    "duration_hours": 1,
                                    "duration_minutes": 45,
                                    "alias": ["shortest"],
                                }
                            ]
                        },
                    ),
                    (
                        "SUCCESS",
                        {
                            "routes": [
                                {
                                    "route_id": "route_fast_charger_short",
                                    "start_id": "loc_home_1",
                                    "destination_id": "poi_fast",
                                    "distance_km": 122,
                                    "duration_hours": 1,
                                    "duration_minutes": 50,
                                    "alias": ["shortest"],
                                }
                            ]
                        },
                    ),
                ],
                "get_weather": ("SUCCESS", {"current_slot": {"condition": "clear"}}),
                "search_poi_at_location": (
                    "SUCCESS",
                    {
                        "pois": [
                            {
                                "id": "poi_slow",
                                "name": "Slow Charge",
                                "category": "charging_stations",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_slow",
                                        "power_type": "DC",
                                        "power_kw": 80,
                                        "availability": "available",
                                    }
                                ],
                            },
                            {
                                "id": "poi_fast",
                                "name": "Fast Charge",
                                "category": "charging_stations",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_fast",
                                        "power_type": "DC",
                                        "power_kw": 250,
                                        "availability": "available",
                                    }
                                ],
                            },
                        ]
                    },
                ),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                    required=["location_id", "category_poi"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )

        result = ex.run(
            "r = navigate_to_poi_by_arrival_weather(\n"
            "    primary_location_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    category_poi='charging_stations',\n"
            "    avoid_conditions=['rain'],\n"
            "    poi_prefer='fastest_charging',\n"
            "    route_prefer='shortest',\n"
            ")\n"
            "respond(r['branch'] + '|' + r['chosen_destination_id'] + '|' + r['route_id'])"
        )

        self.assertTrue(
            result.response_text.startswith(
                "primary_poi|poi_fast|route_fast_charger_short"
            )
        )
        self.assertEqual(
            self._emitted(ws, "search_poi_at_location"),
            {"location_id": "loc_primary", "category_poi": "charging_stations"},
        )
        self.assertEqual(
            self._emitted(ws, "set_new_navigation"),
            {"route_ids": ["route_fast_charger_short"]},
        )
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_poi"]["poi_id"],
            "poi_fast",
        )

    def test_weather_conditioned_navigation_keeps_primary_when_clear(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_primary",
                                "start_id": "loc_home_1",
                                "destination_id": "loc_primary",
                                "distance_km": 100,
                                "duration_hours": 1,
                                "duration_minutes": 0,
                                "alias": ["fastest", "shortest"],
                            }
                        ]
                    },
                ),
                "get_weather": (
                    "SUCCESS",
                    {"current_slot": {"condition": "clear"}},
                ),
                "set_new_navigation": ("SUCCESS", {}),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
                    required=["start_id", "destination_id"],
                ),
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "number"},
                        "day": {"type": "number"},
                        "time_hour_24hformat": {"type": "number"},
                        "time_minutes": {"type": "number"},
                    },
                    required=["location_or_poi_id", "month", "day", "time_hour_24hformat"],
                ),
                "set_new_navigation": self._nav_schema(),
            },
        )
        result = ex.run(
            "r = set_navigation_conditioned_on_arrival_weather(\n"
            "    primary_destination_id='loc_primary',\n"
            "    fallback_destination_id='loc_fallback',\n"
            "    avoid_conditions=['rain'],\n"
            ")\n"
            "respond(r['branch'] + '|' + r['route_id'])"
        )
        self.assertEqual(result.response_text, "primary|route_primary")
        self.assertEqual(
            self._emitted(ws, "set_new_navigation"),
            {"route_ids": ["route_primary"]},
        )

    def test_route_preference_helper_selects_no_toll_within_threshold(self):
        ws, _ = self.make({}, {})
        ws.scratchpad["entities"]["user_preferences"] = {
            "preferences": {
                "navigation_and_routing": {
                    "route_selection": [
                        "User always wants to take the fastest route without toll roads "
                        "if it's not more than 10 minutes longer than the fastest route"
                    ]
                }
            },
            "summary": [],
        }
        routes = [
            {
                "route_id": "route_fast_toll",
                "duration_hours": 13,
                "duration_minutes": 20,
                "includes_toll": True,
                "alias": ["fastest", "first"],
            },
            {
                "route_id": "route_no_toll_close",
                "duration_hours": 13,
                "duration_minutes": 27,
                "includes_toll": False,
                "alias": ["second"],
            },
            {
                "route_id": "route_no_toll_slow",
                "duration_hours": 13,
                "duration_minutes": 38,
                "includes_toll": False,
                "alias": ["third"],
            },
        ]
        result = ws.select_route_by_user_preferences(routes)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["selected_route_id"], "route_no_toll_close")
        narration = ws.scratchpad["facts"]["pending_route_narration"]
        self.assertIsInstance(narration, dict)
        self.assertIn("preference-resolved route", narration["text"])
        self.assertIn("without toll roads", narration["text"])
        self.assertNotIn("It uses toll roads", narration["text"])
        self.assertTrue(narration["offers_alternatives"])

    def test_route_preference_helper_keeps_fastest_when_no_toll_exceeds_threshold(self):
        ws, _ = self.make({}, {})
        result = ws.select_route_by_user_preferences(
            [
                {
                    "route_id": "route_fast_toll",
                    "duration_hours": 1,
                    "duration_minutes": 0,
                    "includes_toll": True,
                    "alias": ["fastest"],
                },
                {
                    "route_id": "route_no_toll_slow",
                    "duration_hours": 1,
                    "duration_minutes": 15,
                    "includes_toll": False,
                    "alias": ["second"],
                },
            ],
            preference_text=(
                "fastest route without toll roads if not more than 10 minutes longer than fastest"
            ),
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["selected_route_id"], "route_fast_toll")
        self.assertNotIn("without toll roads", ws.scratchpad["facts"]["pending_route_narration"])

    def test_route_preference_helper_does_not_guess_between_multiple_no_toll_routes(self):
        ws, _ = self.make({}, {})
        result = ws.select_route_by_user_preferences(
            [
                {
                    "route_id": "route_toll",
                    "duration_hours": 1,
                    "duration_minutes": 0,
                    "includes_toll": True,
                },
                {
                    "route_id": "route_no_toll_1",
                    "duration_hours": 1,
                    "duration_minutes": 5,
                    "includes_toll": False,
                },
                {
                    "route_id": "route_no_toll_2",
                    "duration_hours": 1,
                    "duration_minutes": 6,
                    "includes_toll": False,
                },
            ],
            preference_text="avoid toll roads",
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(len(result["matches"]), 2)

    def test_open_at_arrival_poi_helper_selects_unique_open_poi(self):
        ws, ex = self.make(
            {
                "search_poi_at_location": (
                    "SUCCESS",
                    {
                        "pois": [
                            {
                                "id": "poi_sup_closed",
                                "name": "Tesco",
                                "category": "supermarkets",
                                "opening_hours": "08:00h - 18:00h",
                            },
                            {
                                "id": "poi_sup_open",
                                "name": "Billa",
                                "category": "supermarkets",
                                "opening_hours": "06:00h - 21:00h",
                            },
                        ]
                    },
                ),
            },
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "filters": {"type": "array", "items": {"type": "string"}},
                    },
                    required=["location_id", "category_poi"],
                ),
            },
            policy=(
                'CURRENT_LOCATION = {"id": "loc_home_1", "name": "Munich"}\n'
                'DATETIME = {"year": 2025, "month": 6, "day": 6, "hour": 5, "minute": 30}\n'
            ),
        )
        result = ex.run(
            "route = {\n"
            "    'route_id': 'route_to_city',\n"
            "    'start_id': 'loc_home_1',\n"
            "    'destination_id': 'loc_city',\n"
            "    'duration_hours': 13,\n"
            "    'duration_minutes': 27,\n"
            "}\n"
            "poi = select_poi_at_location_open_at_route_arrival(\n"
            "    location_id='loc_city',\n"
            "    category_poi='supermarkets',\n"
            "    route=route,\n"
            ")\n"
            "respond(poi['status'] + '|' + poi['poi_id'] + '|' + poi['arrival']['time_label'])"
        )
        self.assertEqual(result.response_text, "SUCCESS|poi_sup_open|18:57")
        args = self._emitted(ws, "search_poi_at_location")
        self.assertEqual(args["location_id"], "loc_city")
        self.assertEqual(args["category_poi"], "supermarkets")
        self.assertNotIn("filters", args)
        self.assertEqual(
            ws.scratchpad["entities"]["selected_poi"]["poi_id"],
            "poi_sup_open",
        )

    def test_open_at_arrival_poi_helper_reports_ambiguous_open_pois(self):
        ws, ex = self.make(
            {
                "search_poi_at_location": (
                    "SUCCESS",
                    {
                        "pois": [
                            {
                                "id": "poi_sup_1",
                                "name": "Billa",
                                "opening_hours": "06:00h - 21:00h",
                            },
                            {
                                "id": "poi_sup_2",
                                "name": "Aldi",
                                "opening_hours": "08:00h - 22:00h",
                            },
                        ]
                    },
                ),
            },
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {"location_id": {"type": "string"}, "category_poi": {"type": "string"}},
                    required=["location_id", "category_poi"],
                ),
            },
            policy=(
                'CURRENT_LOCATION = {"id": "loc_home_1", "name": "Munich"}\n'
                'DATETIME = {"year": 2025, "month": 6, "day": 6, "hour": 5, "minute": 30}\n'
            ),
        )
        result = ex.run(
            "poi = select_poi_at_location_open_at_route_arrival(\n"
            "    location_id='loc_city',\n"
            "    category_poi='supermarkets',\n"
            "    route={'route_id': 'r1', 'duration_hours': 13, 'duration_minutes': 27},\n"
            ")\n"
            "respond(poi['status'] + '|' + str(len(poi['open_pois'])))"
        )
        self.assertEqual(result.response_text, "AMBIGUOUS|2")
        self.assertNotIn("selected_poi", ws.scratchpad["entities"])

    def test_open_at_arrival_poi_helper_reports_none_open(self):
        ws, ex = self.make(
            {
                "search_poi_at_location": (
                    "SUCCESS",
                    {
                        "pois": [
                            {
                                "id": "poi_sup_1",
                                "name": "Tesco",
                                "opening_hours": "08:00h - 18:00h",
                            },
                            {
                                "id": "poi_sup_2",
                                "name": "Local Market",
                                "opening_hours": "07:00h - 12:00h",
                            },
                        ]
                    },
                ),
            },
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {"location_id": {"type": "string"}, "category_poi": {"type": "string"}},
                    required=["location_id", "category_poi"],
                ),
            },
            policy=(
                'CURRENT_LOCATION = {"id": "loc_home_1", "name": "Munich"}\n'
                'DATETIME = {"year": 2025, "month": 6, "day": 6, "hour": 5, "minute": 30}\n'
            ),
        )
        result = ex.run(
            "poi = select_poi_at_location_open_at_route_arrival(\n"
            "    location_id='loc_city',\n"
            "    category_poi='supermarkets',\n"
            "    route={'route_id': 'r1', 'duration_hours': 13, 'duration_minutes': 27},\n"
            ")\n"
            "respond(poi['status'] + '|' + str(len(poi['closed_pois'])))"
        )
        self.assertEqual(result.response_text, "NOT_FOUND|2")

    def test_opening_hours_parser_handles_overnight_windows(self):
        self.assertTrue(
            CoroutineWorkspace._poi_open_status_at_minutes("22:00h - 02:00h", 23 * 60 + 30)
        )
        self.assertTrue(
            CoroutineWorkspace._poi_open_status_at_minutes("22:00h - 02:00h", 60)
        )
        self.assertFalse(
            CoroutineWorkspace._poi_open_status_at_minutes("22:00h - 02:00h", 3 * 60)
        )

    def test_select_charging_plug_prefers_highest_power_even_if_occupied(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "pois = [\n"
            "    {'id': 'poi_slow', 'name': 'SlowCo', 'phone_number': '+1', 'charging_plugs': [\n"
            "        {'plug_id': 'plug_22', 'power_type': 'AC', 'power_kw': 22, 'availability': 'available'}]},\n"
            "    {'id': 'poi_fast', 'name': 'FastCo', 'phone_number': '+2', 'charging_plugs': [\n"
            "        {'plug_id': 'plug_300', 'power_type': 'DC', 'power_kw': 300, 'availability': 'occupied'},\n"
            "        {'plug_id': 'plug_50', 'power_type': 'DC', 'power_kw': 50, 'availability': 'available'}]},\n"
            "]\n"
            "plug = select_charging_plug(pois)\n"
            "respond(plug['charging_station_id'] + '|' + plug['charging_station_plug_id'])"
        )
        self.assertEqual(result.response_text, "poi_fast|plug_300")

    def test_select_charging_plug_exposes_common_station_and_power_aliases(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "pois = [\n"
            "    {'id': 'poi_fast', 'name': 'FastCo', 'phone_number': '+2', 'charging_plugs': [\n"
            "        {'plug_id': 'plug_300', 'power_type': 'DC', 'power_kw': 300, 'availability': 'available'}]},\n"
            "]\n"
            "plug = select_charging_plug(pois)\n"
            "respond('|'.join([plug['name'], str(plug['power']), plug['selected']['name'], str(plug['selected']['power'])]))"
        )
        self.assertEqual(result.response_text, "FastCo|300|FastCo|300")

    def test_charging_calculation_repairs_plug_id_to_requested_station(self):
        ws, ex = self.make(
            {
                "calculate_charging_time_by_soc": (
                    "SUCCESS",
                    {"time_from_35.0_until_95.0_percent_soc": "28min"},
                )
            },
            {
                "calculate_charging_time_by_soc": tool_schema(
                    "calculate_charging_time_by_soc",
                    {
                        "charging_station_id": {"type": "string"},
                        "charging_station_plug_id": {"type": "string"},
                        "start_state_of_charge": {"type": "number"},
                        "target_state_of_charge": {"type": "number"},
                    },
                    required=[
                        "charging_station_id",
                        "charging_station_plug_id",
                        "start_state_of_charge",
                        "target_state_of_charge",
                    ],
                )
            },
        )
        ws.scratchpad["entities"]["pois_by_id"] = {
            "poi_ionity": {
                "poi_id": "poi_ionity",
                "navigation_id": "poi_ionity",
                "name": "Ionity",
                "category": "charging_stations",
                "charging_plugs": [
                    {
                        "plug_id": "plug_ionity_ac",
                        "power_type": "AC",
                        "power_kw": 11,
                        "availability": "available",
                    },
                    {
                        "plug_id": "plug_ionity_dc",
                        "power_type": "DC",
                        "power_kw": 100,
                        "availability": "available",
                    },
                ],
            },
            "poi_tesla": {
                "poi_id": "poi_tesla",
                "navigation_id": "poi_tesla",
                "name": "Tesla Supercharger",
                "category": "charging_stations",
                "charging_plugs": [
                    {
                        "plug_id": "plug_tesla_dc",
                        "power_type": "DC",
                        "power_kw": 350,
                        "availability": "occupied",
                    },
                ],
            },
        }

        result = ex.run(
            "calculate_charging_time_by_soc("
            "charging_station_id='poi_ionity', "
            "charging_station_plug_id='plug_tesla_dc', "
            "start_state_of_charge=35, "
            "target_state_of_charge=95)\n"
            "respond('done')"
        )

        self.assertIsNone(result.error)
        self.assertEqual(
            ws.bridge.requests[0][0]["arguments"]["charging_station_plug_id"],
            "plug_ionity_dc",
        )
        self.assertEqual(
            ws.scratchpad["gates"]["charging_station_plug_pair_guard"]["status"],
            "REPAIRED",
        )

    def test_select_charging_plug_can_require_available(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "pois = [{'id': 'poi_fast', 'name': 'FastCo', 'charging_plugs': [\n"
            "    {'plug_id': 'plug_300', 'power_kw': 300, 'availability': 'occupied'},\n"
            "    {'plug_id': 'plug_50', 'power_kw': 50, 'availability': 'available'}]}]\n"
            "plug = select_charging_plug(pois, require_available=True)\n"
            "respond(plug['charging_station_plug_id'])"
        )
        self.assertEqual(result.response_text, "plug_50")

    def test_direct_poi_tool_result_exposes_navigation_id_and_plug_ids(self):
        ws, ex = self.make(
            {
                "search_poi_at_location": (
                    "SUCCESS",
                    {
                        "pois_found": [
                            {
                                "id": "poi_ionity",
                                "name": "Ionity",
                                "corresponding_location_id": "loc_war",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_dc",
                                        "power_kw": 100,
                                        "availability": "available",
                                    }
                                ],
                            }
                        ]
                    },
                )
            },
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                    required=["location_id", "category_poi"],
                ),
            },
        )
        result = ex.run(
            "pois = pois_value(search_poi_at_location(location_id='loc_war', category_poi='charging_stations'))\n"
            "poi = pois[0]\n"
            "respond('|'.join([poi['navigation_id'], poi['poi_id'], poi['plug_ids'][0], poi['available_plug_ids'][0]]))"
        )
        self.assertEqual(result.response_text, "poi_ionity|poi_ionity|plug_dc|plug_dc")

    def test_single_required_argument_wrapper_accepts_positional_value(self):
        ws, ex = self.make(
            {"call_phone_by_number": ("SUCCESS", {"phone_number_called": True})},
            {
                "call_phone_by_number": tool_schema(
                    "call_phone_by_number",
                    {"phone_number": {"type": "string"}},
                    required=["phone_number"],
                ),
            },
        )
        ex.run("call_phone_by_number('+49 123 456')")
        self.assertEqual(
            ws.bridge.requests[0][0]["arguments"]["phone_number"],
            "+49 123 456",
        )

    def test_set_new_navigation_returns_structured_block_when_active(self):
        tools = {
            "set_new_navigation": tool_schema(
                "set_new_navigation", {"route_ids": {"type": "array", "items": {"type": "string"}}}),
            "navigation_replace_final_destination": tool_schema(
                "navigation_replace_final_destination",
                {"new_destination_id": {"type": "string"},
                 "route_id_leading_to_new_destination": {"type": "string"}}),
        }
        ws, ex = self.make({}, tools)
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": True}
        # Model fetched routes to the new destination earlier (auto-persisted),
        # so the FACT-only candidate destination can be derived for the block.
        ws.scratchpad["entities"]["last_routes"] = [
            {"route_id": "R_new", "destination_id": "loc_dest", "alias": ["fastest", "shortest"]}]
        result = ex.run(
            "r = set_new_navigation(route_ids=['R_new'])\n"
            "respond(r['status'] + '|' + str(r['candidate_destination_id']))"
        )
        # The runtime hands back facts; it does NOT choose or emit the edit.
        self.assertEqual(result.response_text, "NEEDS_ACTIVE_ROUTE_EDIT|loc_dest")
        self.assertIsNone(self._emitted(ws, "navigation_replace_final_destination"))
        self.assertIsNone(self._emitted(ws, "set_new_navigation"))

    def test_navigation_read_does_not_interrupt_python(self):
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_start", "loc_destination"],
                        "routes_to_final_destination_id": ["route_current"],
                        "details": {
                            "waypoints": [
                                {"id": "loc_start"},
                                {"id": "loc_destination"},
                            ],
                            "routes": [{"route_id": "route_current"}],
                        },
                    },
                ),
                "set_fan_speed": ("SUCCESS", {}),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "set_fan_speed": tool_schema(
                    "set_fan_speed",
                    {"level": {"type": "integer"}},
                ),
            },
        )

        result = ex.run(
            "navigation = get_navigation_state(detailed_information=True)\n"
            "set_fan_speed(level=3)"
        )
        self.assertIsNone(result.error)
        self.assertEqual(
            [call["tool_name"] for request in ws.bridge.requests for call in request],
            ["get_current_navigation_state", "set_fan_speed"],
        )
        self.assertEqual(
            ws.scratchpad["entities"]["navigation_state"]["waypoint_count"],
            2,
        )

    def test_navigation_preflight_populates_and_reuses_state(self):
        ws, _ = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_start", "loc_mid", "loc_destination"],
                        "routes_to_final_destination_id": ["route_1", "route_2"],
                        "details": {
                            "waypoints": [
                                {"id": "loc_start"},
                                {"id": "loc_mid"},
                                {"id": "loc_destination"},
                            ],
                            "routes": [
                                {"route_id": "route_1"},
                                {"route_id": "route_2"},
                            ],
                        },
                    },
                ),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
            },
        )

        first = ws.preflight_navigation_state()
        second = ws.preflight_navigation_state()
        self.assertEqual(first["status"], "SUCCESS")
        self.assertEqual(second["status"], "CACHED")
        self.assertIs(first["navigation_state"]["is_multi_stop"], True)
        self.assertEqual(
            [call["tool_name"] for request in ws.bridge.requests for call in request],
            ["get_current_navigation_state"],
        )

    def test_new_user_turn_refreshes_preflight_navigation_state(self):
        states = [
            {
                "navigation_active": True,
                "waypoints_id": ["loc_start", "loc_old"],
                "routes_to_final_destination_id": ["route_old"],
                "details": {
                    "waypoints": [{"id": "loc_start"}, {"id": "loc_old"}],
                    "routes": [{"route_id": "route_old"}],
                },
            },
            {
                "navigation_active": True,
                "waypoints_id": ["loc_start", "loc_new"],
                "routes_to_final_destination_id": ["route_new"],
                "details": {
                    "waypoints": [{"id": "loc_start"}, {"id": "loc_new"}],
                    "routes": [{"route_id": "route_new"}],
                },
            },
        ]
        ws, _ = self.make(
            {
                "get_current_navigation_state": ("SUCCESS", states[0]),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
            },
        )

        ws.observe_user("First request")
        first = ws.preflight_navigation_state()
        ws.bridge.responses["get_current_navigation_state"] = ("SUCCESS", states[1])
        ws.observe_user("Follow-up request")
        second = ws.preflight_navigation_state()
        cached = ws.preflight_navigation_state()

        self.assertEqual(first["navigation_state"]["destination_id"], "loc_old")
        self.assertEqual(second["navigation_state"]["destination_id"], "loc_new")
        self.assertEqual(cached["status"], "CACHED")
        self.assertEqual(
            [call["tool_name"] for request in ws.bridge.requests for call in request],
            ["get_current_navigation_state", "get_current_navigation_state"],
        )

    def test_preflight_user_preferences_populates_and_reuses_state(self):
        preferences = {
            "navigation_and_routing": {
                "route_selection": [
                    "The user always wants the fastest route that does not include toll roads."
                ]
            },
            "vehicle_settings": {
                "climate_control": ["The user prefers airflow toward FEET."],
                "vehicle_settings": [],
            },
        }
        ws, _ = self.make(
            {"get_user_preferences": ("SUCCESS", preferences)},
            {
                "get_user_preferences": tool_schema(
                    "get_user_preferences",
                    {
                        "preference_categories": {
                            "type": "object",
                            "properties": {
                                "navigation_and_routing": {
                                    "type": "object",
                                    "properties": {
                                        "route_selection": {"type": "boolean"},
                                    },
                                },
                                "vehicle_settings": {
                                    "type": "object",
                                    "properties": {
                                        "climate_control": {"type": "boolean"},
                                        "vehicle_settings": {"type": "boolean"},
                                    },
                                },
                            },
                        }
                    },
                    required=["preference_categories"],
                ),
            },
        )

        first = ws.preflight_user_preferences()
        second = ws.preflight_user_preferences()

        self.assertEqual(first["status"], "SUCCESS")
        self.assertEqual(second["status"], "CACHED")
        stored = ws.scratchpad["entities"]["user_preferences"]
        self.assertEqual(stored["preferences"], preferences)
        self.assertEqual(
            stored["summary"],
            [
                (
                    "navigation_and_routing.route_selection: "
                    "The user always wants the fastest route that does not include toll roads."
                ),
                "vehicle_settings.climate_control: The user prefers airflow toward FEET.",
            ],
        )
        self.assertEqual(
            [call["tool_name"] for request in ws.bridge.requests for call in request],
            ["get_user_preferences"],
        )

    def test_preflight_user_preferences_skips_when_tool_unavailable(self):
        ws, _ = self.make({}, {})

        result = ws.preflight_user_preferences()

        self.assertEqual(result["status"], "SKIPPED")
        self.assertNotIn("user_preferences", ws.scratchpad["entities"])
        self.assertEqual(ws.bridge.requests, [])

    def test_preflight_user_preferences_uses_live_schema_leaves_only(self):
        preferences = {
            "vehicle_settings": {
                "vehicle_settings": ["The user prefers BLUE ambient lighting."],
            },
        }
        ws, _ = self.make(
            {"get_user_preferences": ("SUCCESS", preferences)},
            {
                "get_user_preferences": tool_schema(
                    "get_user_preferences",
                    {
                        "preference_categories": {
                            "type": "object",
                            "properties": {
                                "vehicle_settings": {
                                    "type": "object",
                                    "properties": {
                                        "vehicle_settings": {"type": "boolean"},
                                    },
                                },
                            },
                        }
                    },
                    required=["preference_categories"],
                ),
            },
        )

        result = ws.preflight_user_preferences()

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(
            ws.bridge.requests[0][0]["arguments"],
            {
                "preference_categories": {
                    "vehicle_settings": {"vehicle_settings": True}
                }
            },
        )

    def test_question_mark_placeholder_id_is_blocked_before_evaluator(self):
        ws, ex = self.make(
            {},
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                    required=["start_id", "destination_id"],
                ),
            },
        )

        result = ex.run(
            "get_routes_from_start_to_destination("
            "start_id='loc_start', destination_id='loc_stg_???')"
        )
        self.assertEqual(result.error["type"], "ValueError")
        self.assertIn("placeholder/ungrounded", result.error["message"])
        self.assertEqual(ws.bridge.requests, [])

    def test_set_new_navigation_allowed_when_inactive(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ex.run("set_new_navigation(route_ids=['route_9'])\nrespond('Navigation started.')")
        self.assertEqual(len(ws.bridge.requests), 1)

    def test_set_new_navigation_repairs_stale_second_leg_with_connected_route(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_warsaw_charger": {
                "route_id": "R_warsaw_charger",
                "start_id": "loc_warsaw",
                "destination_id": "poi_charger",
                "alias": ["fastest"],
            },
            "R_warsaw_hamburg": {
                "route_id": "R_warsaw_hamburg",
                "start_id": "loc_warsaw",
                "destination_id": "loc_hamburg",
                "alias": ["fastest"],
            },
            "R_charger_hamburg": {
                "route_id": "R_charger_hamburg",
                "base_route_id": "R_warsaw_hamburg",
                "start_id": "poi_charger",
                "destination_id": "loc_hamburg",
                "alias": ["fastest"],
            },
        }
        ex.run(
            "set_new_navigation(route_ids=['R_warsaw_charger', 'R_warsaw_hamburg'])"
        )
        args = self._emitted(ws, "set_new_navigation")
        self.assertEqual(
            args["route_ids"],
            ["R_warsaw_charger", "R_charger_hamburg"],
        )
        self.assertEqual(
            ws.scratchpad["gates"]["route_chain_guard"]["status"],
            "REPAIRED",
        )

    def test_set_new_navigation_repairs_recorded_second_route_selection(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_stop": {
                "route_id": "R_start_stop",
                "start_id": "loc_start",
                "destination_id": "poi_stop",
                "alias": ["fastest", "first"],
            },
            "R_stop_dest_fast": {
                "route_id": "R_stop_dest_fast",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "alias": ["fastest", "first"],
                "name_via": "A1",
            },
            "R_stop_dest_second": {
                "route_id": "R_stop_dest_second",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "alias": ["second"],
                "name_via": "B432, B132",
            },
        }
        ws.scratchpad["entities"]["route_selection_history"] = [
            {
                "route_id": "R_old_dest_second",
                "selected_route_id": "R_old_dest_second",
                "destination_id": "loc_dest",
                "selector": {"alias": "second"},
                "route": {
                    "route_id": "R_old_dest_second",
                    "destination_id": "loc_dest",
                    "alias": ["second"],
                    "name_via": "B432, B132",
                },
            },
        ]
        ex.run("set_new_navigation(route_ids=['R_start_stop', 'R_stop_dest_fast'])")
        args = self._emitted(ws, "set_new_navigation")
        self.assertEqual(args["route_ids"], ["R_start_stop", "R_stop_dest_second"])
        self.assertEqual(
            ws.scratchpad["gates"]["route_selection_guard"]["status"],
            "REPAIRED",
        )

    def test_set_new_navigation_repairs_single_leg_recorded_selection(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_dest_fast": {
                "route_id": "R_dest_fast",
                "start_id": "loc_start",
                "destination_id": "loc_dest",
                "alias": ["fastest", "first"],
                "name_via": "A1",
            },
            "R_dest_short": {
                "route_id": "R_dest_short",
                "start_id": "loc_start",
                "destination_id": "loc_dest",
                "alias": ["shortest", "second"],
                "name_via": "B2",
            },
        }
        ws.scratchpad["entities"]["route_selection_history"] = [
            {
                "route_id": "R_dest_short",
                "selected_route_id": "R_dest_short",
                "destination_id": "loc_dest",
                "selector": {"alias": "shortest"},
                "route": {
                    "route_id": "R_dest_short",
                    "destination_id": "loc_dest",
                    "alias": ["shortest", "second"],
                    "name_via": "B2",
                },
            }
        ]
        ex.run("set_new_navigation(route_ids=['R_dest_fast'])")
        args = self._emitted(ws, "set_new_navigation")
        self.assertEqual(args["route_ids"], ["R_dest_short"])
        self.assertEqual(
            ws.scratchpad["gates"]["route_selection_guard"]["status"],
            "REPAIRED",
        )

    def test_set_new_navigation_latest_recorded_selection_wins(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_stop": {
                "route_id": "R_start_stop",
                "start_id": "loc_start",
                "destination_id": "poi_stop",
            },
            "R_stop_dest_fast": {
                "route_id": "R_stop_dest_fast",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "alias": ["fastest", "first"],
            },
            "R_stop_dest_second": {
                "route_id": "R_stop_dest_second",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "alias": ["second"],
            },
        }
        ws.scratchpad["entities"]["route_selection_history"] = [
            {
                "destination_id": "loc_dest",
                "selector": {"alias": "second"},
                "route": {"alias": ["second"], "destination_id": "loc_dest"},
            },
            {
                "destination_id": "loc_dest",
                "selector": {"prefer": "fastest"},
                "route": {"alias": ["fastest", "first"], "destination_id": "loc_dest"},
            },
        ]
        ex.run("set_new_navigation(route_ids=['R_start_stop', 'R_stop_dest_fast'])")
        args = self._emitted(ws, "set_new_navigation")
        self.assertEqual(args["route_ids"], ["R_start_stop", "R_stop_dest_fast"])
        self.assertNotIn("route_selection_guard", ws.scratchpad["gates"])

    def test_set_new_navigation_repairs_current_request_via_roads(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.observe_user(
            "Set navigation via the charging stop, and for the final leg use the route via B432, B132."
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_stop": {
                "route_id": "R_start_stop",
                "start_id": "loc_start",
                "destination_id": "poi_stop",
            },
            "R_stop_dest_fast": {
                "route_id": "R_stop_dest_fast",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "alias": ["fastest", "first"],
                "name_via": "A1",
            },
            "R_stop_dest_via": {
                "route_id": "R_stop_dest_via",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "alias": ["second"],
                "name_via": "B432, B132",
            },
        }

        ex.run("set_new_navigation(route_ids=['R_start_stop', 'R_stop_dest_fast'])")

        self.assertEqual(
            self._emitted(ws, "set_new_navigation")["route_ids"],
            ["R_start_stop", "R_stop_dest_via"],
        )
        self.assertEqual(
            ws.scratchpad["gates"]["route_via_request_guard"]["status"],
            "REPAIRED",
        )

    def test_set_new_navigation_does_not_reuse_via_roads_after_bare_continue(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.observe_user("For the final leg, use the route via B432, B132.")
        ws.observe_user("CONTINUE")
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_stop": {
                "route_id": "R_start_stop",
                "start_id": "loc_start",
                "destination_id": "poi_stop",
            },
            "R_stop_dest_fast": {
                "route_id": "R_stop_dest_fast",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "name_via": "A1",
            },
            "R_stop_dest_via": {
                "route_id": "R_stop_dest_via",
                "start_id": "poi_stop",
                "destination_id": "loc_dest",
                "name_via": "B432, B132",
            },
        }

        ex.run("set_new_navigation(route_ids=['R_start_stop', 'R_stop_dest_fast'])")

        self.assertEqual(
            self._emitted(ws, "set_new_navigation")["route_ids"],
            ["R_start_stop", "R_stop_dest_fast"],
        )
        self.assertNotIn("route_via_request_guard", ws.scratchpad["gates"])

    def test_final_destination_replacement_preserves_concrete_route_id(self):
        routes = [
            {
                "route_id": "R_fast",
                "start_id": "loc_start",
                "destination_id": "loc_dest",
                "alias": ["fastest", "shortest"],
                "name_via": "A1",
            },
            {
                "route_id": "R_second",
                "start_id": "loc_start",
                "destination_id": "loc_dest",
                "alias": ["second"],
                "name_via": "K816, A46",
            },
        ]
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {"waypoints_id": ["loc_start", "loc_old_dest"]},
                ),
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": routes},
                ),
                "navigation_replace_final_destination": ("SUCCESS", {}),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
                "navigation_replace_final_destination": tool_schema(
                    "navigation_replace_final_destination",
                    {
                        "new_destination_id": {"type": "string"},
                        "route_id_leading_to_new_destination": {"type": "string"},
                    },
                ),
            },
        )
        ws.observe_user("Change my final destination to Munich.")
        ws.scratchpad["entities"]["navigation_state"] = {
            "navigation_active": True,
            "waypoint_order": ["loc_start", "loc_old_dest"],
        }

        result = ex.run(
            "navigation_replace_final_destination("
            "new_destination_id='loc_dest', "
            "route_id_leading_to_new_destination='R_fast')"
        )

        self.assertIsNone(result.response_text)
        self.assertEqual(
            self._emitted(ws, "navigation_replace_final_destination"),
            {
                "new_destination_id": "loc_dest",
                "route_id_leading_to_new_destination": "R_fast",
            },
        )

    def test_final_destination_replacement_allows_explicit_fastest(self):
        routes = [
            {
                "route_id": "R_fast",
                "start_id": "loc_start",
                "destination_id": "loc_dest",
                "alias": ["fastest", "shortest"],
            },
            {
                "route_id": "R_second",
                "start_id": "loc_start",
                "destination_id": "loc_dest",
                "alias": ["second"],
            },
        ]
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {"waypoints_id": ["loc_start", "loc_old_dest"]},
                ),
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": routes},
                ),
                "navigation_replace_final_destination": ("SUCCESS", {}),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
                "navigation_replace_final_destination": tool_schema(
                    "navigation_replace_final_destination",
                    {
                        "new_destination_id": {"type": "string"},
                        "route_id_leading_to_new_destination": {"type": "string"},
                    },
                ),
            },
        )
        ws.observe_user("Change my final destination to Munich using the fastest route.")
        ws.scratchpad["entities"]["navigation_state"] = {
            "navigation_active": True,
            "waypoint_order": ["loc_start", "loc_old_dest"],
        }

        ex.run(
            "navigation_replace_final_destination("
            "new_destination_id='loc_dest', "
            "route_id_leading_to_new_destination='R_fast')"
        )

        self.assertEqual(
            self._emitted(ws, "navigation_replace_final_destination"),
            {
                "new_destination_id": "loc_dest",
                "route_id_leading_to_new_destination": "R_fast",
            },
        )

    def test_set_new_navigation_blocks_known_unconnected_route_chain(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_ab": {
                "route_id": "R_ab",
                "start_id": "loc_a",
                "destination_id": "loc_b",
            },
            "R_cd": {
                "route_id": "R_cd",
                "start_id": "loc_c",
                "destination_id": "loc_d",
            },
        }
        result = ex.run(
            "r = set_new_navigation(route_ids=['R_ab', 'R_cd'])\n"
            "respond(r['status'])"
        )
        self.assertEqual(result.response_text, "ROUTE_CHAIN_MISMATCH")
        self.assertIsNone(self._emitted(ws, "set_new_navigation"))

    def test_set_new_navigation_blocks_charging_plan_route_mismatch(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["selected_charging_plan"] = {
            "charging_station_id": "poi_cha_fastned",
            "charging_station_plug_id": "plug_fast",
            "phone_number": "+49 110",
        }
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_enbw": {
                "route_id": "R_start_enbw",
                "start_id": "loc_start",
                "destination_id": "poi_cha_enbw",
            },
            "R_start_fastned": {
                "route_id": "R_start_fastned",
                "start_id": "loc_start",
                "destination_id": "poi_cha_fastned",
            },
            "R_enbw_dest": {
                "route_id": "R_enbw_dest",
                "start_id": "poi_cha_enbw",
                "destination_id": "loc_dest",
            },
        }
        result = ex.run(
            "r = set_new_navigation(route_ids=['R_start_enbw', 'R_enbw_dest'])\n"
            "respond(r['status'])"
        )
        self.assertEqual(result.response_text, "CHARGING_PLAN_ROUTE_MISMATCH")
        self.assertIsNone(self._emitted(ws, "set_new_navigation"))

    def test_set_new_navigation_repairs_charging_plan_route_when_chain_known(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["selected_charging_plan"] = {
            "charging_station_id": "poi_cha_fastned",
            "charging_station_plug_id": "plug_fast",
        }
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_enbw": {
                "route_id": "R_start_enbw",
                "start_id": "loc_start",
                "destination_id": "poi_cha_enbw",
            },
            "R_start_fastned": {
                "route_id": "R_start_fastned",
                "start_id": "loc_start",
                "destination_id": "poi_cha_fastned",
            },
            "R_enbw_dest": {
                "route_id": "R_enbw_dest",
                "start_id": "poi_cha_enbw",
                "destination_id": "loc_dest",
            },
            "R_fastned_dest": {
                "route_id": "R_fastned_dest",
                "start_id": "poi_cha_fastned",
                "destination_id": "loc_dest",
            },
        }
        ex.run("set_new_navigation(route_ids=['R_start_enbw', 'R_enbw_dest'])")
        args = self._emitted(ws, "set_new_navigation")
        self.assertEqual(args["route_ids"], ["R_start_fastned", "R_fastned_dest"])

    def test_set_new_navigation_does_not_repair_direct_route_from_user_text(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["selected_charging_plan"] = {
            "charging_station_id": "poi_cha_fastned",
            "meeting_location_id": "loc_meeting",
            "navigation_route_ids": ["R_start_fastned", "R_fastned_meeting"],
        }
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_meeting": {
                "route_id": "R_start_meeting",
                "start_id": "loc_start",
                "destination_id": "loc_meeting",
            },
            "R_start_fastned": {
                "route_id": "R_start_fastned",
                "start_id": "loc_start",
                "destination_id": "poi_cha_fastned",
            },
            "R_fastned_meeting": {
                "route_id": "R_fastned_meeting",
                "start_id": "poi_cha_fastned",
                "destination_id": "loc_meeting",
            },
        }

        ex.run("set_new_navigation(route_ids=['R_start_meeting'])")

        self.assertEqual(
            self._emitted(ws, "set_new_navigation")["route_ids"],
            ["R_start_meeting"],
        )
        self.assertNotIn("charging_plan_route_guard", ws.scratchpad["gates"])

    def test_set_new_navigation_keeps_direct_route_when_user_skips_charging(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.observe_user("Navigate directly to the meeting and skip charging.")
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ws.scratchpad["entities"]["selected_charging_plan"] = {
            "charging_station_id": "poi_cha_fastned",
            "meeting_location_id": "loc_meeting",
            "navigation_route_ids": ["R_start_fastned", "R_fastned_meeting"],
        }
        ws.scratchpad["entities"]["routes_by_id"] = {
            "R_start_meeting": {
                "route_id": "R_start_meeting",
                "start_id": "loc_start",
                "destination_id": "loc_meeting",
            },
            "R_start_fastned": {
                "route_id": "R_start_fastned",
                "start_id": "loc_start",
                "destination_id": "poi_cha_fastned",
            },
            "R_fastned_meeting": {
                "route_id": "R_fastned_meeting",
                "start_id": "poi_cha_fastned",
                "destination_id": "loc_meeting",
            },
        }

        ex.run("set_new_navigation(route_ids=['R_start_meeting'])")

        self.assertEqual(
            self._emitted(ws, "set_new_navigation")["route_ids"],
            ["R_start_meeting"],
        )
        self.assertNotIn("charging_plan_route_guard", ws.scratchpad["gates"])

    def test_charging_location_search_after_route_edit_repairs_to_route_search(self):
        ws, ex = self.make(
            {
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {"pois_found": [{"id": "poi_cha_1"}]},
                ),
            },
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                        "filters": {"type": "array"},
                    },
                ),
            },
        )
        ws.observe_user("Remove the waypoint and find a charging station along the way.")
        ws.remember_entity(
            "last_route_edit_followup_route",
            {
                "turn": ws.last_user_message,
                "source_tool": "navigation_delete_waypoint",
                "route_id": "R_direct",
            },
        )
        ws.remember_entity(
            "last_charging_specs_and_status",
            {"remaining_range": "323.0km", "remaining_range_km": 323.0},
        )

        ex.run(
            "search_poi_at_location("
            "location_id='loc_destination', category_poi='charging_stations')"
        )

        self.assertIsNone(self._emitted(ws, "search_poi_at_location"))
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route"),
            {
                "route_id": "R_direct",
                "category_poi": "charging_stations",
                "at_kilometer": 300,
                "filters": ["charging_stations::has_available_plug"],
            },
        )
        self.assertEqual(
            ws.scratchpad["gates"]["charging_location_search_guard"]["status"],
            "REPAIRED_TO_ROUTE_SEARCH",
        )

    def test_charging_location_search_followup_does_not_repair_from_user_text(self):
        ws, ex = self.make(
            {
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {"pois_found": [{"id": "poi_cha_1"}]},
                ),
            },
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )
        ws.observe_user("Remove the waypoint from my route.")
        ws.remember_entity(
            "last_route_edit_followup_route",
            {
                "turn": ws.last_user_message,
                "source_tool": "navigation_delete_waypoint",
                "route_id": "R_direct",
            },
        )
        ws.remember_entity(
            "last_charging_specs_and_status",
            {"remaining_range": "323.0km", "remaining_range_km": 323.0},
        )
        ws.observe_user("Now let's look into the charging situation for this trip.")

        ex.run(
            "search_poi_at_location("
            "location_id='loc_destination', category_poi='charging_stations')"
        )

        self.assertIsNone(self._emitted(ws, "search_poi_along_the_route"))
        self.assertEqual(
            self._emitted(ws, "search_poi_at_location"),
            {"location_id": "loc_destination", "category_poi": "charging_stations"},
        )

    def test_later_segment_charging_search_converts_global_soc_distance_to_segment_km(self):
        ws, ex = self.make(
            {
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {"pois_found_along_route": []},
                ),
            },
            {
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )
        ws.remember_entity(
            "navigation_state",
            {"route_ids": ["route_to_stop", "route_after_stop"]},
        )
        ws.scratchpad["entities"]["routes_by_id"] = {
            "route_to_stop": {
                "route_id": "route_to_stop",
                "distance_km": 336.3,
            },
            "route_after_stop": {
                "route_id": "route_after_stop",
                "distance_km": 1257.8,
            },
        }
        ws.remember_entity(
            "last_distance_by_soc",
            {
                "initial_state_of_charge": 98,
                "final_state_of_charge": 15,
                "distance_km": 394.0,
            },
        )

        ex.run(
            "search_poi_along_the_route("
            "route_id='route_after_stop', "
            "category_poi='charging_stations', "
            "at_kilometer=394.0)"
        )

        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route")["at_kilometer"],
            57.7,
        )
        self.assertEqual(
            ws.scratchpad["gates"]["later_segment_charging_search_guard"]["status"],
            "REPAIRED_GLOBAL_DISTANCE_TO_SEGMENT_KM",
        )

    def test_later_segment_charging_search_leaves_non_soc_distance_alone(self):
        ws, ex = self.make(
            {
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {"pois_found_along_route": []},
                ),
            },
            {
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )
        ws.remember_entity(
            "navigation_state",
            {"route_ids": ["route_to_stop", "route_after_stop"]},
        )
        ws.scratchpad["entities"]["routes_by_id"] = {
            "route_to_stop": {
                "route_id": "route_to_stop",
                "distance_km": 336.3,
            },
            "route_after_stop": {
                "route_id": "route_after_stop",
                "distance_km": 1257.8,
            },
        }
        ws.remember_entity(
            "last_distance_by_soc",
            {
                "initial_state_of_charge": 98,
                "final_state_of_charge": 15,
                "distance_km": 394.0,
            },
        )

        ex.run(
            "search_poi_along_the_route("
            "route_id='route_after_stop', "
            "category_poi='charging_stations', "
            "at_kilometer=628.0)"
        )

        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route")["at_kilometer"],
            628.0,
        )
        self.assertNotIn("later_segment_charging_search_guard", ws.scratchpad["gates"])

    def test_active_route_soc_charging_helper_searches_later_segment(self):
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_lei", "loc_fra", "loc_bar"],
                        "routes_to_final_destination_id": [
                            "route_to_stop",
                            "route_after_stop",
                        ],
                        "details": {
                            "waypoints": [
                                {"id": "loc_lei", "name": "Leipzig"},
                                {"id": "loc_fra", "name": "Frankfurt"},
                                {"id": "loc_bar", "name": "Barcelona"},
                            ],
                            "routes": [
                                {
                                    "route_id": "route_to_stop",
                                    "start_id": "loc_lei",
                                    "destination_id": "loc_fra",
                                    "distance_km": 336.3,
                                },
                                {
                                    "route_id": "route_after_stop",
                                    "start_id": "loc_fra",
                                    "destination_id": "loc_bar",
                                    "distance_km": 1257.8,
                                },
                            ],
                        },
                    },
                ),
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 98, "remaining_range": "466.0km"},
                ),
                "get_distance_by_soc": (
                    "SUCCESS",
                    {"distance_km_for_98_until_15_percent_soc": "394.0km"},
                ),
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {
                        "pois_found_along_route": [
                            {
                                "id": "poi_fastned",
                                "name": "Fastned",
                                "category": "charging_stations",
                                "phone_number": "+49 358 8158348",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_fast",
                                        "power_kw": 150,
                                        "availability": "occupied",
                                    },
                                    {
                                        "plug_id": "plug_available",
                                        "power_kw": 100,
                                        "availability": "available",
                                    },
                                ],
                            }
                        ]
                    },
                ),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
                "get_distance_by_soc": tool_schema(
                    "get_distance_by_soc",
                    {
                        "initial_state_of_charge": {"type": "integer"},
                        "final_state_of_charge": {"type": "integer"},
                    },
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )

        result = ex.run(
            "search = find_charging_stop_on_active_route_by_soc("
            "reserve_state_of_charge=15)\n"
            "respond(search['selected_charging_plug']['station_name'])"
        )

        self.assertEqual(result.response_text, "Fastned")
        self.assertEqual(
            self._emitted(ws, "get_distance_by_soc"),
            {"initial_state_of_charge": 98, "final_state_of_charge": 15},
        )
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route"),
            {
                "route_id": "route_after_stop",
                "category_poi": "charging_stations",
                "at_kilometer": 50,
            },
        )
        report = ws.scratchpad["entities"]["last_active_route_soc_charging_search"]
        self.assertEqual(report["route_id"], "route_after_stop")
        self.assertEqual(report["search_at_kilometer"], 50)
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_plug"]["station_name"],
            "Fastned",
        )

    def test_active_route_soc_charging_helper_adds_available_filter_when_requested(self):
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_start", "loc_stop", "loc_dest"],
                        "routes_to_final_destination_id": ["route_before_stop", "route_after_stop"],
                        "details": {
                            "waypoints": [
                                {"id": "loc_start", "name": "Start"},
                                {"id": "loc_stop", "name": "Stop"},
                                {"id": "loc_dest", "name": "Destination"},
                            ],
                            "routes": [
                                {
                                    "route_id": "route_before_stop",
                                    "start_id": "loc_start",
                                    "destination_id": "loc_stop",
                                    "distance_km": 120,
                                },
                                {
                                    "route_id": "route_after_stop",
                                    "start_id": "loc_stop",
                                    "destination_id": "loc_dest",
                                    "distance_km": 300,
                                },
                            ],
                        },
                    },
                ),
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 90, "remaining_range": "450.0km"},
                ),
                "get_distance_by_soc": (
                    "SUCCESS",
                    {"distance_km_for_90_until_15_percent_soc": "275.0km"},
                ),
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {
                        "pois_found_along_route": [
                            {
                                "id": "poi_cha_1",
                                "name": "EV+",
                                "category": "charging_stations",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_occupied",
                                        "power_kw": 350,
                                        "availability": "occupied",
                                    },
                                    {
                                        "plug_id": "plug_available",
                                        "power_kw": 150,
                                        "availability": "available",
                                    },
                                ],
                            }
                        ]
                    },
                ),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
                "get_distance_by_soc": tool_schema(
                    "get_distance_by_soc",
                    {
                        "initial_state_of_charge": {"type": "integer"},
                        "final_state_of_charge": {"type": "integer"},
                    },
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                        "filters": {"type": "array"},
                    },
                ),
            },
        )

        result = ex.run(
            "search = find_charging_stop_on_active_route_by_soc("
            "reserve_state_of_charge=15, require_available=True)\n"
            "respond(search['selected_charging_plug']['plug_id'])"
        )

        self.assertEqual(result.response_text, "plug_available")
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route"),
            {
                "route_id": "route_after_stop",
                "category_poi": "charging_stations",
                "at_kilometer": 150,
                "filters": ["charging_stations::has_available_plug"],
            },
        )
        report = ws.scratchpad["entities"]["last_active_route_soc_charging_search"]
        self.assertTrue(report["availability_filter_applied"])

    def test_active_route_soc_charging_helper_does_not_search_when_route_ends_first(self):
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_start", "loc_dest"],
                        "routes_to_final_destination_id": ["route_short"],
                        "details": {
                            "waypoints": [
                                {"id": "loc_start", "name": "Start"},
                                {"id": "loc_dest", "name": "Destination"},
                            ],
                            "routes": [
                                {
                                    "route_id": "route_short",
                                    "start_id": "loc_start",
                                    "destination_id": "loc_dest",
                                    "distance_km": 80,
                                }
                            ],
                        },
                    },
                ),
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 90, "remaining_range": "450.0km"},
                ),
                "get_distance_by_soc": (
                    "SUCCESS",
                    {"distance_km_for_90_until_15_percent_soc": "375.0km"},
                ),
                "search_poi_along_the_route": ("SUCCESS", {"pois_found_along_route": []}),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
                "get_distance_by_soc": tool_schema(
                    "get_distance_by_soc",
                    {
                        "initial_state_of_charge": {"type": "integer"},
                        "final_state_of_charge": {"type": "integer"},
                    },
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )

        result = ex.run(
            "search = find_charging_stop_on_active_route_by_soc("
            "reserve_state_of_charge=15)\n"
            "respond(search['status'])"
        )

        self.assertEqual(result.response_text, "RESERVE_AFTER_ROUTE")
        self.assertIsNone(self._emitted(ws, "search_poi_along_the_route"))

    def test_active_route_kilometer_charging_helper_searches_current_segment(self):
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_here", "loc_stop", "loc_final"],
                        "routes_to_final_destination_id": ["route_current", "route_next"],
                        "details": {
                            "waypoints": [
                                {"id": "loc_here", "name": "Here"},
                                {"id": "loc_stop", "name": "Stop"},
                                {"id": "loc_final", "name": "Final"},
                            ],
                            "routes": [
                                {"route_id": "route_current", "distance_km": 300},
                                {"route_id": "route_next", "distance_km": 200},
                            ],
                        },
                    },
                ),
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {
                        "pois_found_along_route": [
                            {
                                "id": "poi_cha_1",
                                "name": "EV+",
                                "category": "charging_stations",
                                "phone_number": "+49 111",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_1",
                                        "power_type": "DC",
                                        "power_kw": 150,
                                        "availability": "available",
                                    }
                                ],
                            }
                        ]
                    },
                ),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )

        result = ex.run(
            "search = search_charging_stations_on_active_route(at_kilometer=100)\n"
            "respond(search['selected_charging_plug']['station_name'])"
        )

        self.assertEqual(result.response_text, "EV+")
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route"),
            {
                "route_id": "route_current",
                "category_poi": "charging_stations",
                "at_kilometer": 100.0,
            },
        )

    def test_planned_route_charging_helper_does_not_start_navigation(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 72, "remaining_range": "324.0km"},
                ),
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {
                        "pois_found_along_route": [
                            {
                                "id": "poi_cha_1",
                                "name": "PRE",
                                "category": "charging_stations",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_1",
                                        "power_kw": 150,
                                        "availability": "occupied",
                                    }
                                ],
                            }
                        ]
                    },
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
                "set_new_navigation": tool_schema(
                    "set_new_navigation",
                    {"route_ids": {"type": "array"}},
                ),
            },
        )

        result = ex.run(
            "search = search_charging_stations_on_route("
            "route_id='route_planned', at_kilometer=150)\n"
            "respond(search['selected_charging_plug']['station_name'])"
        )

        self.assertEqual(result.response_text, "PRE")
        self.assertEqual(
            [call["tool_name"] for batch in ws.bridge.requests for call in batch],
            ["get_charging_specs_and_status", "search_poi_along_the_route"],
        )
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route"),
            {
                "route_id": "route_planned",
                "category_poi": "charging_stations",
                "at_kilometer": 150.0,
            },
        )
        self.assertIsNone(self._emitted(ws, "set_new_navigation"))

    def test_planned_route_charging_helper_adds_available_filter_when_requested(self):
        ws, ex = self.make(
            {
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {
                        "pois_found_along_route": [
                            {
                                "id": "poi_cha_1",
                                "name": "PRE",
                                "category": "charging_stations",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_occupied",
                                        "power_kw": 350,
                                        "availability": "occupied",
                                    },
                                    {
                                        "plug_id": "plug_available",
                                        "power_kw": 150,
                                        "availability": "available",
                                    },
                                ],
                            }
                        ]
                    },
                ),
            },
            {
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                        "filters": {"type": "array"},
                    },
                ),
            },
        )

        result = ex.run(
            "search = search_charging_stations_on_route("
            "route_id='route_planned', at_kilometer=150, require_available=True)\n"
            "respond(search['selected_charging_plug']['plug_id'])"
        )

        self.assertEqual(result.response_text, "plug_available")
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route"),
            {
                "route_id": "route_planned",
                "category_poi": "charging_stations",
                "at_kilometer": 150.0,
                "filters": ["charging_stations::has_available_plug"],
            },
        )
        self.assertTrue(
            ws.scratchpad["entities"]["last_route_charging_search"][
                "availability_filter_applied"
            ]
        )

    def test_raw_route_charging_search_reads_charging_status_when_available(self):
        ws, ex = self.make(
            {
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 25, "remaining_range": "112.0km"},
                ),
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {"pois_found_along_route": []},
                ),
            },
            {
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )

        ex.run(
            "search_poi_along_the_route("
            "route_id='route_active', category_poi='charging_stations', at_kilometer=100)"
        )

        self.assertEqual(
            [call["tool_name"] for batch in ws.bridge.requests for call in batch],
            ["get_charging_specs_and_status", "search_poi_along_the_route"],
        )
        self.assertEqual(
            ws.scratchpad["entities"]["last_charging_specs_and_status"][
                "remaining_range_km"
            ],
            112.0,
        )

    def test_active_route_kilometer_charging_helper_adds_available_filter_when_requested(self):
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_here", "loc_final"],
                        "routes_to_final_destination_id": ["route_current"],
                        "details": {
                            "waypoints": [
                                {"id": "loc_here", "name": "Here"},
                                {"id": "loc_final", "name": "Final"},
                            ],
                            "routes": [{"route_id": "route_current", "distance_km": 300}],
                        },
                    },
                ),
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {
                        "pois_found_along_route": [
                            {
                                "id": "poi_cha_1",
                                "name": "EV+",
                                "category": "charging_stations",
                                "charging_plugs": [
                                    {
                                        "plug_id": "plug_occupied",
                                        "power_type": "DC",
                                        "power_kw": 300,
                                        "availability": "occupied",
                                    },
                                    {
                                        "plug_id": "plug_available",
                                        "power_type": "DC",
                                        "power_kw": 150,
                                        "availability": "available",
                                    },
                                ],
                            }
                        ]
                    },
                ),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                        "filters": {"type": "array"},
                    },
                ),
            },
        )

        result = ex.run(
            "search = search_charging_stations_on_active_route("
            "at_kilometer=100, require_available=True)\n"
            "respond(search['selected_charging_plug']['plug_id'])"
        )

        self.assertEqual(result.response_text, "plug_available")
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route"),
            {
                "route_id": "route_current",
                "category_poi": "charging_stations",
                "at_kilometer": 100.0,
                "filters": ["charging_stations::has_available_plug"],
            },
        )
        report = ws.scratchpad["entities"]["last_active_route_charging_search"]
        self.assertTrue(report["availability_filter_applied"])

    def test_active_route_kilometer_charging_helper_allows_explicit_segment(self):
        ws, ex = self.make(
            {
                "get_current_navigation_state": (
                    "SUCCESS",
                    {
                        "navigation_active": True,
                        "waypoints_id": ["loc_here", "loc_stop", "loc_final"],
                        "routes_to_final_destination_id": ["route_current", "route_next"],
                        "details": {
                            "waypoints": [
                                {"id": "loc_here", "name": "Here"},
                                {"id": "loc_stop", "name": "Stop"},
                                {"id": "loc_final", "name": "Final"},
                            ],
                            "routes": [
                                {"route_id": "route_current", "distance_km": 300},
                                {"route_id": "route_next", "distance_km": 200},
                            ]
                        },
                    },
                ),
                "search_poi_along_the_route": (
                    "SUCCESS",
                    {"pois_found_along_route": []},
                ),
            },
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
                "search_poi_along_the_route": tool_schema(
                    "search_poi_along_the_route",
                    {
                        "route_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                        "at_kilometer": {"type": "number"},
                    },
                ),
            },
        )

        result = ex.run(
            "search = search_charging_stations_on_active_route("
            "at_kilometer=25, route_id='route_next')\n"
            "respond(search['route_id'])"
        )

        self.assertEqual(result.response_text, "route_next")
        self.assertEqual(
            self._emitted(ws, "search_poi_along_the_route")["route_id"],
            "route_next",
        )

    def test_charge_window_stop_helper_uses_official_distance_by_soc(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_fast",
                                "start_id": "loc_home_1",
                                "destination_id": "loc_mad",
                                "distance_km": 1764.8,
                                "duration_hours": 22,
                                "duration_minutes": 3,
                                "alias": ["fastest", "first"],
                            },
                            {
                                "route_id": "route_slow",
                                "start_id": "loc_home_1",
                                "destination_id": "loc_mad",
                                "distance_km": 1856.6,
                                "duration_hours": 23,
                                "duration_minutes": 24,
                                "alias": ["second"],
                            },
                        ],
                    },
                ),
                "get_distance_by_soc": (
                    "SUCCESS",
                    {"distance_km_for_80_until_10_percent_soc": "476.0km"},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
                "get_distance_by_soc": tool_schema(
                    "get_distance_by_soc",
                    {
                        "initial_state_of_charge": {"type": "integer"},
                        "final_state_of_charge": {"type": "integer"},
                    },
                ),
            },
        )

        result = ex.run(
            "estimate = estimate_charging_stops_for_route_by_soc_window(\n"
            "    destination_id='loc_mad',\n"
            "    charge_from_state_of_charge=10,\n"
            "    charge_to_state_of_charge=80,\n"
            "    route_prefer='fastest',\n"
            ")\n"
            "respond(f\"{estimate['route_id']} {estimate['estimated_charging_stops']} "
            "{estimate['range_per_charge_window_km']:.0f}\")"
        )

        self.assertTrue(result.response_text.startswith("route_fast 4 476"))
        self.assertEqual(
            self._emitted(ws, "get_routes_from_start_to_destination"),
            {"start_id": "loc_home_1", "destination_id": "loc_mad"},
        )
        self.assertEqual(
            self._emitted(ws, "get_distance_by_soc"),
            {"initial_state_of_charge": 80, "final_state_of_charge": 10},
        )
        report = ws.scratchpad["entities"]["last_charging_stop_estimate"]
        self.assertEqual(report["route_id"], "route_fast")
        self.assertEqual(report["estimated_charging_stops"], 4)

    def test_charge_window_stop_helper_does_not_choose_without_route_preference(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_one",
                                "start_id": "loc_home_1",
                                "destination_id": "loc_mad",
                                "distance_km": 100,
                                "alias": ["first"],
                            },
                            {
                                "route_id": "route_two",
                                "start_id": "loc_home_1",
                                "destination_id": "loc_mad",
                                "distance_km": 120,
                                "alias": ["second"],
                            },
                        ],
                    },
                ),
                "get_distance_by_soc": (
                    "SUCCESS",
                    {"distance_km_for_80_until_10_percent_soc": "476.0km"},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
                "get_distance_by_soc": tool_schema(
                    "get_distance_by_soc",
                    {
                        "initial_state_of_charge": {"type": "integer"},
                        "final_state_of_charge": {"type": "integer"},
                    },
                ),
            },
        )

        result = ex.run(
            "estimate = estimate_charging_stops_for_route_by_soc_window(\n"
            "    destination_id='loc_mad',\n"
            "    charge_from_state_of_charge=10,\n"
            "    charge_to_state_of_charge=80,\n"
            ")\n"
            "respond(estimate['status'])"
        )

        self.assertEqual(result.response_text, "AMBIGUOUS")
        self.assertIsNone(self._emitted(ws, "get_distance_by_soc"))

    def test_charge_window_stop_helper_accepts_reversed_soc_bounds(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_fast",
                                "start_id": "loc_home_1",
                                "destination_id": "loc_mad",
                                "distance_km": 1764.8,
                                "alias": ["fastest", "first"],
                            }
                        ],
                    },
                ),
                "get_distance_by_soc": (
                    "SUCCESS",
                    {"distance_km_for_80_until_10_percent_soc": "476.0km"},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
                "get_distance_by_soc": tool_schema(
                    "get_distance_by_soc",
                    {
                        "initial_state_of_charge": {"type": "integer"},
                        "final_state_of_charge": {"type": "integer"},
                    },
                ),
            },
        )

        result = ex.run(
            "estimate = estimate_charging_stops_for_route_by_soc_window(\n"
            "    destination_id='loc_mad',\n"
            "    charge_from_state_of_charge=80,\n"
            "    charge_to_state_of_charge=10,\n"
            "    route_prefer='fastest',\n"
            ")\n"
            "respond(str(estimate['soc_bounds_reordered']) + ' ' + "
            "str(estimate['estimated_charging_stops']))"
        )

        self.assertEqual(result.response_text, "True 4")
        self.assertEqual(
            self._emitted(ws, "get_distance_by_soc"),
            {"initial_state_of_charge": 80, "final_state_of_charge": 10},
        )

    # --- 3. policy date/time exposure ------------------------------------

    def test_policy_now_facts_and_global(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "now = policy_now()\n"
            "respond(f\"day={now.get('day')} loc={policy_location_id()} \"\n"
            "        f\"fact={scratchpad['facts'].get('policy_now', {}).get('day')}\")"
        )
        self.assertIn("day=6", result.response_text)
        self.assertIn("loc=loc_home_1", result.response_text)
        self.assertIn("fact=6", result.response_text)

    def test_get_weather_uses_policy_date_and_preserves_explicit_time(self):
        ws, ex = self.make(
            {"get_weather": ("SUCCESS", {"condition": "rain"})},
            {
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "integer"},
                        "day": {"type": "integer"},
                        "time_hour_24hformat": {"type": "integer"},
                        "time_minutes": {"type": "integer"},
                    },
                ),
            },
        )
        ex.run(
            "get_weather(location_or_poi_id='loc_man', month=3, day=13, "
            "time_hour_24hformat=16, time_minutes=0)"
        )
        args = self._emitted(ws, "get_weather")
        self.assertEqual(args["month"], 6)
        self.assertEqual(args["day"], 6)
        self.assertEqual(args["time_hour_24hformat"], 16)
        self.assertEqual(args["time_minutes"], 0)

    def test_weather_current_slot_fields_are_top_level_aliases(self):
        ws, ex = self.make(
            {
                "get_weather": (
                    "SUCCESS",
                    {
                        "current_slot": {
                            "start_time": "09:00",
                            "end_time": "12:00",
                            "temperature_c": 26,
                            "wind_speed_kph": 20,
                            "humidity_percent": 91,
                            "condition": "cloudy_and_rain",
                        }
                    },
                )
            },
            {
                "get_weather": tool_schema(
                    "get_weather",
                    {
                        "location_or_poi_id": {"type": "string"},
                        "month": {"type": "integer"},
                        "day": {"type": "integer"},
                        "time_hour_24hformat": {"type": "integer"},
                        "time_minutes": {"type": "integer"},
                    },
                ),
            },
        )

        result = ex.run(
            "weather = get_weather(location_or_poi_id='loc_and', month=8, day=8, "
            "time_hour_24hformat=11, time_minutes=0)\n"
            "stored = scratchpad['entities']['last_weather']\n"
            "respond('|'.join([\n"
            "    str(weather['temperature_c']),\n"
            "    weather['condition'],\n"
            "    str(weather['current_temperature_c']),\n"
            "    stored['condition'],\n"
            "]))"
        )

        self.assertEqual(result.response_text, "26|cloudy_and_rain|26|cloudy_and_rain")

    def test_workspace_scratchpad_section_aliases(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "ws.facts['a'] = 1\n"
            "ws['entities']['b'] = 2\n"
            "respond(f\"{scratchpad['facts']['a']}|{ws.entities['b']}\")"
        )
        self.assertEqual(result.response_text, "1|2")

    def test_policy_location_callable_argument_is_resolved(self):
        ws, ex = self.make(
            {"search_poi_at_location": ("SUCCESS", {"pois_found": []})},
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                ),
            },
        )
        ex.run(
            "search_poi_at_location("
            "location_id=policy_location_id, category_poi='charging_stations')"
        )
        self.assertEqual(
            ws.bridge.requests[0][0]["arguments"]["location_id"],
            "loc_home_1",
        )

    # --- 4. auto-persistence ---------------------------------------------

    def test_navigation_state_persisted(self):
        nav = {
            "navigation_active": True,
            "waypoints_id": ["loc_a", "loc_b"],
            "details": {"waypoints": [{"id": "loc_a", "name": "Aix"}, {"id": "loc_b", "name": "Bonn"}]},
        }
        ws, ex = self.make(
            {"get_current_navigation_state": ("SUCCESS", nav)},
            {"get_current_navigation_state": tool_schema(
                "get_current_navigation_state", {"detailed_information": {"type": "boolean"}})},
        )
        ex.run("get_current_navigation_state(detailed_information=True)")
        state = ws.scratchpad["entities"].get("navigation_state")
        self.assertEqual(state.get("navigation_active"), True)
        self.assertIn("Bonn", state.get("waypoint_names", []))
        self.assertEqual(state.get("waypoint_count"), 2)
        self.assertEqual(state.get("segment_count"), 1)
        self.assertEqual(state.get("intermediate_waypoint_count"), 0)
        self.assertIs(state.get("is_multi_stop"), False)
        self.assertNotIn("final_destination_replacement_rule", state)

    def test_navigation_helper_exposes_multi_stop_route_shape(self):
        nav = {
            "navigation_active": True,
            "waypoints_id": ["loc_a", "loc_b", "loc_c"],
            "routes_to_final_destination_id": ["route_ab", "route_bc"],
            "details": {
                "waypoints": [
                    {"id": "loc_a", "name": "Aix"},
                    {"id": "loc_b", "name": "Bonn"},
                    {"id": "loc_c", "name": "Cologne"},
                ],
                "routes": [
                    {"route_id": "route_ab"},
                    {"route_id": "route_bc"},
                ],
            },
        }
        ws, _ = self.make(
            {"get_current_navigation_state": ("SUCCESS", nav)},
            {
                "get_current_navigation_state": tool_schema(
                    "get_current_navigation_state",
                    {"detailed_information": {"type": "boolean"}},
                ),
            },
        )
        normalized = ws.get_navigation_state(detailed_information=True)
        self.assertEqual(
            (normalized["is_multi_stop"], normalized["segment_count"]),
            (True, 2),
        )
        state = ws.scratchpad["entities"]["navigation_state"]
        self.assertIs(state["is_multi_stop"], True)
        self.assertEqual(state["intermediate_waypoint_count"], 1)
        self.assertNotIn("final_destination_replacement_rule", state)

    def test_pois_persisted_with_phone(self):
        pois = {"pois": [{"name": "Gasthaus", "id": "poi_1", "phone_number": "+49 89 123"}]}
        ws, ex = self.make(
            {"search_poi_at_location": ("SUCCESS", pois)},
            {"search_poi_at_location": tool_schema(
                "search_poi_at_location",
                {"location_id": {"type": "string"}, "category_poi": {"type": "string"}})},
        )
        ex.run("search_poi_at_location(location_id='loc_a', category_poi='restaurants')")
        stored = ws.scratchpad["entities"].get("last_pois")
        self.assertTrue(stored)
        self.assertEqual(stored[0]["phone_number"], "+49 89 123")

    def test_known_poi_name_location_lookup_uses_poi_id_without_bridge_call(self):
        pois = {
            "pois_found": [
                {
                    "id": "poi_ionity",
                    "name": "Ionity",
                    "category": "charging_stations",
                    "corresponding_location_id": "loc_war",
                }
            ]
        }
        ws, ex = self.make(
            {
                "search_poi_at_location": ("SUCCESS", pois),
                "get_location_id_by_location_name": ("FAILURE", {}),
            },
            {
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                    required=["location_id", "category_poi"],
                ),
                "get_location_id_by_location_name": tool_schema(
                    "get_location_id_by_location_name",
                    {"location": {"type": "string"}},
                    required=["location"],
                ),
            },
        )
        ex.run("search_poi_at_location(location_id='loc_war', category_poi='charging_stations')")
        request_count = len(ws.bridge.requests)

        result = ex.run(
            "loc = get_location_id_by_location_name(location='Ionity')\n"
            "respond(loc['result']['id'])"
        )

        self.assertEqual(result.response_text, "poi_ionity")
        self.assertEqual(len(ws.bridge.requests), request_count)
        self.assertEqual(
            ws.scratchpad["entities"]["selected_charging_poi"]["poi_id"],
            "poi_ionity",
        )

    def test_nav_mutation_marks_active(self):
        ws, ex = self.make(
            {"navigation_replace_final_destination": ("SUCCESS", {})},
            {"navigation_replace_final_destination": tool_schema(
                "navigation_replace_final_destination",
                {"new_destination_id": {"type": "string"},
                 "route_id_leading_to_new_destination": {"type": "string"}})},
        )
        ex.run(
            "navigation_replace_final_destination("
            "new_destination_id='loc_x', route_id_leading_to_new_destination='route_x')"
        )
        self.assertEqual(
            ws.scratchpad["entities"]["navigation_state"]["navigation_active"], True
        )


    # --- 5. degenerate / malformed call guards ---------------------------

    def test_get_routes_same_start_dest_skipped(self):
        ws, ex = self.make(
            {},
            {"get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}})},
        )
        ex.run(
            "r = get_routes_from_start_to_destination(start_id='loc_a', destination_id='loc_a')\n"
            "respond(r['status'])"
        )
        # Degenerate call is never emitted to the evaluator.
        self.assertEqual(ws.bridge.requests, [])
        self.assertEqual(ws.scratchpad["gates"]["degenerate_route_guard"]["status"], "SKIPPED")

    def test_get_routes_distinct_start_dest_emitted(self):
        ws, ex = self.make(
            {"get_routes_from_start_to_destination": ("SUCCESS", {"routes": []})},
            {"get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}})},
        )
        ex.run("get_routes_from_start_to_destination(start_id='loc_a', destination_id='loc_b')")
        self.assertEqual(len(ws.bridge.requests), 1)

    def test_get_routes_normalizes_known_location_name_endpoint(self):
        ws, ex = self.make(
            {
                "get_location_id_by_location_name": (
                    "SUCCESS",
                    {"id": "loc_stu_828398"},
                ),
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": []},
                ),
            },
            {
                "get_location_id_by_location_name": tool_schema(
                    "get_location_id_by_location_name",
                    {"location": {"type": "string"}},
                ),
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
            },
        )
        ex.run(
            "get_location_id_by_location_name(location='Stuttgart')\n"
            "get_routes_from_start_to_destination("
            "start_id='poi_cha_363177', destination_id='Stuttgart')"
        )
        self.assertEqual(
            ws.bridge.requests[-1][0]["arguments"]["destination_id"],
            "loc_stu_828398",
        )
        self.assertEqual(
            ws.scratchpad["gates"]["route_endpoint_guard"]["status"],
            "NORMALIZED",
        )

    def test_get_routes_blocks_unknown_raw_location_name_endpoint(self):
        ws, ex = self.make(
            {},
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
            },
        )
        result = ex.run(
            "r = get_routes_from_start_to_destination("
            "start_id='poi_cha_363177', destination_id='Stuttgart')\n"
            "respond(r['status'])"
        )
        self.assertEqual(result.response_text, "NEEDS_GROUNDED_ROUTE_ENDPOINT")
        self.assertEqual(ws.bridge.requests, [])

    def test_charging_route_search_without_kilometer_blocked(self):
        ws, ex = self.make(
            {},
            {"search_poi_along_the_route": tool_schema(
                "search_poi_along_the_route",
                {"route_id": {"type": "string"}, "category_poi": {"type": "string"},
                 "at_kilometer": {"type": "number"}})},
        )
        ex.run(
            "r = search_poi_along_the_route(route_id='r1', category_poi='charging_station')\n"
            "respond(r['status'])"
        )
        self.assertEqual(ws.bridge.requests, [])
        self.assertEqual(ws.scratchpad["gates"]["charging_search_guard"]["status"], "NEEDS_KILOMETER")

    def test_charging_route_search_with_kilometer_emitted(self):
        ws, ex = self.make(
            {"search_poi_along_the_route": ("SUCCESS", {"pois": []})},
            {"search_poi_along_the_route": tool_schema(
                "search_poi_along_the_route",
                {"route_id": {"type": "string"}, "category_poi": {"type": "string"},
                 "at_kilometer": {"type": "number"}})},
        )
        ex.run(
            "search_poi_along_the_route(route_id='r1', category_poi='charging_station', at_kilometer=150)"
        )
        self.assertEqual(len(ws.bridge.requests), 1)


    # --- 6. active-route edit adjacency derivation -----------------------

    NAV_4WP = {
        "navigation_active": True,
        "waypoints_id": ["loc_a", "loc_b", "loc_c", "loc_d"],
        "details": {"waypoints": [
            {"id": "loc_a", "name": "Aix"}, {"id": "loc_b", "name": "Bonn"},
            {"id": "loc_c", "name": "Cologne"}, {"id": "loc_d", "name": "Dortmund"},
        ]},
    }
    NAV_2WP = {
        "navigation_active": True,
        "waypoints_id": ["loc_a", "loc_old"],
        "routes_to_final_destination_id": ["R_old"],
        "details": {
            "waypoints": [
                {"id": "loc_a", "name": "Aix"},
                {"id": "loc_old", "name": "Old Destination"},
            ],
            "routes": [{"route_id": "R_old"}],
        },
    }
    UNKNOWN_NAV_STRUCTURE = {
        "navigation_active": True,
        "waypoints_id": "unknown",
        "routes_to_final_destination_id": "unknown",
        "details": {
            "waypoints": "unknown",
            "routes": "unknown",
        },
    }
    REPLACEMENT_ROUTES = [
        {
            "route_id": "R_fast",
            "start_id": "loc_a",
            "destination_id": "loc_new",
            "name_via": "A74",
            "distance_km": 100,
            "duration_hours": 1,
            "duration_minutes": 20,
            "alias": ["fastest", "first"],
        },
        {
            "route_id": "R_second",
            "start_id": "loc_a",
            "destination_id": "loc_new",
            "name_via": "K57, B65",
            "distance_km": 95,
            "duration_hours": 1,
            "duration_minutes": 30,
            "alias": ["shortest", "second"],
        },
    ]

    def _nav_edit_ws(self, extra_tool, extra_props):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state", {"detailed_information": {"type": "boolean"}}),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}}),
            extra_tool: tool_schema(extra_tool, extra_props),
        }
        responses = {
            "get_current_navigation_state": ("SUCCESS", self.NAV_4WP),
            "get_routes_from_start_to_destination": ("SUCCESS", {"routes": [
                {"route_id": "R_derived", "start_id": "x", "destination_id": "y", "name_via": "K1"}]}),
            extra_tool: ("SUCCESS", {}),
        }
        return self.make(responses, tools)

    def _emitted(self, ws, tool_name):
        for batch in ws.bridge.requests:
            for call in batch:
                if call["tool_name"] == tool_name:
                    return call["arguments"]
        return None

    def _final_replace_ws(self, nav=None, routes=None):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
            "navigation_replace_final_destination": tool_schema(
                "navigation_replace_final_destination",
                {
                    "new_destination_id": {"type": "string"},
                    "route_id_leading_to_new_destination": {"type": "string"},
                },
            ),
        }
        responses = {
            "get_current_navigation_state": (
                "SUCCESS",
                nav or self.NAV_2WP,
            ),
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {"routes": routes or self.REPLACEMENT_ROUTES},
            ),
            "navigation_replace_final_destination": ("SUCCESS", {}),
        }
        return self.make(responses, tools)

    def test_delete_mid_waypoint_derives_connecting_route(self):
        ws, ex = self._nav_edit_ws(
            "navigation_delete_waypoint",
            {"waypoint_id_to_delete": {"type": "string"},
             "route_id_without_waypoint": {"type": "string"}})
        # Model supplies a STALE route id; the guard must override it.
        ex.run("navigation_delete_waypoint(waypoint_id_to_delete='loc_b', route_id_without_waypoint='R_stale')")
        args = self._emitted(ws, "navigation_delete_waypoint")
        self.assertEqual(args["route_id_without_waypoint"], "R_derived")

    def test_delete_mid_waypoint_preserves_supplied_connecting_route(self):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
            ),
            "navigation_delete_waypoint": tool_schema(
                "navigation_delete_waypoint",
                {
                    "waypoint_id_to_delete": {"type": "string"},
                    "route_id_without_waypoint": {"type": "string"},
                },
            ),
        }
        responses = {
            "get_current_navigation_state": ("SUCCESS", self.NAV_4WP),
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {
                    "routes": [
                        {
                            "route_id": "R_fast",
                            "start_id": "loc_a",
                            "destination_id": "loc_c",
                            "name_via": "A11, A51",
                            "alias": ["fastest", "first"],
                        },
                        {
                            "route_id": "R_second",
                            "start_id": "loc_a",
                            "destination_id": "loc_c",
                            "name_via": "A58",
                            "alias": ["second"],
                        },
                    ]
                },
            ),
            "navigation_delete_waypoint": ("SUCCESS", {"waypoint_deleted": True}),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "routes = get_route_options(start_id='loc_a', destination_id='loc_c')\n"
            "second = select_route(routes['routes'], route_id='R_second')\n"
            "navigation_delete_waypoint(\n"
            "    waypoint_id_to_delete='loc_b',\n"
            "    route_id_without_waypoint=second['selected_route_id'],\n"
            ")\n"
            "respond('Bonn has been removed from your route.')"
        )

        self.assertEqual(
            self._emitted(ws, "navigation_delete_waypoint")["route_id_without_waypoint"],
            "R_second",
        )
        self.assertIn("selected route via A58", result.response_text)
        self.assertNotIn("fastest route", result.response_text)

    def test_delete_mid_waypoint_names_selected_route_and_offers_alternatives(self):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
            ),
            "navigation_delete_waypoint": tool_schema(
                "navigation_delete_waypoint",
                {
                    "waypoint_id_to_delete": {"type": "string"},
                    "route_id_without_waypoint": {"type": "string"},
                },
            ),
        }
        responses = {
            "get_current_navigation_state": ("SUCCESS", self.NAV_4WP),
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {
                    "routes": [
                        {
                            "route_id": "R_fast",
                            "start_id": "loc_a",
                            "destination_id": "loc_c",
                            "name_via": "A11, A51",
                            "alias": ["fastest", "first", "shortest"],
                        },
                        {
                            "route_id": "R_second",
                            "start_id": "loc_a",
                            "destination_id": "loc_c",
                            "name_via": "A58",
                            "alias": ["second"],
                        },
                    ]
                },
            ),
            "navigation_delete_waypoint": ("SUCCESS", {"waypoint_deleted": True}),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "navigation_delete_waypoint(waypoint_id_to_delete='loc_b')\n"
            "respond('Bonn has been removed from your route.')"
        )

        self.assertEqual(
            self._emitted(ws, "navigation_delete_waypoint")["route_id_without_waypoint"],
            "R_fast",
        )
        self.assertIn("fastest route", result.response_text)
        self.assertIn("A11, A51", result.response_text)
        self.assertIn("alternative", result.response_text.casefold())

    def test_insert_mid_waypoint_derives_after_args(self):
        ws, ex = self._nav_edit_ws(
            "navigation_add_one_waypoint",
            {"waypoint_id_to_add": {"type": "string"},
             "waypoint_id_before_new_waypoint": {"type": "string"},
             "route_id_leading_to_new_waypoint": {"type": "string"},
             "waypoint_id_after_new_waypoint": {"type": "string"},
             "route_id_leading_away_from_new_waypoint": {"type": "string"}})
        # Insert after loc_b (mid-route): after-waypoint args are missing.
        ex.run(
            "navigation_add_one_waypoint(waypoint_id_to_add='loc_new', "
            "waypoint_id_before_new_waypoint='loc_b', route_id_leading_to_new_waypoint='R_to')"
        )
        args = self._emitted(ws, "navigation_add_one_waypoint")
        self.assertEqual(args["waypoint_id_after_new_waypoint"], "loc_c")
        self.assertEqual(args["route_id_leading_away_from_new_waypoint"], "R_derived")

    def test_insert_with_after_waypoint_but_missing_away_route_derives_it(self):
        # base_64 bug: model supplies waypoint_id_after but NOT route_id_leading_away;
        # the guard must derive the away route instead of forwarding incomplete.
        ws, ex = self._nav_edit_ws(
            "navigation_add_one_waypoint",
            {"waypoint_id_to_add": {"type": "string"},
             "waypoint_id_before_new_waypoint": {"type": "string"},
             "route_id_leading_to_new_waypoint": {"type": "string"},
             "waypoint_id_after_new_waypoint": {"type": "string"},
             "route_id_leading_away_from_new_waypoint": {"type": "string"}})
        ex.run(
            "navigation_add_one_waypoint(waypoint_id_to_add='loc_new', "
            "waypoint_id_before_new_waypoint='loc_b', route_id_leading_to_new_waypoint='R_to', "
            "waypoint_id_after_new_waypoint='loc_c')"
        )
        args = self._emitted(ws, "navigation_add_one_waypoint")
        self.assertEqual(args["route_id_leading_away_from_new_waypoint"], "R_derived")

    def test_delete_already_removed_waypoint_is_idempotent(self):
        # base_88: a repeated delete of an already-removed waypoint must not be
        # emitted (would loop on NavigationDelete_005); return idempotent success.
        ws, ex = self._nav_edit_ws(
            "navigation_delete_waypoint",
            {"waypoint_id_to_delete": {"type": "string"},
             "route_id_without_waypoint": {"type": "string"}})
        result = ex.run(
            "r = navigation_delete_waypoint(waypoint_id_to_delete='loc_GONE')\n"
            "respond('ok' if r.get('status') == 'SUCCESS' else 'fail')"
        )
        self.assertIsNone(self._emitted(ws, "navigation_delete_waypoint"))  # never emitted
        self.assertEqual(result.response_text, "ok")

    def test_get_navigation_state_unknown_structure_aborts_with_edit_limitation(self):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
        }
        ws, ex = self.make(
            {"get_current_navigation_state": ("SUCCESS", self.UNKNOWN_NAV_STRUCTURE)},
            tools,
        )
        ws.observe_user("Can you remove the intermediate stop and take me straight to Paris?")

        result = ex.run("get_navigation_state()")

        self.assertIsNotNone(result.response_text)
        self.assertIn("can't use the current navigation state", result.response_text)
        self.assertIn("looked up the current navigation state", result.response_text)
        self.assertIn("did not provide the current waypoint order", result.response_text)
        self.assertIn("route information", result.response_text)

    def test_raw_unknown_navigation_field_access_uses_edit_limitation(self):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
        }
        ws, ex = self.make(
            {"get_current_navigation_state": ("SUCCESS", self.UNKNOWN_NAV_STRUCTURE)},
            tools,
        )
        ws.observe_user("Please skip the intermediate stop on my route.")

        result = ex.run(
            "state = result_value(get_current_navigation_state(detailed_information=True))\n"
            "len(state['waypoints_id'])"
        )

        self.assertIsNotNone(result.response_text)
        self.assertIn("can't use the current navigation state", result.response_text)
        self.assertIn("route structure", result.response_text)

    def test_unknown_navigation_structure_replaces_internal_response(self):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
        }
        ws, ex = self.make(
            {"get_current_navigation_state": ("SUCCESS", self.UNKNOWN_NAV_STRUCTURE)},
            tools,
        )
        ws.observe_user("Remove the intermediate stop from my route.")

        result = ex.run(
            "get_current_navigation_state(detailed_information=True)\n"
            "respond('I hit an internal issue while deciding the next step.')"
        )

        self.assertIsNotNone(result.response_text)
        self.assertIn("can't use the current navigation state", result.response_text)
        self.assertNotIn("internal issue", result.response_text)

    def test_insert_after_final_destination_leaves_args_untouched(self):
        ws, ex = self._nav_edit_ws(
            "navigation_add_one_waypoint",
            {"waypoint_id_to_add": {"type": "string"},
             "waypoint_id_before_new_waypoint": {"type": "string"},
             "route_id_leading_to_new_waypoint": {"type": "string"},
             "waypoint_id_after_new_waypoint": {"type": "string"},
             "route_id_leading_away_from_new_waypoint": {"type": "string"}})
        # Insert after loc_d (the final destination): new waypoint becomes final,
        # so no after-args should be added.
        ex.run(
            "navigation_add_one_waypoint(waypoint_id_to_add='loc_new', "
            "waypoint_id_before_new_waypoint='loc_d', route_id_leading_to_new_waypoint='R_to')"
        )
        args = self._emitted(ws, "navigation_add_one_waypoint")
        self.assertNotIn("waypoint_id_after_new_waypoint", args)

    def test_insert_existing_waypoint_is_truthful_noop(self):
        ws, ex = self._nav_edit_ws(
            "navigation_add_one_waypoint",
            {"waypoint_id_to_add": {"type": "string"},
             "waypoint_id_before_new_waypoint": {"type": "string"},
             "route_id_leading_to_new_waypoint": {"type": "string"},
             "waypoint_id_after_new_waypoint": {"type": "string"},
             "route_id_leading_away_from_new_waypoint": {"type": "string"}})
        result = ex.run(
            "r = navigation_add_one_waypoint(waypoint_id_to_add='loc_c', "
            "waypoint_id_before_new_waypoint='loc_b', "
            "route_id_leading_to_new_waypoint='R_to')\n"
            "respond(str(r.get('already_present')) + '|' + "
            "str(r['result'].get('waypoint_added')))"
        )
        self.assertEqual(result.response_text, "True|False")
        self.assertIsNone(self._emitted(ws, "navigation_add_one_waypoint"))

    def test_final_replacement_does_not_parse_user_wording(self):
        ws, ex = self._final_replace_ws()
        ws.observe_user("Can you only show me the second route?")
        ex.run(
            "navigation_replace_final_destination("
            "new_destination_id='loc_new', "
            "route_id_leading_to_new_destination='R_second')"
        )
        args = self._emitted(ws, "navigation_replace_final_destination")
        self.assertEqual(args["route_id_leading_to_new_destination"], "R_second")

    def test_multi_stop_replacement_preserves_model_route_choice(self):
        ws, ex = self._final_replace_ws(nav=self.NAV_4WP)
        ws.observe_user("Change my final destination to New City.")
        ex.run(
            "navigation_replace_final_destination("
            "new_destination_id='loc_new', "
            "route_id_leading_to_new_destination='R_second')"
        )
        args = self._emitted(ws, "navigation_replace_final_destination")
        self.assertEqual(args["route_id_leading_to_new_destination"], "R_second")

    def test_single_available_replacement_route_is_allowed(self):
        ws, ex = self._final_replace_ws(routes=[self.REPLACEMENT_ROUTES[0]])
        ws.observe_user("Change my destination to New City.")
        ex.run("navigation_replace_final_destination(new_destination_id='loc_new')")
        args = self._emitted(ws, "navigation_replace_final_destination")
        self.assertEqual(args["route_id_leading_to_new_destination"], "R_fast")

    def test_missing_destination_replacement_tool_does_not_block_route_lookup_from_user_text(self):
        tools = {
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
        }
        responses = {
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {"routes": self.REPLACEMENT_ROUTES},
            ),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user("Change my destination to New City.")
        ws.remember_entity(
            "navigation_state",
            {
                "navigation_active": True,
                "waypoint_order": ["loc_a", "loc_old"],
                "destination_id": "loc_old",
                "final_destination_id": "loc_old",
            },
        )

        result = ex.run(
            "get_routes_from_start_to_destination("
            "start_id='loc_a', destination_id='loc_new')"
        )

        self.assertIsNone(result.response_text)
        self.assertEqual(
            self._emitted(ws, "get_routes_from_start_to_destination"),
            {"start_id": "loc_a", "destination_id": "loc_new"},
        )

    def test_read_only_route_lookup_not_blocked_by_missing_destination_replacement(self):
        tools = {
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
        }
        responses = {
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {"routes": self.REPLACEMENT_ROUTES},
            ),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user("Show me routes to New City.")
        ws.remember_entity(
            "navigation_state",
            {
                "navigation_active": True,
                "waypoint_order": ["loc_a", "loc_old"],
                "destination_id": "loc_old",
                "final_destination_id": "loc_old",
            },
        )

        result = ex.run(
            "get_routes_from_start_to_destination("
            "start_id='loc_a', destination_id='loc_new')"
        )

        self.assertIsNone(result.response_text)
        self.assertEqual(
            self._emitted(ws, "get_routes_from_start_to_destination"),
            {"start_id": "loc_a", "destination_id": "loc_new"},
        )

    def test_missing_destination_replacement_tool_blocks_wrapper_before_route_derivation(self):
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
        }
        responses = {
            "get_current_navigation_state": ("SUCCESS", self.NAV_2WP),
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {"routes": self.REPLACEMENT_ROUTES},
            ),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user("Change my destination to New City.")

        result = ex.run("navigation_replace_final_destination(new_destination_id='loc_new')")

        self.assertIsNotNone(result.response_text)
        self.assertIn("can't change the destination", result.response_text)
        self.assertIsNone(self._emitted(ws, "navigation_replace_final_destination"))
        self.assertIsNone(self._emitted(ws, "get_routes_from_start_to_destination"))

    def test_route_choice_response_for_unavailable_replacement_is_not_rewritten_from_user_text(self):
        tools = {
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
        }
        ws, ex = self.make({}, tools)
        ws.observe_user("Change my destination to New City.")
        ws.remember_entity(
            "navigation_state",
            {
                "navigation_active": True,
                "waypoint_order": ["loc_a", "loc_old"],
                "destination_id": "loc_old",
                "final_destination_id": "loc_old",
            },
        )
        ws.remember_entity(
            "last_route_options",
            {
                "start_id": "loc_a",
                "destination_id": "loc_new",
                "routes": self.REPLACEMENT_ROUTES,
            },
        )

        result = ex.run(
            "respond('Fastest route: R_fast. There are 2 other route alternatives. "
            "Which route would you like to take?')"
        )

        self.assertIsNotNone(result.response_text)
        self.assertIn("Which route", result.response_text)
        self.assertNotIn("can't change the destination", result.response_text)

    def test_replace_final_destination_fills_only_route(self):
        ws, ex = self._nav_edit_ws(
            "navigation_replace_final_destination",
            {
                "new_destination_id": {"type": "string"},
                "route_id_leading_to_new_destination": {"type": "string"},
            },
        )
        ex.run("navigation_replace_final_destination(new_destination_id='loc_new')")
        args = self._emitted(ws, "navigation_replace_final_destination")
        self.assertEqual(args["route_id_leading_to_new_destination"], "R_derived")

    def test_final_destination_maps_base_route_id_to_poi_route_id(self):
        routes = [
            {
                "route_id": "rlp_bar_res_409480",
                "base_route_id": "rll_bar_mad_615661",
                "start_id": "loc_bar_223644",
                "destination_id": "poi_res_825069",
                "alias": ["fastest", "first", "shortest"],
            },
            {
                "route_id": "rlp_bar_res_209760",
                "base_route_id": "rll_bar_mad_233586",
                "start_id": "loc_bar_223644",
                "destination_id": "poi_res_825069",
                "alias": ["second"],
            },
        ]
        nav = {
            "navigation_active": True,
            "waypoints_id": ["loc_bar_223644", "loc_vie_753398"],
            "details": {
                "waypoints": [
                    {"id": "loc_bar_223644"},
                    {"id": "loc_vie_753398"},
                ],
            },
        }
        ws, ex = self._final_replace_ws(nav=nav, routes=routes)
        ex.run(
            "navigation_replace_final_destination("
            "new_destination_id='poi_res_825069', "
            "route_id_leading_to_new_destination='rll_bar_mad_615661')"
        )
        args = self._emitted(ws, "navigation_replace_final_destination")
        self.assertEqual(
            args["route_id_leading_to_new_destination"],
            "rlp_bar_res_409480",
        )


    # --- 7. occupied-seat heating helper ---------------------------------

    def _seat_ws(self, occupancy, levels):
        tools = {
            "get_seats_occupancy": tool_schema("get_seats_occupancy", {}),
            "get_seat_heating_level": tool_schema("get_seat_heating_level", {}),
            "set_seat_heating": tool_schema(
                "set_seat_heating",
                {"level": {"type": "integer"}, "seat_zone": {"type": "string"}}),
        }
        responses = {
            "get_seats_occupancy": ("SUCCESS", {"seats_occupied": occupancy}),
            "get_seat_heating_level": ("SUCCESS", levels),
            "set_seat_heating": ("SUCCESS", {}),
        }
        return self.make(responses, tools)

    def _seat_sets(self, ws):
        out = []
        for batch in ws.bridge.requests:
            for call in batch:
                if call["tool_name"] == "set_seat_heating":
                    out.append(call["arguments"])
        return out

    def test_occupied_seat_relative_increase(self):
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": True, "driver_rear": False, "passenger_rear": False},
            {"seat_heating_driver": 0, "seat_heating_passenger": 0})
        ex.run("set_occupied_seat_heating(increase_by=2)")
        sets = self._seat_sets(ws)
        self.assertEqual({s["seat_zone"]: s["level"] for s in sets}, {"DRIVER": 2, "PASSENGER": 2})

    def test_occupied_seat_only_occupied_zones(self):
        # Only the driver is occupied -> passenger must not be heated.
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": False, "driver_rear": False, "passenger_rear": False},
            {"seat_heating_driver": 1, "seat_heating_passenger": 0})
        ex.run("set_occupied_seat_heating(level=3)")
        sets = self._seat_sets(ws)
        self.assertEqual([(s["seat_zone"], s["level"]) for s in sets], [("DRIVER", 3)])

    def test_occupied_seat_explicit_zone_does_not_heat_other_occupied_seat(self):
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": True, "driver_rear": False, "passenger_rear": False},
            {"seat_heating_driver": 0, "seat_heating_passenger": 0})
        ex.run("set_occupied_seat_heating(seat_zone='DRIVER', level=2)")
        sets = self._seat_sets(ws)
        self.assertEqual(sets, [{"level": 2, "seat_zone": "DRIVER"}])
        emitted_names = [
            call["tool_name"]
            for batch in ws.bridge.requests
            for call in batch
        ]
        self.assertNotIn("get_seats_occupancy", emitted_names)

    def test_occupied_seat_explicit_zone_relative_uses_only_that_zone(self):
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": True},
            {"seat_heating_driver": 0, "seat_heating_passenger": 2})
        ex.run("set_occupied_seat_heating(seat_zone='PASSENGER', increase_by=2)")
        sets = self._seat_sets(ws)
        self.assertEqual(sets, [{"level": 3, "seat_zone": "PASSENGER"}])

    def test_occupied_seat_relative_clamps_to_max(self):
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": False},
            {"seat_heating_driver": 2, "seat_heating_passenger": 0})
        ex.run("set_occupied_seat_heating(increase_by=2)")  # 2+2 -> clamp 3
        self.assertEqual(self._seat_sets(ws)[0]["level"], 3)

    def test_no_occupied_seats_no_setter(self):
        ws, ex = self._seat_ws(
            {"driver": False, "passenger": False},
            {"seat_heating_driver": 0, "seat_heating_passenger": 0})
        # Success helpers no longer lock the response; they record a suggested
        # message the model can compose with. No setter should fire.
        result = ex.run(
            "set_occupied_seat_heating(level=2)\n"
            "respond(scratchpad['facts'].get('last_helper_message', ''))"
        )
        self.assertEqual(self._seat_sets(ws), [])
        self.assertIn("nothing to heat", result.response_text)

    def test_turn_off_unoccupied_seat_heating_only_changes_empty_front_seats(self):
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": False, "driver_rear": True, "passenger_rear": False},
            {"seat_heating_driver": 2, "seat_heating_passenger": 2})
        result = ex.run("r = turn_off_unoccupied_seat_heating()\nrespond(r['message'])")
        self.assertEqual(self._seat_sets(ws), [{"level": 0, "seat_zone": "PASSENGER"}])
        self.assertIn("passenger", result.response_text)
        self.assertEqual(
            ws.scratchpad["facts"]["last_helper_report"]["occupied_rear_unheated"],
            ["driver_rear"],
        )

    def test_turn_off_unoccupied_seat_heating_noops_when_empty_seats_already_off(self):
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": False},
            {"seat_heating_driver": 2, "seat_heating_passenger": 0})
        result = ex.run("r = turn_off_unoccupied_seat_heating()\nrespond(r['message'])")
        self.assertEqual(self._seat_sets(ws), [])
        self.assertIn("No unoccupied front seat heating needed", result.response_text)

    def test_turn_off_unoccupied_seat_heating_sets_zero_when_level_unknown(self):
        ws, ex = self._seat_ws(
            {"driver": False, "passenger": True},
            {"seat_heating_driver": None, "seat_heating_passenger": 3})
        result = ex.run("r = turn_off_unoccupied_seat_heating()\nrespond(r['message'])")
        self.assertEqual(self._seat_sets(ws), [{"level": 0, "seat_zone": "DRIVER"}])
        self.assertIn("unavailable", result.response_text)
        self.assertEqual(
            ws.scratchpad["facts"]["last_helper_report"]["unavailable_levels"],
            ["DRIVER"],
        )

    def _reading_light_ws(self, occupancy):
        tools = {
            "get_seats_occupancy": tool_schema("get_seats_occupancy", {}),
            "set_reading_light": tool_schema(
                "set_reading_light",
                {"position": {"type": "string"}, "on": {"type": "boolean"}},
                required=["position", "on"],
            ),
        }
        responses = {
            "get_seats_occupancy": ("SUCCESS", {"seats_occupied": occupancy}),
            "set_reading_light": ("SUCCESS", {}),
        }
        return self.make(responses, tools)

    def _reading_light_sets(self, ws):
        return [
            call["arguments"]
            for batch in ws.bridge.requests
            for call in batch
            if call["tool_name"] == "set_reading_light"
        ]

    def test_occupied_reading_lights_use_canonical_positions_once(self):
        ws, ex = self._reading_light_ws(
            {
                "driver": True,
                "passenger": False,
                "driver_rear": True,
                "left_rear": True,
                "passenger_rear": False,
                "right_rear": False,
            }
        )

        ex.run("set_occupied_reading_lights(on=True)")

        self.assertEqual(
            self._reading_light_sets(ws),
            [
                {"position": "DRIVER", "on": True},
                {"position": "DRIVER_REAR", "on": True},
            ],
        )

    def test_occupied_reading_lights_can_exclude_rear(self):
        ws, ex = self._reading_light_ws(
            {
                "driver": False,
                "passenger": True,
                "driver_rear": True,
                "passenger_rear": True,
            }
        )

        ex.run("set_occupied_reading_lights(on=False, include_rear=False)")

        self.assertEqual(
            self._reading_light_sets(ws),
            [{"position": "PASSENGER", "on": False}],
        )

    def test_occupied_reading_lights_no_occupied_seats_no_setter(self):
        ws, ex = self._reading_light_ws(
            {
                "driver": False,
                "passenger": False,
                "driver_rear": False,
                "passenger_rear": False,
            }
        )

        result = ex.run(
            "set_occupied_reading_lights(on=True)\n"
            "respond(scratchpad['facts'].get('last_helper_message', ''))"
        )

        self.assertEqual(self._reading_light_sets(ws), [])
        self.assertIn("No occupied seats", result.response_text)


    # --- 8. route-presentation narration (policy 022/021) ----------------

    def _routes(self, toll=False, n=3, alternative_toll=False):
        routes = [{"route_id": "R_to", "alias": ["fastest", "first", "shortest"],
                   "includes_toll": toll, "name_via": "K1"}]
        for i in range(2, n + 1):
            routes.append({"route_id": f"R{i}", "alias": [f"r{i}"],
                           "includes_toll": alternative_toll and i == 2,
                           "name_via": f"K{i}"})
        return routes

    def test_narration_fastest_with_alternatives(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(), "R_to")
        self.assertIn("fastest route", text)
        self.assertIn("other alternative routes", text)
        self.assertNotIn("Would you like", text)
        self.assertNotIn("toll", text)

    def test_narration_includes_tolls_when_present(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(toll=True), "R_to")
        self.assertIn("toll roads", text)

    def test_narration_discloses_toll_alternative(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(
            self._routes(alternative_toll=True),
            "R_to",
            stage="search",
        )
        self.assertIn("One other option uses toll roads", text)

    def test_navigation_narration_discloses_toll_alternative(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(
            self._routes(alternative_toll=True),
            "R_to",
            stage="navigate",
        )
        self.assertIn("One other option uses toll roads", text)

    def test_narration_single_route_no_alternatives_clause(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(n=1), "R_to")
        self.assertIn("fastest route", text)
        self.assertNotIn("other option", text)

    def test_narration_selected_non_default_route_uses_via_name(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(
            self._routes(),
            "R2",
            stage="navigate",
        )
        self.assertIn("selected route via K2", text)
        self.assertNotIn("fastest route", text)

    def test_narration_route_id_selection_keeps_fastest_fact(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(
            self._routes(),
            "R_to",
            stage="navigate",
            selector={"route_id": "R_to"},
        )
        self.assertIn("fastest route", text)
        self.assertIn("K1", text)
        self.assertNotIn("selected route", text)

    def test_narration_route_id_selection_non_default_stays_selected(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(
            self._routes(),
            "R2",
            stage="navigate",
            selector={"route_id": "R2"},
        )
        self.assertIn("selected route via K2", text)
        self.assertNotIn("fastest route", text)

    def test_replace_waypoint_appends_narration_to_response(self):
        nav = {"navigation_active": True, "waypoints_id": ["A", "B", "C", "D"],
               "details": {"waypoints": [{"id": x, "name": x} for x in ["A", "B", "C", "D"]]}}
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state", {"detailed_information": {"type": "boolean"}}),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}}),
            "navigation_replace_one_waypoint": tool_schema(
                "navigation_replace_one_waypoint",
                {"waypoint_id_to_replace": {"type": "string"}, "new_waypoint_id": {"type": "string"},
                 "route_id_leading_to_new_waypoint": {"type": "string"},
                 "route_id_leading_away_from_new_waypoint": {"type": "string"}}),
        }
        responses = {
            "get_current_navigation_state": ("SUCCESS", nav),
            "get_routes_from_start_to_destination": ("SUCCESS", {"routes": self._routes()}),
            "navigation_replace_one_waypoint": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        result = ex.run(
            "navigation_replace_one_waypoint(waypoint_id_to_replace='B', new_waypoint_id='NEW', "
            "route_id_leading_to_new_waypoint='R_stale', route_id_leading_away_from_new_waypoint='R_stale')\n"
            "respond('Replaced B with NEW.')"
        )
        self.assertIn("Replaced B with NEW", result.response_text)
        self.assertIn("fastest route", result.response_text)
        # Stale route id was corrected to the derived valid one.
        args = self._emitted(ws, "navigation_replace_one_waypoint")
        self.assertEqual(args["route_id_leading_to_new_waypoint"], "R_to")

    def test_replace_waypoint_appends_both_new_segment_narrations(self):
        nav = {
            "navigation_active": True,
            "waypoints_id": ["loc_a", "loc_b", "loc_old", "loc_d"],
            "details": {
                "waypoints": [
                    {"id": "loc_a", "name": "Aix"},
                    {"id": "loc_b", "name": "Budapest"},
                    {"id": "loc_old", "name": "Rome"},
                    {"id": "loc_d", "name": "Luxembourg"},
                ]
            },
        }
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}},
            ),
            "navigation_replace_one_waypoint": tool_schema(
                "navigation_replace_one_waypoint",
                {
                    "waypoint_id_to_replace": {"type": "string"},
                    "new_waypoint_id": {"type": "string"},
                    "route_id_leading_to_new_waypoint": {"type": "string"},
                    "route_id_leading_away_from_new_waypoint": {"type": "string"},
                },
            ),
        }
        responses = {
            "get_current_navigation_state": ("SUCCESS", nav),
            "get_routes_from_start_to_destination": [
                (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "R_bud_col",
                                "start_id": "loc_b",
                                "destination_id": "loc_col",
                                "name_via": "B441",
                                "alias": ["fastest", "shortest", "first"],
                            },
                            {
                                "route_id": "R_bud_col_2",
                                "start_id": "loc_b",
                                "destination_id": "loc_col",
                                "name_via": "A93",
                                "alias": ["second"],
                            },
                        ]
                    },
                ),
                (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "R_col_lux",
                                "start_id": "loc_col",
                                "destination_id": "loc_d",
                                "name_via": "L397, L496, L686",
                                "alias": ["fastest", "shortest", "first"],
                            },
                            {
                                "route_id": "R_col_lux_2",
                                "start_id": "loc_col",
                                "destination_id": "loc_d",
                                "name_via": "A4",
                                "alias": ["second"],
                                "includes_toll": True,
                            },
                        ]
                    },
                ),
            ],
            "navigation_replace_one_waypoint": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "navigation_replace_one_waypoint("
            "waypoint_id_to_replace='loc_old', new_waypoint_id='loc_col', "
            "route_id_leading_to_new_waypoint='stale_1', "
            "route_id_leading_away_from_new_waypoint='stale_2')\n"
            "respond('Rome has been replaced with Cologne.')"
        )

        args = self._emitted(ws, "navigation_replace_one_waypoint")
        self.assertEqual(args["route_id_leading_to_new_waypoint"], "R_bud_col")
        self.assertEqual(args["route_id_leading_away_from_new_waypoint"], "R_col_lux")
        self.assertIn("segment to the replacement waypoint", result.response_text)
        self.assertIn("B441", result.response_text)
        self.assertIn("segment after the replacement waypoint", result.response_text)
        self.assertIn("L397, L496, L686", result.response_text)
        self.assertIn("alternative routes for either new segment", result.response_text)

    def test_narration_skipped_when_alternatives_already_offered(self):
        ws, ex = self.make({}, {})
        ws.scratchpad["facts"]["pending_route_narration"] = "I took the fastest route — there are 2 other options."
        msg = "Set. I took the fastest route; there are 2 other options if you want."
        result = ex.run(f"respond({msg!r})")
        self.assertEqual(result.response_text, msg)  # model already offered alternatives

    def test_narration_appended_when_alternatives_missing(self):
        ws, ex = self.make({}, {})
        ws.scratchpad["facts"]["pending_route_narration"] = (
            "I took the fastest route — there are 2 other options if you'd like details.")
        result = ex.run("respond('Destination updated using the fastest route.')")
        self.assertIn("other options", result.response_text)  # appended despite 'fastest' present

    def test_navigation_narration_overwrites_stale_search_narration(self):
        ws, ex = self.make({}, {})
        ws.scratchpad["facts"]["pending_route_narration"] = {
            "text": "The fastest route. There are 2 other options. Would you like details?",
            "offers_alternatives": True,
            "stage": "search",
        }
        ws._store_route_narration(self._routes(), "R2", stage="navigate")
        result = ex.run("respond('Destination changed via K2.')")
        self.assertIn("selected route via K2", result.response_text)
        self.assertNotIn("fastest route", result.response_text)

    def test_pending_narration_cleared_on_new_user_turn(self):
        ws, ex = self.make({}, {})
        ws.scratchpad["facts"]["pending_route_narration"] = "I took the fastest route."
        ws.observe_user("anything new")
        self.assertNotIn("pending_route_narration", ws.scratchpad["facts"])


    # --- 9. reactive missing-capability (live membership, no catalog diff) ---

    def test_missing_tool_blocked_by_live_membership(self):
        # Only set_fan_speed is available this task; calling a tool absent from
        # the per-task list is blocked reactively — no comparison to a catalog.
        ws, ex = self.make(
            {},
            {"set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}})},
        )
        result = ex.run("open_close_window(window='DRIVER', percentage=50)")
        self.assertEqual(ws.bridge.requests, [])  # invalid call never emitted
        self.assertIn("open_close_window", result.response_text)
        self.assertIn("unavailable", result.response_text.lower())

    def test_missing_param_blocked_by_live_schema(self):
        # lightcolor is not in THIS task's set_ambient_lights schema -> blocked
        # by checking the live schema, not by diffing the original schema.
        ws, ex = self.make(
            {},
            {"set_ambient_lights": tool_schema("set_ambient_lights", {"on": {"type": "boolean"}})},
        )
        result = ex.run("set_ambient_lights(on=True, lightcolor='BROWN')")
        self.assertEqual(ws.bridge.requests, [])
        self.assertIn("lightcolor", result.response_text)

    def test_available_tool_with_valid_args_is_emitted(self):
        ws, ex = self.make(
            {"set_fan_speed": ("SUCCESS", {})},
            {"set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}})},
        )
        ex.run("set_fan_speed(level=3)")
        self.assertEqual(len(ws.bridge.requests), 1)  # valid call passes through


    # --- 10. introspection coverage for every workspace helper ----------

    def test_signature_and_describe_cover_all_helpers(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import (
            CoroutineWorkspace, WORKSPACE_HELPER_NAMES)
        ws = CoroutineWorkspace(None)
        for name in WORKSPACE_HELPER_NAMES:
            sig = ws.tool_signature(name)          # must not raise
            self.assertIn(name, sig)
            desc = ws.describe_tool(name)           # must not raise
            self.assertEqual(desc["name"], name)


    # --- 11. facts-vs-intention refactor (2026-06-21) --------------------

    def test_batch_helper_keys_hoisted_to_envelope(self):
        # #6: a batched helper's non-reserved keys are readable on the envelope.
        tools = {"get_contact_id_by_contact_name": tool_schema(
            "get_contact_id_by_contact_name",
            {"contact_last_name": {"type": "string"}})}
        responses = {"get_contact_id_by_contact_name": ("SUCCESS", {
            "matches": {"con_1": "Nathan Scott", "con_2": "Helen Scott"}})}
        ws, ex = self.make(responses, tools)
        result = ex.run(
            "res = batch([('get_contact_id_by_contact_name', {'contact_last_name': 'Scott'})])\n"
            "item = result_by_tool(res, 'get_contact_id_by_contact_name')\n"
            "respond(','.join(item['contact_ids']))"  # hoisted, not under ['result']
        )
        self.assertEqual(result.response_text, "con_1,con_2")

    def test_batch_envelope_reserved_keys_protected(self):
        # #6: reserved envelope keys are the ones never hoisted from a helper.
        from track_1_agent_coroutine_under_test.coroutine_repl import (
            _RESERVED_BATCH_ENVELOPE_KEYS)
        self.assertEqual(
            set(_RESERVED_BATCH_ENVELOPE_KEYS),
            {"status", "tool_name", "tool_call_id", "result"})

    def test_contact_search_normalizes_matches_map(self):
        # #5: {contact_id: name} map becomes contact_ids/contacts/by_id.
        tools = {"get_contact_id_by_contact_name": tool_schema(
            "get_contact_id_by_contact_name",
            {"contact_last_name": {"type": "string"}})}
        responses = {"get_contact_id_by_contact_name": ("SUCCESS", {
            "matches": {"con_1139": "Nathan Scott", "con_2347": "Mia Scott"}})}
        ws, ex = self.make(responses, tools)
        result = ex.run(
            "lk = get_contact_id_by_contact_name(contact_last_name='Scott')\n"
            "respond(str(lk['contact_ids']) + '|' + lk['by_id']['con_1139']['display_name'])"
        )
        self.assertEqual(
            result.response_text, "['con_1139', 'con_2347']|Nathan Scott")

    def test_contact_search_exposes_unique_intersection_with_previous_lookup(self):
        tools = {"get_contact_id_by_contact_name": tool_schema(
            "get_contact_id_by_contact_name",
            {"contact_first_name": {"type": "string"}})}
        responses = {"get_contact_id_by_contact_name": ("SUCCESS", {
            "matches": {
                "con_7150": "Nathan Carter",
                "con_1139": "Nathan Scott",
            }})}
        ws, ex = self.make(responses, tools)
        ws.entities["last_contact_lookup"] = {
            "query": {"contact_last_name": "Scott"},
            "contact_ids": ["con_1139", "con_1501"],
            "contacts": [],
        }
        result = ex.run(
            "lk = get_contact_id_by_contact_name(contact_first_name='Nathan')\n"
            "respond(lk['unique_intersection_with_previous_contact_id'])"
        )
        self.assertEqual(result.response_text, "con_1139")

    def test_contact_search_exposes_unique_calendar_attendee_intersection(self):
        tools = {
            "get_entries_from_calendar": tool_schema(
                "get_entries_from_calendar",
                {"month": {"type": "integer"}, "day": {"type": "integer"}},
                required=["month", "day"],
            ),
            "get_contact_id_by_contact_name": tool_schema(
                "get_contact_id_by_contact_name",
                {"contact_first_name": {"type": "string"}},
            ),
        }
        responses = {
            "get_entries_from_calendar": (
                "SUCCESS",
                {
                    "meetings": [
                        {
                            "start": {"hour": "13", "minute": "30"},
                            "duration": "60min",
                            "location": "Frankfurt",
                            "attendees": ["con_4970", "con_8656"],
                            "topic": "Risk Management",
                        }
                    ]
                },
            ),
            "get_contact_id_by_contact_name": (
                "SUCCESS",
                {
                    "matches": {
                        "con_2288": "tina campbell",
                        "con_1977": "tina brown",
                        "con_4970": "tina phillips",
                    }
                },
            ),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "get_entries_from_calendar(month=8, day=15)\n"
            "lk = get_contact_id_by_contact_name(\n"
            "    contact_first_name='Tina',\n"
            "    constrain_to_recent_calendar_attendees=True,\n"
            ")\n"
            "respond(lk['unique_calendar_attendee_contact_id'] + '|' + "
            "        ','.join(lk['contact_ids']) + '|' + "
            "        ','.join(lk['unconstrained_contact_ids']))"
        )

        self.assertEqual(
            result.response_text,
            "con_4970|con_4970|con_2288,con_1977,con_4970",
        )
        self.assertEqual(
            ws.entities["last_contact_lookup"]["intersection_with_calendar_attendee_ids"],
            ["con_4970"],
        )
        self.assertEqual(ws.entities["last_contact_lookup"]["contact_ids"], ["con_4970"])
        self.assertTrue(
            ws.entities["last_contact_lookup"]["constrained_to_calendar_attendees"]
        )

    def test_contact_search_ranks_unique_calendar_attendee_first(self):
        tools = {
            "get_entries_from_calendar": tool_schema(
                "get_entries_from_calendar",
                {"month": {"type": "integer"}, "day": {"type": "integer"}},
                required=["month", "day"],
            ),
            "get_contact_id_by_contact_name": tool_schema(
                "get_contact_id_by_contact_name",
                {"contact_first_name": {"type": "string"}},
            ),
        }
        responses = {
            "get_entries_from_calendar": (
                "SUCCESS",
                {
                    "meetings": [
                        {
                            "start": {"hour": "13", "minute": "30"},
                            "attendees": ["con_4970", "con_8656"],
                        }
                    ]
                },
            ),
            "get_contact_id_by_contact_name": (
                "SUCCESS",
                {
                    "matches": {
                        "con_2288": "tina campbell",
                        "con_1977": "tina brown",
                        "con_4970": "tina phillips",
                    }
                },
            ),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "get_entries_from_calendar(month=8, day=15)\n"
            "lk = get_contact_id_by_contact_name(contact_first_name='Tina')\n"
            "respond(','.join(lk['contact_ids']) + '|' + "
            "        ','.join(lk['unconstrained_contact_ids']) + '|' + "
            "        str(lk['calendar_attendee_ranked_first']))"
        )

        self.assertEqual(
            result.response_text,
            "con_4970,con_2288,con_1977|con_2288,con_1977,con_4970|True",
        )
        self.assertEqual(ws.entities["last_contact_lookup"]["contact_ids"][0], "con_4970")

    def test_contact_search_keeps_calendar_attendee_intersection_ambiguous(self):
        tools = {
            "get_entries_from_calendar": tool_schema(
                "get_entries_from_calendar",
                {"month": {"type": "integer"}, "day": {"type": "integer"}},
                required=["month", "day"],
            ),
            "get_contact_id_by_contact_name": tool_schema(
                "get_contact_id_by_contact_name",
                {"contact_first_name": {"type": "string"}},
            ),
        }
        responses = {
            "get_entries_from_calendar": (
                "SUCCESS",
                {
                    "meetings": [
                        {
                            "start": {"hour": "13", "minute": "30"},
                            "attendees": ["con_4970", "con_1977"],
                        }
                    ]
                },
            ),
            "get_contact_id_by_contact_name": (
                "SUCCESS",
                {
                    "matches": {
                        "con_1977": "tina brown",
                        "con_4970": "tina phillips",
                        "con_7657": "tina adams",
                    }
                },
            ),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "get_entries_from_calendar(month=8, day=15)\n"
            "lk = get_contact_id_by_contact_name(contact_first_name='Tina')\n"
            "respond(str(lk.get('unique_calendar_attendee_contact_id')) + '|' + "
            "        ','.join(lk['intersection_with_calendar_attendee_ids']))"
        )

        self.assertEqual(result.response_text, "None|con_1977,con_4970")

    def test_send_email_confirmation_uses_unique_contact_intersection_email(self):
        tools = {"send_email": tool_schema(
            "send_email",
            {
                "email_addresses": {"type": "array", "items": {"type": "string"}},
                "content_message": {"type": "string"},
            },
            required=["email_addresses", "content_message"],
            description="REQUIRES_CONFIRMATION Send an email.",
        )}
        ws, ex = self.make({"send_email": ("SUCCESS", {})}, tools)
        ws.entities["last_unique_contact_intersection_id"] = "con_1139"
        ws.entities["contacts_by_id"] = {
            "con_1139": {
                "contact_id": "con_1139",
                "display_name": "Nathan Scott",
                "email": "nathan.scott@example.com",
            },
            "con_7150": {
                "contact_id": "con_7150",
                "display_name": "Nathan Carter",
                "email": "nathan.carter@example.com",
            },
        }
        result = ex.run(
            "send_email(email_addresses=['nathan.carter@example.com'], "
            "content_message='Here are the Scott contacts.')"
        )
        self.assertIn("nathan.scott@example.com", result.response_text)
        pending = ws.scratchpad["facts"]["pending_confirmation"]
        self.assertEqual(
            pending["on_confirm_calls"][0]["arguments"]["email_addresses"],
            ["nathan.scott@example.com"],
        )

    def test_send_email_confirmation_keeps_email_without_unique_intersection(self):
        tools = {"send_email": tool_schema(
            "send_email",
            {
                "email_addresses": {"type": "array", "items": {"type": "string"}},
                "content_message": {"type": "string"},
            },
            required=["email_addresses", "content_message"],
            description="REQUIRES_CONFIRMATION Send an email.",
        )}
        ws, ex = self.make({"send_email": ("SUCCESS", {})}, tools)
        ws.entities["contacts_by_id"] = {
            "con_7150": {
                "contact_id": "con_7150",
                "display_name": "Nathan Carter",
                "email": "nathan.carter@example.com",
            },
        }
        result = ex.run(
            "send_email(email_addresses=['nathan.carter@example.com'], "
            "content_message='Hello.')"
        )
        self.assertIn("nathan.carter@example.com", result.response_text)
        self.assertNotIn("contact_recipient_guard", ws.scratchpad["gates"])

    def test_send_contact_details_to_contact_keeps_recipient_and_subject_separate(self):
        tools = {
            "get_contact_information": tool_schema(
                "get_contact_information",
                {"contact_ids": {"type": "array", "items": {"type": "string"}}},
                required=["contact_ids"],
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        contacts_payload = {
            "con_recipient": {
                "email": "recipient@example.com",
                "name": {"first_name": "Recipient", "last_name": "Person"},
            },
            "con_subject": {
                "email": "subject@example.com",
                "phone_number": "+15550100",
                "name": {"first_name": "Subject", "last_name": "Person"},
            },
        }
        ws, ex = self.make(
            {"get_contact_information": ("SUCCESS", contacts_payload)},
            tools,
        )

        result = ex.run(
            "send_contact_details_to_contact("
            "recipient_contact_id='con_recipient', "
            "subject_contact_id='con_subject', "
            "required_fields=['phone_number'])"
        )

        self.assertIn("recipient@example.com", result.response_text)
        pending = ws.scratchpad["facts"]["pending_confirmation"]
        call = pending["on_confirm_calls"][0]
        self.assertEqual(call["arguments"]["email_addresses"], ["recipient@example.com"])
        self.assertIn("+15550100", call["arguments"]["content_message"])
        self.assertNotIn("subject@example.com", call["arguments"]["email_addresses"])

    def test_email_confirmation_uses_explicit_contact_roles_after_last_contacts_drift(self):
        tools = {
            "get_contact_information": tool_schema(
                "get_contact_information",
                {"contact_ids": {"type": "array", "items": {"type": "string"}}},
                required=["contact_ids"],
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        contacts_payload = {
            "con_recipient": {
                "email": "recipient@example.com",
                "name": {"first_name": "Recipient", "last_name": "Person"},
            },
            "con_subject": {
                "email": "subject@example.com",
                "phone_number": "+15550100",
                "name": {"first_name": "Subject", "last_name": "Person"},
            },
        }
        ws, ex = self.make(
            {
                "get_contact_information": ("SUCCESS", contacts_payload),
                "send_email": ("SUCCESS", {}),
            },
            tools,
        )
        ws.observe_user("Send one contact's phone number to another contact.")

        result = ex.run(
            "recipient = get_contact_details("
            "'con_recipient', required_fields=['email'], role='email_recipient')\n"
            "subject = get_contact_details("
            "'con_subject', required_fields=['phone_number'], "
            "role='contact_details_subject')\n"
            "send_email("
            "email_addresses=[subject['email']], "
            "content_message='phone number: ' + subject['phone_number'])"
        )

        self.assertIn("recipient@example.com", result.response_text)
        pending = ws.scratchpad["facts"]["pending_confirmation"]
        call = pending["on_confirm_calls"][0]
        self.assertEqual(call["arguments"]["email_addresses"], ["recipient@example.com"])
        self.assertEqual(
            ws.scratchpad["gates"]["contact_recipient_role_guard"]["status"],
            "REPAIRED",
        )

    def test_email_confirmation_does_not_repair_without_subject_role(self):
        tools = {
            "get_contact_information": tool_schema(
                "get_contact_information",
                {"contact_ids": {"type": "array", "items": {"type": "string"}}},
                required=["contact_ids"],
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        contacts_payload = {
            "con_recipient": {"email": "recipient@example.com"},
            "con_other": {"email": "other@example.com"},
        }
        ws, ex = self.make(
            {
                "get_contact_information": ("SUCCESS", contacts_payload),
                "send_email": ("SUCCESS", {}),
            },
            tools,
        )
        ws.observe_user("Email the grounded contact.")

        result = ex.run(
            "get_contact_details("
            "'con_recipient', required_fields=['email'], role='email_recipient')\n"
            "other = get_contact_details('con_other', required_fields=['email'])\n"
            "send_email(email_addresses=[other['email']], content_message='Hello.')"
        )

        self.assertIn("other@example.com", result.response_text)
        pending = ws.scratchpad["facts"]["pending_confirmation"]
        call = pending["on_confirm_calls"][0]
        self.assertEqual(call["arguments"]["email_addresses"], ["other@example.com"])
        self.assertNotIn("contact_recipient_role_guard", ws.scratchpad["gates"])

    def test_email_confirmation_reports_same_turn_charging_search_result(self):
        tools = {
            "search_poi_along_the_route": tool_schema(
                "search_poi_along_the_route",
                {
                    "route_id": {"type": "string"},
                    "category_poi": {"type": "string"},
                    "at_kilometer": {"type": "number"},
                },
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        responses = {
            "search_poi_along_the_route": (
                "SUCCESS",
                {
                    "pois_found_along_route": [
                        {
                            "id": "poi_cha_pre",
                            "name": "PRE",
                            "category": "charging_stations",
                        }
                    ]
                },
            ),
            "send_email": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user(
            "Search for a charging station around 150 km and email Grace my travel time."
        )

        result = ex.run(
            "search_poi_along_the_route("
            "route_id='route_1', category_poi='charging_stations', at_kilometer=150)\n"
            "send_email("
            "email_addresses=['grace@example.com'], "
            "content_message='My trip will take 4h 41m.')"
        )

        self.assertIn("I found PRE near the 150-km point.", result.response_text)
        self.assertIn("This action requires confirmation", result.response_text)
        pending_call = ws.scratchpad["facts"]["pending_confirmation"]["on_confirm_calls"][0]
        content = pending_call["arguments"]["content_message"]
        self.assertIn("My trip will take 4h 41m.", content)
        self.assertIn("Charging station: PRE near the 150-km point.", content)
        self.assertEqual(
            ws.scratchpad["gates"]["charging_email_detail_guard"]["status"],
            "REPAIRED",
        )
        self.assertEqual(
            pending_call["tool_name"],
            "send_email",
        )

    def test_email_confirmation_does_not_add_charging_details_to_unrelated_email(self):
        tools = {
            "search_poi_along_the_route": tool_schema(
                "search_poi_along_the_route",
                {
                    "route_id": {"type": "string"},
                    "category_poi": {"type": "string"},
                    "at_kilometer": {"type": "number"},
                },
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        responses = {
            "search_poi_along_the_route": (
                "SUCCESS",
                {
                    "pois_found_along_route": [
                        {
                            "id": "poi_cha_pre",
                            "name": "PRE",
                            "category": "charging_stations",
                        }
                    ]
                },
            ),
            "send_email": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user("Search for a charging station, then email Grace.")

        ex.run(
            "search_poi_along_the_route("
            "route_id='route_1', category_poi='charging_stations', at_kilometer=150)\n"
            "send_email("
            "email_addresses=['grace@example.com'], "
            "content_message='See you soon.')"
        )

        pending_call = ws.scratchpad["facts"]["pending_confirmation"]["on_confirm_calls"][0]
        self.assertEqual(
            pending_call["arguments"]["content_message"],
            "See you soon.",
        )
        self.assertNotIn("charging_email_detail_guard", ws.scratchpad["gates"])

    def test_email_confirmation_blocks_placeholder_time_units(self):
        tools = {
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        ws, ex = self.make({"send_email": ("SUCCESS", {})}, tools)
        ws.observe_user("Email Grace the travel time.")

        result = ex.run(
            "send_email("
            "email_addresses=['grace@example.com'], "
            "content_message='Travel time to Budapest is Noneh Nonem.')"
        )

        self.assertIn("can't request confirmation yet", result.response_text)
        self.assertIn("send_email[0].content_message", result.response_text)
        self.assertNotIn("pending_confirmation", ws.scratchpad["facts"])

    def test_applied_long_route_blocks_email_until_charging_status_read(self):
        tools = {
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
            "set_new_navigation": self._nav_schema(),
            "get_charging_specs_and_status": tool_schema(
                "get_charging_specs_and_status",
                {},
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        responses = {
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {
                    "routes": [
                        {
                            "route_id": "route_long",
                            "distance": "368.81 km",
                            "duration_hours": 4,
                            "duration_minutes": 41,
                        }
                    ]
                },
            ),
            "set_new_navigation": ("SUCCESS", {}),
            "send_email": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "get_routes_from_start_to_destination("
            "start_id='loc_home_1', destination_id='loc_budapest')\n"
            "set_new_navigation(route_ids=['route_long'])\n"
            "send_email("
            "email_addresses=['grace@example.com'], "
            "content_message='My trip will take 4h 41m.')\n"
            "respond('I need to check the charging status before asking to send the email.')"
        )

        self.assertIsNone(result.error)
        self.assertEqual(
            result.response_text,
            "I need to check the charging status before asking to send the email.",
        )
        guard = ws.scratchpad["gates"]["long_route_email_charging_fact_guard"]
        self.assertEqual(guard["status"], "NEEDS_MORE_FACTS")
        self.assertEqual(guard["route_id"], "route_long")
        self.assertEqual(guard["route_distance_km"], 368.81)
        self.assertNotIn("last_routes", ws.scratchpad["entities"])
        self.assertEqual(
            ws.scratchpad["entities"]["active_route_records"][0]["route_id"],
            "route_long",
        )
        emitted = [
            call["tool_name"] for request in ws.bridge.requests for call in request
        ]
        self.assertEqual(
            emitted,
            ["get_routes_from_start_to_destination", "set_new_navigation"],
        )

    def test_post_charge_long_route_email_requires_distance_by_soc(self):
        tools = {
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
            "get_charging_specs_and_status": tool_schema(
                "get_charging_specs_and_status",
                {},
            ),
            "calculate_charging_time_by_soc": tool_schema(
                "calculate_charging_time_by_soc",
                {
                    "charging_station_id": {"type": "string"},
                    "charging_station_plug_id": {"type": "string"},
                    "start_state_of_charge": {"type": "number"},
                    "target_state_of_charge": {"type": "number"},
                },
            ),
            "get_distance_by_soc": tool_schema(
                "get_distance_by_soc",
                {
                    "initial_state_of_charge": {"type": "number"},
                    "final_state_of_charge": {"type": "number"},
                },
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        responses = {
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {
                    "routes": [
                        {
                            "route_id": "route_mad_mon",
                            "distance_km": 1168.11,
                            "duration_hours": 14,
                            "duration_minutes": 38,
                            "alias": ["fastest"],
                        }
                    ]
                },
            ),
            "get_charging_specs_and_status": (
                "SUCCESS",
                {"state_of_charge": 70, "remaining_range": "443.0km"},
            ),
            "calculate_charging_time_by_soc": (
                "SUCCESS",
                {"time_from_70_until_100_percent_soc": "10min"},
            ),
            "send_email": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "get_route_options(start_id='loc_mad_180891', destination_id='loc_mon_279370')\n"
            "get_charging_specs_and_status()\n"
            "calculate_charging_time_by_soc("
            "charging_station_id='poi_repsol', "
            "charging_station_plug_id='plug_repsol', "
            "start_state_of_charge=70, "
            "target_state_of_charge=100)\n"
            "send_email("
            "email_addresses=['rachel@example.com'], "
            "content_message='Route and charging details.')\n"
            "respond('I need the official post-charge range before asking to send the email.')"
        )

        self.assertEqual(
            result.response_text,
            "I need the official post-charge range before asking to send the email.",
        )
        guard = ws.scratchpad["gates"]["post_charge_email_distance_fact_guard"]
        self.assertEqual(guard["status"], "NEEDS_MORE_FACTS")
        self.assertEqual(guard["target_state_of_charge"], 100)
        self.assertEqual(guard["route_distance_km"], 1168.11)
        emitted = [
            call["tool_name"] for request in ws.bridge.requests for call in request
        ]
        self.assertEqual(
            emitted,
            [
                "get_routes_from_start_to_destination",
                "get_charging_specs_and_status",
                "calculate_charging_time_by_soc",
            ],
        )

    def test_post_charge_long_route_email_allows_after_distance_by_soc(self):
        tools = {
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
            "get_charging_specs_and_status": tool_schema(
                "get_charging_specs_and_status",
                {},
            ),
            "calculate_charging_time_by_soc": tool_schema(
                "calculate_charging_time_by_soc",
                {
                    "charging_station_id": {"type": "string"},
                    "charging_station_plug_id": {"type": "string"},
                    "start_state_of_charge": {"type": "number"},
                    "target_state_of_charge": {"type": "number"},
                },
            ),
            "get_distance_by_soc": tool_schema(
                "get_distance_by_soc",
                {
                    "initial_state_of_charge": {"type": "number"},
                    "final_state_of_charge": {"type": "number"},
                },
            ),
            "send_email": tool_schema(
                "send_email",
                {
                    "email_addresses": {"type": "array", "items": {"type": "string"}},
                    "content_message": {"type": "string"},
                },
                required=["email_addresses", "content_message"],
                description="REQUIRES_CONFIRMATION Send an email.",
            ),
        }
        responses = {
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {
                    "routes": [
                        {
                            "route_id": "route_mad_mon",
                            "distance_km": 1168.11,
                            "duration_hours": 14,
                            "duration_minutes": 38,
                            "alias": ["fastest"],
                        }
                    ]
                },
            ),
            "get_charging_specs_and_status": (
                "SUCCESS",
                {"state_of_charge": 70, "remaining_range": "443.0km"},
            ),
            "calculate_charging_time_by_soc": (
                "SUCCESS",
                {"time_from_70_until_100_percent_soc": "10min"},
            ),
            "get_distance_by_soc": (
                "SUCCESS",
                {"distance_km_for_100.0_until_0.0_percent_soc": "507.0km"},
            ),
            "send_email": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)

        result = ex.run(
            "get_route_options(start_id='loc_mad_180891', destination_id='loc_mon_279370')\n"
            "get_charging_specs_and_status()\n"
            "calculate_charging_time_by_soc("
            "charging_station_id='poi_repsol', "
            "charging_station_plug_id='plug_repsol', "
            "start_state_of_charge=70, "
            "target_state_of_charge=100)\n"
            "get_distance_by_soc(initial_state_of_charge=100, final_state_of_charge=0)\n"
            "send_email("
            "email_addresses=['rachel@example.com'], "
            "content_message='Route and charging details.')"
        )

        self.assertIn("This action requires confirmation", result.response_text)
        self.assertNotIn("post_charge_email_distance_fact_guard", ws.scratchpad["gates"])
        self.assertEqual(
            ws.scratchpad["entities"]["last_distance_by_soc"]["distance_km"],
            507,
        )
        pending = ws.scratchpad["facts"]["pending_confirmation"]
        self.assertEqual(pending["on_confirm_calls"][0]["tool_name"], "send_email")

    def test_next_calendar_entry_uses_policy_day_and_current_time(self):
        tools = {"get_entries_from_calendar": tool_schema(
            "get_entries_from_calendar",
            {"month": {"type": "integer"}, "day": {"type": "integer"}},
            required=["month", "day"])}
        responses = {"get_entries_from_calendar": ("SUCCESS", {
            "date": {"year": 2025, "month": 6, "day": 6},
            "meetings": [
                {
                    "start": {"hour": "13", "minute": "30"},
                    "duration": "30min",
                    "location": "Past",
                },
                {
                    "start": {"hour": "15", "minute": "00"},
                    "duration": "60min",
                    "location": "Berlin",
                },
            ],
        })}
        ws, ex = self.make(responses, tools)
        result = ex.run(
            "calendar = get_next_calendar_entry()\n"
            "respond(calendar['next_entry']['location'])"
        )
        self.assertEqual(result.response_text, "Berlin")
        self.assertEqual(
            ws.bridge.requests[0][0]["arguments"],
            {"month": 6, "day": 6},
        )
        self.assertEqual(ws.entities["next_calendar_entry"]["start_minutes"], 900)

    def test_calendar_tool_output_exposes_24h_display_fields(self):
        tools = {"get_entries_from_calendar": tool_schema(
            "get_entries_from_calendar",
            {"month": {"type": "integer"}, "day": {"type": "integer"}},
            required=["month", "day"])}
        responses = {"get_entries_from_calendar": ("SUCCESS", {
            "date": {"year": 2025, "month": 6, "day": 6},
            "meetings": [
                {
                    "start": {"hour": "13", "minute": "30"},
                    "duration": "60min",
                    "location": "Berlin",
                    "topic": "Customer Feedback",
                },
                {
                    "start": {"hour": "18", "minute": "00"},
                    "duration": "60min",
                    "location": "Mannheim",
                    "topic": "Leadership Development",
                },
            ],
        })}
        ws, ex = self.make(responses, tools)
        result = ex.run(
            "calendar = get_entries_from_calendar(month=6, day=6)\n"
            "entries = calendar['entries']\n"
            "respond(entries[0]['display'] + '|' + entries[1]['start_time_24h'])"
        )
        self.assertEqual(
            result.response_text,
            "13:30 (60min) Customer Feedback at Berlin|18:00",
        )
        self.assertEqual(
            ws.entities["last_calendar"]["entries"][0]["start_time"],
            "13:30",
        )
        self.assertEqual(
            ws.entities["last_calendar"]["entries"][0]["start_minutes"],
            810,
        )

    def test_mutation_failure_kept_per_target(self):
        # #2: one target's success must not clear another target's failure.
        ws, _ = self.make({}, {})
        parsed = [
            {"tool_name": "open_close_window", "status": "FAILURE"},
            {"tool_name": "open_close_window", "status": "SUCCESS"},
        ]
        calls = [
            {"tool_name": "open_close_window",
             "arguments": {"window": "PASSENGER", "percentage": 0}},
            {"tool_name": "open_close_window",
             "arguments": {"window": "DRIVER", "percentage": 0}},
        ]
        ws._record_mutation_outcomes(parsed, calls)
        # The passenger failure survives the driver success.
        self.assertIsNotNone(ws._unacknowledged_mutation_failure_message())

    def test_mutation_failure_cleared_by_same_target_success(self):
        ws, _ = self.make({}, {})
        same = {"window": "PASSENGER", "percentage": 0}
        ws._record_mutation_outcomes(
            [{"tool_name": "open_close_window", "status": "FAILURE"}],
            [{"tool_name": "open_close_window", "arguments": dict(same)}])
        self.assertIsNotNone(ws._unacknowledged_mutation_failure_message())
        ws._record_mutation_outcomes(
            [{"tool_name": "open_close_window", "status": "SUCCESS"}],
            [{"tool_name": "open_close_window", "arguments": dict(same)}])
        self.assertIsNone(ws._unacknowledged_mutation_failure_message())

    def test_mutation_failure_proved_by_state_read(self):
        # #2: a window-position read proving the target is closed clears it.
        ws, _ = self.make({}, {})
        ws._record_mutation_outcomes(
            [{"tool_name": "open_close_window", "status": "FAILURE"}],
            [{"tool_name": "open_close_window",
              "arguments": {"window": "PASSENGER", "percentage": 0}}])
        self.assertIsNotNone(ws._unacknowledged_mutation_failure_message())
        ws._record_mutation_outcomes(
            [{"tool_name": "get_vehicle_window_positions", "status": "SUCCESS",
              "result": {"PASSENGER": 0, "DRIVER": 50}}],
            [{"tool_name": "get_vehicle_window_positions", "arguments": {}}])
        self.assertIsNone(ws._unacknowledged_mutation_failure_message())

    def test_window_proof_does_not_confuse_driver_with_driver_rear(self):
        ws, _ = self.make({}, {})
        ws._record_mutation_outcomes(
            [{"tool_name": "open_close_window", "status": "FAILURE"}],
            [{"tool_name": "open_close_window",
              "arguments": {"window": "DRIVER", "percentage": 0}}],
        )
        ws._record_mutation_outcomes(
            [{"tool_name": "get_vehicle_window_positions", "status": "SUCCESS",
              "result": {
                  "window_driver_position": 50,
                  "window_driver_rear_position": 0,
              }}],
            [{"tool_name": "get_vehicle_window_positions", "arguments": {}}],
        )
        self.assertIsNotNone(ws._unacknowledged_mutation_failure_message())

    def test_route_narration_search_stage_offers_not_claims(self):
        # #4: a pure search must not claim a route was taken.
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(), "R_to", stage="search")
        self.assertIn("Would you like", text)
        self.assertNotIn("Navigation is now using", text)
        self.assertNotIn("This route segment is now using", text)
        self.assertNotIn("I took", text)

    def test_route_narration_navigate_stage_states_action(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(), "R_to", stage="navigate")
        self.assertIn("This route segment is now using", text)
        self.assertNotIn("Navigation is now using", text)
        self.assertNotIn("Would you like", text)

    def test_already_absent_delete_reports_no_deletion(self):
        # ALREADY_ABSENT: absence is not proof of deletion.
        ws, ex = self._nav_edit_ws(
            "navigation_delete_waypoint",
            {"waypoint_id_to_delete": {"type": "string"},
             "route_id_without_waypoint": {"type": "string"}})
        result = ex.run(
            "r = navigation_delete_waypoint(waypoint_id_to_delete='loc_GONE')\n"
            "respond(str(r.get('already_absent')) + '|' + "
            "str(r['result'].get('waypoint_deleted')))"
        )
        self.assertEqual(result.response_text, "True|False")
        self.assertIsNone(self._emitted(ws, "navigation_delete_waypoint"))

    def test_success_helper_does_not_lock_response(self):
        # #3: the model can compose its own message after a successful helper.
        ws, ex = self._seat_ws(
            {"driver": True, "passenger": False},
            {"seat_heating_driver": 0, "seat_heating_passenger": 0})
        result = ex.run(
            "set_occupied_seat_heating(level=2)\n"
            "respond('Driver seat heating set, plus everything else.')"
        )
        # Not locked: the model's composed message wins.
        self.assertEqual(
            result.response_text, "Driver seat heating set, plus everything else.")

    def test_policy_warning_survives_later_helper_message(self):
        tools = {
            "get_temperature_inside_car": tool_schema("get_temperature_inside_car", {}),
            "set_climate_temperature": tool_schema(
                "set_climate_temperature",
                {"seat_zone": {"type": "string"}, "temperature": {"type": "number"}},
            ),
        }
        responses = {
            "get_temperature_inside_car": (
                "SUCCESS",
                {
                    "climate_temperature_driver": 21,
                    "climate_temperature_passenger": 20,
                },
            ),
            "set_climate_temperature": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        result = ex.run(
            "set_climate_temperature_safe(seat_zone='DRIVER', temperature=28)\n"
            "ws._helper_message('The second subgoal is complete.')\n"
            "respond('Everything is complete.')"
        )
        self.assertIn("more than 3 degrees", result.response_text)
        self.assertEqual(
            ws.scratchpad["facts"]["pending_helper_messages"],
            [
                "driver temperature set to 28 degrees Celsius. Heads up, that is more than 3 degrees different from the passenger side.",
                "The second subgoal is complete.",
            ],
        )

    def test_temperature_response_adds_celsius_after_successful_setter(self):
        tools = {
            "set_climate_temperature": tool_schema(
                "set_climate_temperature",
                {"seat_zone": {"type": "string"}, "temperature": {"type": "number"}},
            ),
        }
        ws, ex = self.make({"set_climate_temperature": ("SUCCESS", {})}, tools)

        result = ex.run(
            "set_climate_temperature(seat_zone='ALL_ZONES', temperature=22)\n"
            "respond('Temperature set to 22 degrees for all zones.')"
        )

        self.assertEqual(
            result.response_text,
            "Temperature set to 22 degrees Celsius for all zones.",
        )

    def test_sync_climate_zone_copies_source_values_to_target_zone(self):
        tools = {
            "get_temperature_inside_car": tool_schema("get_temperature_inside_car", {}),
            "get_seat_heating_level": tool_schema("get_seat_heating_level", {}),
            "set_climate_temperature": tool_schema(
                "set_climate_temperature",
                {"seat_zone": {"type": "string"}, "temperature": {"type": "number"}},
            ),
            "set_seat_heating": tool_schema(
                "set_seat_heating",
                {"seat_zone": {"type": "string"}, "level": {"type": "integer"}},
            ),
        }
        responses = {
            "get_temperature_inside_car": (
                "SUCCESS",
                {
                    "climate_temperature_driver": 27,
                    "climate_temperature_passenger": 16,
                },
            ),
            "get_seat_heating_level": (
                "SUCCESS",
                {"seat_heating_driver": 3, "seat_heating_passenger": 1},
            ),
            "set_climate_temperature": ("SUCCESS", {}),
            "set_seat_heating": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ex.run(
            "sync_climate_zone(source_zone='PASSENGER', target_zone='DRIVER')"
        )
        temp_args = self._emitted(ws, "set_climate_temperature")
        heat_args = self._emitted(ws, "set_seat_heating")
        self.assertEqual(temp_args, {"seat_zone": "DRIVER", "temperature": 16.0})
        self.assertEqual(heat_args, {"seat_zone": "DRIVER", "level": 1})

    def test_climate_sync_raw_call_is_not_repaired_from_user_text(self):
        tools = {
            "get_temperature_inside_car": tool_schema("get_temperature_inside_car", {}),
            "get_seat_heating_level": tool_schema("get_seat_heating_level", {}),
            "set_climate_temperature": tool_schema(
                "set_climate_temperature",
                {"seat_zone": {"type": "string"}, "temperature": {"type": "number"}},
            ),
            "set_seat_heating": tool_schema(
                "set_seat_heating",
                {"seat_zone": {"type": "string"}, "level": {"type": "integer"}},
            ),
        }
        responses = {
            "get_temperature_inside_car": (
                "SUCCESS",
                {
                    "climate_temperature_driver": 27,
                    "climate_temperature_passenger": 16,
                },
            ),
            "get_seat_heating_level": (
                "SUCCESS",
                {"seat_heating_driver": 3, "seat_heating_passenger": 1},
            ),
            "set_climate_temperature": ("SUCCESS", {}),
            "set_seat_heating": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user(
            "Could you sync my driver zone climate settings to match the "
            "passenger side? Also sync my driver zone heating settings."
        )
        ex.run(
            "get_temperature_inside_car()\n"
            "set_climate_temperature(seat_zone='PASSENGER', temperature=27)\n"
            "get_seat_heating_level()\n"
            "set_seat_heating(seat_zone='PASSENGER', level=3)"
        )
        temp_args = self._emitted(ws, "set_climate_temperature")
        heat_args = self._emitted(ws, "set_seat_heating")
        self.assertEqual(temp_args, {"seat_zone": "PASSENGER", "temperature": 27})
        self.assertEqual(heat_args, {"seat_zone": "PASSENGER", "level": 3})
        self.assertNotIn("climate_sync_guard", ws.scratchpad["gates"])

    def test_climate_sync_guard_ignores_ambiguous_sync_request(self):
        tools = {
            "get_temperature_inside_car": tool_schema("get_temperature_inside_car", {}),
            "set_climate_temperature": tool_schema(
                "set_climate_temperature",
                {"seat_zone": {"type": "string"}, "temperature": {"type": "number"}},
            ),
        }
        responses = {
            "get_temperature_inside_car": (
                "SUCCESS",
                {
                    "climate_temperature_driver": 27,
                    "climate_temperature_passenger": 16,
                },
            ),
            "set_climate_temperature": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user("Can you sync both sides?")
        ex.run(
            "get_temperature_inside_car()\n"
            "set_climate_temperature(seat_zone='PASSENGER', temperature=27)"
        )
        temp_args = self._emitted(ws, "set_climate_temperature")
        self.assertEqual(temp_args, {"seat_zone": "PASSENGER", "temperature": 27})
        self.assertNotIn("climate_sync_guard", ws.scratchpad["gates"])

    def test_increase_fan_speed_reads_climate_and_applies_one_step(self):
        tools = {
            "get_climate_settings": tool_schema("get_climate_settings", {}),
            "set_fan_speed": tool_schema(
                "set_fan_speed",
                {"level": {"type": "integer"}},
            ),
        }
        responses = {
            "get_climate_settings": ("SUCCESS", {"fan_speed": 2}),
            "set_fan_speed": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ex.run("increase_fan_speed()")
        emitted_tools = [
            call["tool_name"] for batch in ws.bridge.requests for call in batch
        ]
        self.assertEqual(emitted_tools, ["get_climate_settings", "set_fan_speed"])
        self.assertEqual(self._emitted(ws, "set_fan_speed"), {"level": 3})

    def test_increase_fan_speed_unknown_current_aborts_with_lookup_limitation(self):
        tools = {
            "get_climate_settings": tool_schema("get_climate_settings", {}),
            "set_fan_speed": tool_schema(
                "set_fan_speed",
                {"level": {"type": "integer"}},
            ),
        }
        responses = {
            "get_climate_settings": ("SUCCESS", {"fan_speed": "unknown"}),
            "set_fan_speed": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        result = ex.run("increase_fan_speed(steps=2)")

        self.assertIsNotNone(result.response_text)
        self.assertIn("can't increase the fan speed by 2 levels", result.response_text)
        self.assertIn("looked it up", result.response_text)
        self.assertIn("did not provide the current fan speed", result.response_text)
        emitted_tools = [
            call["tool_name"] for batch in ws.bridge.requests for call in batch
        ]
        self.assertEqual(emitted_tools, ["get_climate_settings"])

    def test_manual_climate_read_unknown_fan_speed_blocks_user_question(self):
        tools = {
            "get_climate_settings": tool_schema("get_climate_settings", {}),
            "set_fan_speed": tool_schema(
                "set_fan_speed",
                {"level": {"type": "integer"}},
            ),
        }
        responses = {
            "get_climate_settings": ("SUCCESS", {"fan_speed": "unknown"}),
            "set_fan_speed": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ws.observe_user("Increase the fan speed by two levels.")

        result = ex.run(
            "get_climate_settings()\n"
            "respond('Sure, could you tell me the current fan speed level?')"
        )

        self.assertIsNotNone(result.response_text)
        self.assertIn(
            "can't change the fan speed by the requested number of levels",
            result.response_text,
        )
        self.assertIn("did not provide the current fan speed", result.response_text)

    def test_decrease_fan_speed_clamps_at_zero(self):
        tools = {
            "get_climate_settings": tool_schema("get_climate_settings", {}),
            "set_fan_speed": tool_schema(
                "set_fan_speed",
                {"level": {"type": "integer"}},
            ),
        }
        responses = {
            "get_climate_settings": ("SUCCESS", {"fan_speed": 0}),
            "set_fan_speed": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        ex.run("decrease_fan_speed()")
        self.assertEqual(self._emitted(ws, "set_fan_speed"), {"level": 0})

    def test_confirmation_success_completes_turn_without_extra_response(self):
        tools = {
            "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
        }
        ws, ex = self.make({"set_fan_speed": ("SUCCESS", {})}, tools)
        ws.remember(
            "pending_confirmation",
            {
                "gate_name": "tool_confirmation",
                "policy": "004",
                "action": "set the fan speed",
                "on_confirm_calls": [
                    {"tool_name": "set_fan_speed", "arguments": {"level": 3}},
                ],
                "response_on_success": "Confirmed, fan speed is set.",
            },
        )
        ws.observe_user("Yes, proceed.")
        result = ex.run(
            "handle_pending_confirmation()\n"
            "set_fan_speed(level=4)\n"
            "respond('This should not replace the confirmation result.')"
        )
        self.assertEqual(result.response_text, "Confirmed, fan speed is set.")
        calls = [call for batch in ws.bridge.requests for call in batch]
        self.assertEqual(
            [(call["tool_name"], call["arguments"]) for call in calls],
            [("set_fan_speed", {"level": 3})],
        )

    def test_default_confirmation_success_names_high_beam_action(self):
        tools = {
            "set_head_lights_high_beams": tool_schema(
                "set_head_lights_high_beams",
                {"on": {"type": "boolean"}},
                required=["on"],
                description="REQUIRES_CONFIRMATION, turns high beams on or off.",
            ),
        }
        ws, ex = self.make(
            {"set_head_lights_high_beams": ("SUCCESS", {"head_lights_high_beams": True})},
            tools,
        )

        with self.assertRaises(ResponseReady):
            ws._call_raw_tool_sync("set_head_lights_high_beams", {"on": True})

        pending = ws.scratchpad["facts"]["pending_confirmation"]
        self.assertEqual(pending["response_on_success"], "High beams turned on.")
        self.assertIn(
            "turn the high beam headlights on (on=True)",
            ws._response_text or "",
        )

        ws.observe_user("Yes, proceed.")
        result = ex.run("handle_pending_confirmation()")

        self.assertEqual(result.response_text, "High beams turned on.")
        self.assertEqual(
            self._emitted(ws, "set_head_lights_high_beams"),
            {"on": True},
        )

    def test_identical_read_is_cached_until_successful_mutation(self):
        tools = {
            "get_temperature_inside_car": tool_schema("get_temperature_inside_car", {}),
            "set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}}),
        }
        responses = {
            "get_temperature_inside_car": ("SUCCESS", {"temperature": 20}),
            "set_fan_speed": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        first = ex.run("r1 = get_temperature_inside_car()")
        second = ex.run(
            "r2 = get_temperature_inside_car()\n"
            "respond(str(r2.get('cached')) + '|' + str(r2.get('no_progress')))"
        )
        self.assertIsNone(first.error)
        self.assertEqual(second.response_text, "True|True")
        self.assertEqual(len(ws.bridge.requests), 1)
        ex.run("set_fan_speed(level=2)")
        ex.run("get_temperature_inside_car()")
        self.assertEqual(len(ws.bridge.requests), 3)

    def test_navigation_mutation_persists_returned_state_and_revision(self):
        tools = {
            "navigation_delete_destination": tool_schema(
                "navigation_delete_destination",
                {"destination_id_to_delete": {"type": "string"}},
            ),
        }
        responses = {
            "navigation_delete_destination": (
                "SUCCESS",
                {
                    "destination_deleted": True,
                    "new_waypoints": ["loc_a", "loc_b"],
                    "new_routes": ["route_ab"],
                },
            ),
        }
        ws, _ = self.make(responses, tools)
        ws.scratchpad["entities"]["last_routes"] = [{"route_id": "stale"}]
        ws.scratchpad["entities"]["active_route_records"] = [{"route_id": "old"}]
        ws.scratchpad["entities"]["last_applied_route_selection"] = {"route_id": "old"}
        ws._call_raw_tool_sync(
            "navigation_delete_destination",
            {"destination_id_to_delete": "loc_c"},
        )
        state = ws.scratchpad["entities"]["navigation_state"]
        self.assertEqual(state["waypoint_order"], ["loc_a", "loc_b"])
        self.assertEqual(state["route_ids"], ["route_ab"])
        self.assertEqual(state["destination_id"], "loc_b")
        self.assertEqual(state["revision"], 1)
        self.assertIs(state["is_multi_stop"], False)
        self.assertEqual(state["segment_count"], 1)
        self.assertNotIn("last_routes", ws.scratchpad["entities"])
        self.assertNotIn("active_route_records", ws.scratchpad["entities"])
        self.assertNotIn("last_applied_route_selection", ws.scratchpad["entities"])

    def test_nested_contact_name_is_normalized_before_required_field_check(self):
        tools = {
            "get_contact_information": tool_schema(
                "get_contact_information",
                {"contact_ids": {"type": "array"}},
            ),
        }
        responses = {
            "get_contact_information": (
                "SUCCESS",
                {
                    "con_1139": {
                        "name": {"first_name": "Nathan", "last_name": "Scott"},
                        "email": " nathan@example.com ",
                    },
                },
            ),
        }
        ws, ex = self.make(responses, tools)
        result = ex.run(
            "c = get_contact_details('con_1139', required_fields=['first_name', 'email'])\n"
            "respond(c['first_name'] + '|' + c['last_name'] + '|' + c['email'])"
        )
        self.assertEqual(
            result.response_text,
            "Nathan|Scott|nathan@example.com",
        )

    def test_charging_poi_summary_exposes_available_plug_ids(self):
        ws, _ = self.make({}, {})
        pois = ws._summarize_pois(
            {
                "status": "SUCCESS",
                "result": {
                    "pois_found_along_route": [
                        {
                            "id": "poi_1",
                            "name": "Charger",
                            "charging_plugs": [
                                {
                                    "plug_id": "plug_available",
                                    "power_type": "DC",
                                    "power_kw": 150,
                                    "availability": "available",
                                },
                                {
                                    "plug_id": "plug_busy",
                                    "power_type": "AC",
                                    "power_kw": 22,
                                    "availability": "occupied",
                                },
                            ],
                        },
                    ],
                },
            },
        )
        self.assertEqual(pois[0]["plug_ids"], ["plug_available", "plug_busy"])
        self.assertEqual(pois[0]["available_plug_ids"], ["plug_available"])

    def test_poi_summary_exposes_detour_facts(self):
        ws, _ = self.make({}, {})
        pois = ws._summarize_pois(
            {
                "status": "SUCCESS",
                "result": {
                    "pois_found_along_route": [
                        {
                            "id": "poi_1",
                            "detour_from_route_km": {
                                "detour": 6.6,
                                "unit": "km",
                            },
                            "detour_from_route_time": {
                                "hour": 0,
                                "minutes": 9,
                            },
                        },
                    ],
                },
            },
        )
        self.assertEqual(pois[0]["detour_km"], 6.6)
        self.assertEqual(pois[0]["detour_minutes"], 9)

    def test_poi_summary_distinguishes_navigation_and_host_ids(self):
        ws, _ = self.make({}, {})
        pois = ws._summarize_pois(
            {
                "status": "SUCCESS",
                "result": {
                    "pois_found": [
                        {
                            "id": "poi_station",
                            "corresponding_location_id": "loc_city",
                        },
                    ],
                },
            },
        )
        self.assertEqual(pois[0]["navigation_id"], "poi_station")
        self.assertEqual(pois[0]["host_location_id"], "loc_city")

    def test_poi_summary_keeps_name_poi_and_host_ids_close(self):
        ws, ex = self.make(
            {
                "get_location_id_by_location_name": ("SUCCESS", {"id": "loc_mad_180891"}),
                "search_poi_at_location": ("SUCCESS", {
                    "pois_found": [
                        {
                            "id": "poi_res_825069",
                            "name": "Mesón del Asador",
                            "corresponding_location_id": "loc_mad_180891",
                        },
                    ],
                }),
            },
            {
                "get_location_id_by_location_name": tool_schema(
                    "get_location_id_by_location_name",
                    {"location": {"type": "string"}},
                ),
                "search_poi_at_location": tool_schema(
                    "search_poi_at_location",
                    {
                        "location_id": {"type": "string"},
                        "category_poi": {"type": "string"},
                    },
                ),
            },
        )
        result = ex.run(
            "loc = get_location_id_by_location_name(location='Madrid')\n"
            "search_poi_at_location(location_id=loc['id'], category_poi='restaurants')\n"
            "poi = scratchpad['entities']['last_pois'][0]\n"
            "respond(poi['display'])"
        )
        self.assertIn("Mesón del Asador", result.response_text)
        self.assertIn("POI id: poi_res_825069", result.response_text)
        self.assertIn("host location: Madrid (loc_mad_180891)", result.response_text)
        self.assertEqual(
            ws.entities["last_pois"][0]["navigation_id"],
            "poi_res_825069",
        )

    def test_route_options_persist_rich_aliases(self):
        routes = [
            {
                "route_id": "route_fast",
                "alias": ["fastest", "first"],
                "distance_km": 10,
                "duration_hours": 0,
                "duration_minutes": 15,
                "road_types": ["urban"],
            },
            {
                "route_id": "route_short",
                "alias": ["shortest", "second"],
                "distance_km": 9,
                "duration_hours": 0,
                "duration_minutes": 20,
                "road_types": ["country road"],
            },
        ]
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": routes},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
            },
        )
        result = ex.run(
            "options = get_route_options(start_id='loc_a', destination_id='loc_b')\n"
            "selected = select_route(options['routes'], prefer='fastest')\n"
            "respond(options['fastest_route_id'] + '|' + "
            "ws.entities['last_route_options']['shortest_route_id'] + '|' + "
            "ws.entities['selected_route']['destination_id'] + '|' + "
            "str(selected['distance_km']))"
        )
        self.assertTrue(result.response_text.startswith("route_fast|route_short|loc_b|10"))
        self.assertEqual(ws.entities["selected_route"]["duration_hours"], 0)
        self.assertEqual(ws.entities["selected_route"]["duration_minutes"], 15)
        self.assertEqual(
            ws.entities["last_route_options"]["routes"][0]["road_types"],
            ["urban"],
        )
        self.assertIn("10 km", ws.entities["last_route_options"]["routes"][0]["display"])
        self.assertIn("0h 15m", ws.entities["last_route_options"]["routes"][0]["display"])
        self.assertIn(
            "route_id: route_fast",
            ws.entities["last_route_options"]["routes"][0]["display"],
        )

    def test_select_route_by_route_id_exposes_route_fields_at_top_level(self):
        routes = [
            {
                "route_id": "route_fast",
                "alias": ["fastest", "first"],
                "distance_km": 10,
                "duration_hours": 0,
                "duration_minutes": 15,
            },
            {
                "route_id": "route_second",
                "alias": ["second"],
                "distance_km": 12,
                "duration_hours": 0,
                "duration_minutes": 18,
            },
        ]
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": routes},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
            },
        )

        result = ex.run(
            "options = get_route_options(start_id='loc_a', destination_id='loc_b')\n"
            "selected = select_route(options['routes'], route_id='route_second')\n"
            "respond(selected['route_id'] + '|' + str(selected['distance_km']))"
        )

        self.assertTrue(result.response_text.startswith("route_second|12"))

    def test_route_normalization_exposes_numeric_distance_aliases(self):
        routes = [
            {
                "route_id": "route_fast",
                "alias": ["fastest"],
                "distance": "10.5km",
                "duration_hours": 0,
                "duration_minutes": 15,
            },
        ]
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": routes},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
            },
        )

        result = ex.run(
            "options = get_route_options(start_id='loc_a', destination_id='loc_b')\n"
            "selected = select_route(options['routes'], prefer='fastest')\n"
            "respond('|'.join([\n"
            "    str(options['fastest']['distance_km']),\n"
            "    str(options['fastest']['distance']),\n"
            "    str(selected['distance_km']),\n"
            "    str(selected['distance']),\n"
            "    str(first_number_value(options['fastest'])),\n"
            "]))"
        )

        self.assertEqual(result.response_text, "10.5|10.5|10.5|10.5|10.5")
        self.assertEqual(
            ws.scratchpad["entities"]["last_route_options"]["fastest"]["distance"],
            10.5,
        )

    def test_persisted_route_and_charging_aliases_are_runtime_globals_next_step(self):
        routes = [
            {
                "route_id": "route_fast",
                "alias": ["fastest"],
                "distance_km": 10,
                "duration_hours": 0,
                "duration_minutes": 15,
            },
        ]
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {"routes": routes},
                ),
                "get_charging_specs_and_status": (
                    "SUCCESS",
                    {"state_of_charge": 35, "remaining_range": "155.0km"},
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
                "get_charging_specs_and_status": tool_schema(
                    "get_charging_specs_and_status",
                    {},
                ),
            },
        )

        first = ex.run(
            "get_route_options(start_id='loc_a', destination_id='loc_b')\n"
            "get_charging_specs_and_status()\n"
            "respond('stored')"
        )
        self.assertEqual(first.error, None)

        second = ex.run(
            "respond(str(last_route_options['fastest']['distance']) + '|' + "
            "str(last_charging_specs_and_status['remaining_range_km']))"
        )

        self.assertEqual(second.response_text, "10|155")

    def test_charging_search_kilometer_accepts_normalized_string_range(self):
        ws, _ = self.make({}, {})
        ws.scratchpad["entities"]["last_charging_specs_and_status"] = {
            "remaining_range": "155.0km",
        }
        ws.scratchpad["entities"]["routes_by_id"] = {
            "route_hamburg": {
                "route_id": "route_hamburg",
                "distance_km": 895.38,
            },
        }

        kilometer = ws._charging_search_kilometer_from_state(
            {"route_id": "route_hamburg"},
        )

        self.assertEqual(kilometer, 150)

    def test_charging_search_kilometer_stops_on_unknown_range(self):
        ws, _ = self.make({}, {})
        ws.scratchpad["entities"]["last_charging_specs_and_status"] = {
            "remaining_range": None,
        }
        ws.scratchpad["entities"]["routes_by_id"] = {
            "route_hamburg": {
                "route_id": "route_hamburg",
                "distance_km": 895.38,
            },
        }

        kilometer = ws._charging_search_kilometer_from_state(
            {"route_id": "route_hamburg"},
        )

        self.assertIsNone(kilometer)

    def test_select_route_by_route_id_falls_back_to_grounded_route_registry(self):
        ws, ex = self.make({}, {})
        ws.scratchpad["entities"]["routes_by_id"] = {
            "route_hamburg_second": {
                "route_id": "route_hamburg_second",
                "start_id": "loc_warsaw",
                "destination_id": "loc_hamburg",
                "name_via": "B432, B132",
                "distance_km": 895.38,
                "duration_hours": 11,
                "duration_minutes": 9,
                "alias": ["second"],
            }
        }
        stale_routes = [
            {
                "route_id": "route_charger_fastest",
                "start_id": "loc_warsaw",
                "destination_id": "poi_ionity",
                "name_via": "A60",
                "distance_km": 2.73,
                "duration_hours": 0,
                "duration_minutes": 4,
                "alias": ["fastest"],
            }
        ]

        result = ex.run(
            f"selected = select_route({stale_routes!r}, route_id='route_hamburg_second')\n"
            "respond(selected['route_id'] + '|' + selected['destination_id'] + '|' + "
            "str(selected['distance_km']))"
        )

        self.assertTrue(
            result.response_text.startswith("route_hamburg_second|loc_hamburg|895.38")
        )

    def test_route_helper_resolves_policy_location_before_persistence(self):
        ws, ex = self.make(
            {
                "get_routes_from_start_to_destination": (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_only",
                                "alias": ["fastest", "shortest"],
                            },
                        ],
                    },
                ),
            },
            {
                "get_routes_from_start_to_destination": tool_schema(
                    "get_routes_from_start_to_destination",
                    {
                        "start_id": {"type": "string"},
                        "destination_id": {"type": "string"},
                    },
                ),
            },
        )
        result = ex.run(
            "get_route_options("
            "start_id=policy_location_id, destination_id='loc_b')\n"
            "respond(ws.entities['last_route_options']['start_id'])"
        )
        self.assertEqual(result.response_text, "loc_home_1")
        self.assertEqual(
            ws.entities["last_route_options"]["start_id"],
            "loc_home_1",
        )

    def test_policy_reordered_raw_batch_returns_original_result_order(self):
        tools = {
            "set_air_conditioning": tool_schema(
                "set_air_conditioning", {"on": {"type": "boolean"}}),
            "open_close_window": tool_schema(
                "open_close_window",
                {"window": {"type": "string"}, "percentage": {"type": "number"}}),
            "set_window_defrost": tool_schema(
                "set_window_defrost",
                {"on": {"type": "boolean"}, "defrost_window": {"type": "string"}}),
        }
        responses = {
            "set_air_conditioning": ("SUCCESS", {}),
            "open_close_window": ("SUCCESS", {}),
            "set_window_defrost": ("SUCCESS", {}),
        }
        ws, _ = self.make(responses, tools)
        results = ws._call_raw_tools_sync(
            [
                ("set_air_conditioning", {"on": True}),
                ("open_close_window", {"window": "DRIVER", "percentage": 0}),
                ("set_window_defrost", {"on": True, "defrost_window": "FRONT"}),
            ],
        )
        self.assertEqual(
            [result["tool_name"] for result in results],
            ["set_air_conditioning", "open_close_window", "set_window_defrost"],
        )
        self.assertEqual(
            [call["tool_name"] for call in ws.bridge.requests[0]],
            ["set_window_defrost", "open_close_window", "set_air_conditioning"],
        )

    def test_call_selected_charging_provider_uses_selected_plug_phone(self):
        tools = {
            "call_phone_by_number": tool_schema(
                "call_phone_by_number",
                {"phone_number": {"type": "string"}},
            ),
        }
        responses = {"call_phone_by_number": ("SUCCESS", {"calling": True})}
        ws, ex = self.make(responses, tools)
        ws.remember_entity(
            "selected_charging_plug",
            {
                "selected": {
                    "station_id": "poi_cha_1",
                    "phone_number": " +49 110 1244459 ",
                },
            },
        )
        result = ex.run(
            "call_selected_charging_provider()\n"
            "respond('Provider call started.')"
        )
        self.assertEqual(result.response_text, "Provider call started.")
        self.assertEqual(
            self._emitted(ws, "call_phone_by_number"),
            {"phone_number": "+49 110 1244459"},
        )
        self.assertEqual(
            ws.facts["last_charging_provider_call"]["phone_number"],
            "+49 110 1244459",
        )

    def test_plan_charging_for_next_meeting_uses_schedule_window_for_max(self):
        policy = (
            'CURRENT_LOCATION = {"id": "loc_man_660365", "name": "Mannheim"}\n'
            'DATETIME = {"year": 2025, "month": 1, "day": 10, "hour": 13, "minute": 20}\n'
        )
        tools = {
            "get_entries_from_calendar": tool_schema(
                "get_entries_from_calendar",
                {"month": {"type": "integer"}, "day": {"type": "integer"}},
            ),
            "get_location_id_by_location_name": tool_schema(
                "get_location_id_by_location_name",
                {"location": {"type": "string"}},
            ),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {
                    "start_id": {"type": "string"},
                    "destination_id": {"type": "string"},
                },
            ),
            "get_charging_specs_and_status": tool_schema(
                "get_charging_specs_and_status",
                {},
            ),
            "get_distance_by_soc": tool_schema(
                "get_distance_by_soc",
                {
                    "initial_state_of_charge": {"type": "integer"},
                    "final_state_of_charge": {"type": "integer"},
                },
            ),
            "search_poi_at_location": tool_schema(
                "search_poi_at_location",
                {
                    "location_id": {"type": "string"},
                    "category_poi": {"type": "string"},
                },
            ),
            "calculate_charging_time_by_soc": tool_schema(
                "calculate_charging_time_by_soc",
                {
                    "charging_station_id": {"type": "string"},
                    "charging_station_plug_id": {"type": "string"},
                    "start_state_of_charge": {"type": "integer"},
                    "target_state_of_charge": {"type": "integer"},
                },
            ),
        }
        responses = {
            "get_entries_from_calendar": (
                "SUCCESS",
                {
                    "meetings": [
                        {
                            "start": {"hour": "15", "minute": "30"},
                            "duration": "30min",
                            "location": "Stuttgart",
                        }
                    ],
                },
            ),
            "get_location_id_by_location_name": ("SUCCESS", {"id": "loc_stu_828398"}),
            "get_routes_from_start_to_destination": [
                (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_direct",
                                "start_id": "loc_man_660365",
                                "destination_id": "loc_stu_828398",
                                "distance_km": 110.8,
                                "duration_hours": 1,
                                "duration_minutes": 25,
                                "alias": ["fastest", "shortest"],
                            }
                        ],
                    },
                ),
                (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_to_charger",
                                "start_id": "loc_man_660365",
                                "destination_id": "poi_fast",
                                "distance_km": 4.0,
                                "duration_hours": 0,
                                "duration_minutes": 8,
                                "alias": ["fastest", "shortest"],
                            }
                        ],
                    },
                ),
                (
                    "SUCCESS",
                    {
                        "routes": [
                            {
                                "route_id": "route_charger_meeting",
                                "start_id": "poi_fast",
                                "destination_id": "loc_stu_828398",
                                "distance_km": 112.0,
                                "duration_hours": 1,
                                "duration_minutes": 30,
                                "alias": ["fastest", "shortest"],
                            }
                        ],
                    },
                ),
            ],
            "get_charging_specs_and_status": (
                "SUCCESS",
                {"state_of_charge": 20, "remaining_range": "101.0km"},
            ),
            "get_distance_by_soc": (
                "SUCCESS",
                {"distance_km_for_100_until_0_percent_soc": "507.0km"},
            ),
            "search_poi_at_location": (
                "SUCCESS",
                {
                    "pois_found": [
                        {
                            "id": "poi_fast",
                            "name": "Fastned",
                            "category": "charging_stations",
                            "phone_number": "+49 110",
                            "charging_plugs": [
                                {
                                    "plug_id": "plug_fast",
                                    "power_type": "DC",
                                    "power_kw": 300,
                                    "availability": "occupied",
                                }
                            ],
                        }
                    ],
                },
            ),
            "calculate_charging_time_by_soc": (
                "SUCCESS",
                {"time_from_20_until_30_percent_soc": "3min"},
            ),
        }
        ws, ex = self.make(responses, tools, policy=policy)
        result = ex.run(
            "plan = plan_charging_for_next_meeting("
            "range_buffer_km=40, arrival_buffer_minutes=5)\n"
            "respond(str(plan['min_charging_minutes']) + '|' + "
            "str(plan['max_charging_minutes']) + '|' + "
            "plan['charging_station_id'])"
        )
        self.assertEqual(result.response_text, "3|40|poi_fast")
        self.assertEqual(ws.facts["last_charging_time_plan"]["target_state_of_charge"], 30)
        self.assertEqual(
            ws.entities["selected_charging_plan"]["navigation_route_ids"],
            ["route_to_charger", "route_charger_meeting"],
        )
        self.assertNotIn("pending_route_narration", ws.facts)

    def test_call_selected_charging_provider_can_use_navigation_waypoint_phone(self):
        tools = {
            "call_phone_by_number": tool_schema(
                "call_phone_by_number",
                {"phone_number": {"type": "string"}},
            ),
        }
        responses = {"call_phone_by_number": ("SUCCESS", {"calling": True})}
        ws, ex = self.make(responses, tools)
        ws.remember_entity(
            "selected_charging_plan",
            {"charging_station_id": "poi_cha_2"},
        )
        ws.remember_entity(
            "navigation_state",
            {
                "waypoints": [
                    {"id": "loc_a"},
                    {
                        "id": "poi_cha_2",
                        "category": "charging_stations",
                        "phone_number": "+49 222",
                    },
                    {"id": "loc_b"},
                ],
            },
        )
        ex.run("call_selected_charging_provider()")
        self.assertEqual(
            self._emitted(ws, "call_phone_by_number"),
            {"phone_number": "+49 222"},
        )

    def test_calendar_normalization_exposes_common_start_aliases(self):
        ws, _ = self.make({}, {})
        entry = ws._normalize_calendar_entry(
            {
                "start": {"hour": "15", "minute": "30"},
                "duration": "30min",
                "location": "Stuttgart",
                "topic": "Partnership Discussion",
            }
        )
        self.assertEqual(entry["start_hour"], 15)
        self.assertEqual(entry["start_minute"], 30)
        self.assertEqual(entry["start_time_hour"], 15)
        self.assertEqual(entry["start_time_minute"], 30)
        self.assertEqual(entry["start_minutes"], 930)
        self.assertEqual(entry["start_time_minutes"], 930)
        self.assertEqual(entry["location_name"], "Stuttgart")

    def test_stop_after_response_and_system_exit_alias_stop_cleanly(self):
        ws, ex = self.make({}, {})
        result = ex.run(
            "respond('Stopping now.')\n"
            "stop_after_response()\n"
            "respond('Should not run.')"
        )
        self.assertIsNone(result.error)
        self.assertEqual(result.response_text, "Stopping now.")

        ws2, ex2 = self.make({}, {})
        result2 = ex2.run(
            "respond('Stopping via alias.')\n"
            "raise SystemExit\n"
            "respond('Should not run.')"
        )
        self.assertIsNone(result2.error)
        self.assertEqual(result2.response_text, "Stopping via alias.")


if __name__ == "__main__":
    unittest.main()
