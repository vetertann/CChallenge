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

    def request_tool_calls(self, calls: list[dict]) -> list[dict]:
        self.requests.append(calls)
        out = []
        for index, call in enumerate(calls):
            name = call["tool_name"]
            status, result = self.responses.get(name, ("SUCCESS", {}))
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

    def test_get_weather_uses_policy_date_and_time(self):
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
        self.assertEqual(args["time_hour_24hformat"], 14)
        self.assertEqual(args["time_minutes"], 30)

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
        self.assertIn("can't remove the intermediate stop", result.response_text)
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
        self.assertIn("can't remove the intermediate stop", result.response_text)
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
        self.assertIn("can't remove the intermediate stop", result.response_text)
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

    def test_missing_destination_replacement_tool_blocks_route_lookup_for_requested_edit(self):
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

        self.assertIsNotNone(result.response_text)
        self.assertIn("can't change the destination", result.response_text)
        self.assertIn("navigation_replace_final_destination", result.response_text)
        self.assertIsNone(self._emitted(ws, "get_routes_from_start_to_destination"))

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

    def test_route_choice_response_for_unavailable_replacement_is_rewritten(self):
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
        self.assertIn("can't change the destination", result.response_text)
        self.assertNotIn("Which route", result.response_text)

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


    # --- 8. route-presentation narration (policy 022/021) ----------------

    def _routes(self, toll=False, n=3):
        routes = [{"route_id": "R_to", "alias": ["fastest", "first", "shortest"],
                   "includes_toll": toll, "name_via": "K1"}]
        for i in range(2, n + 1):
            routes.append({"route_id": f"R{i}", "alias": [f"r{i}"],
                           "includes_toll": False, "name_via": f"K{i}"})
        return routes

    def test_narration_fastest_with_alternatives(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(), "R_to")
        self.assertIn("fastest route", text)
        self.assertIn("2 other options", text)
        self.assertNotIn("toll", text)

    def test_narration_includes_tolls_when_present(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(toll=True), "R_to")
        self.assertIn("toll roads", text)

    def test_narration_single_route_no_alternatives_clause(self):
        from track_1_agent_coroutine_under_test.coroutine_repl import CoroutineWorkspace
        text = CoroutineWorkspace._route_narration(self._routes(n=1), "R_to")
        self.assertIn("fastest route", text)
        self.assertNotIn("other option", text)

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

    def test_climate_sync_guard_repairs_manual_inverse_copy(self):
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
        self.assertEqual(temp_args, {"seat_zone": "DRIVER", "temperature": 16.0})
        self.assertEqual(heat_args, {"seat_zone": "DRIVER", "level": 1})
        self.assertEqual(
            ws.scratchpad["gates"]["climate_sync_guard"]["status"],
            "REPAIRED",
        )

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
            "can't increase the fan speed by the requested number of levels",
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
            "respond('This should not replace the confirmation result.')"
        )
        self.assertEqual(result.response_text, "Confirmed, fan speed is set.")

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
            "select_route(options['routes'], prefer='fastest')\n"
            "respond(options['fastest_route_id'] + '|' + "
            "ws.entities['last_route_options']['shortest_route_id'] + '|' + "
            "ws.entities['selected_route']['destination_id'])"
        )
        self.assertEqual(result.response_text, "route_fast|route_short|loc_b")
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
        results = ws.call_batch_sync(
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
            "get_routes_from_start_to_destination": (
                "SUCCESS",
                {
                    "routes": [
                        {
                            "route_id": "route_fast",
                            "distance_km": 110.8,
                            "duration_hours": 1,
                            "duration_minutes": 25,
                            "alias": ["fastest", "shortest"],
                        }
                    ],
                },
            ),
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
