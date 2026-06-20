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
        ws._failed_mutations["set_fan_speed"] = {"tool_name": "set_fan_speed", "status": "FAILURE"}
        result = ex.run("set_fan_speed(level=3)\nrespond('Fan set to 3.')")
        self.assertEqual(result.response_text, "Fan set to 3.")

    def test_clean_mutation_allows_success_text(self):
        ws, ex = self.make(
            {"set_fan_speed": ("SUCCESS", {})},
            {"set_fan_speed": tool_schema("set_fan_speed", {"level": {"type": "integer"}})},
        )
        result = ex.run("set_fan_speed(level=3)\nrespond('Fan set to 3.')")
        self.assertEqual(result.response_text, "Fan set to 3.")

    # --- 2. active-navigation guard --------------------------------------

    def _nav_schema(self):
        return tool_schema(
            "set_new_navigation",
            {"route_ids": {"type": "array", "items": {"type": "string"}}},
        )

    def test_set_new_navigation_blocked_when_active(self):
        ws, ex = self.make({}, {"set_new_navigation": self._nav_schema()})
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": True}
        result = ex.run("set_new_navigation(route_ids=['route_9'])")
        self.assertIn("already an active route", result.response_text)
        # The blocked call must never reach the bridge.
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

    def test_set_new_navigation_redirects_to_replace_when_active(self):
        nav = {"navigation_active": True, "waypoints_id": ["loc_a", "loc_b"],
               "routes_to_final_destination_id": ["r1"],
               "details": {"waypoints": [{"id": "loc_a", "name": "A"}, {"id": "loc_b", "name": "B"}],
                           "routes": [{"route_id": "r1"}]}}
        tools = {
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state", {"detailed_information": {"type": "boolean"}}),
            "get_routes_from_start_to_destination": tool_schema(
                "get_routes_from_start_to_destination",
                {"start_id": {"type": "string"}, "destination_id": {"type": "string"}}),
            "set_new_navigation": tool_schema(
                "set_new_navigation", {"route_ids": {"type": "array", "items": {"type": "string"}}}),
            "navigation_replace_final_destination": tool_schema(
                "navigation_replace_final_destination",
                {"new_destination_id": {"type": "string"},
                 "route_id_leading_to_new_destination": {"type": "string"}}),
        }
        responses = {
            "get_current_navigation_state": ("SUCCESS", nav),
            "get_routes_from_start_to_destination": ("SUCCESS", {"routes": [
                {"route_id": "R_to", "alias": ["fastest"], "destination_id": "loc_dest"}]}),
            "navigation_replace_final_destination": ("SUCCESS", {}),
        }
        ws, ex = self.make(responses, tools)
        # Model fetched routes to the new destination earlier (auto-persisted).
        ws.scratchpad["entities"]["last_routes"] = [
            {"route_id": "R_new", "destination_id": "loc_dest", "alias": ["fastest", "shortest"]}]
        ex.run("set_new_navigation(route_ids=['R_new'])")
        # Redirected: replace was emitted, set_new_navigation was NOT.
        self.assertIsNotNone(self._emitted(ws, "navigation_replace_final_destination"))
        self.assertIsNone(self._emitted(ws, "set_new_navigation"))

    def test_set_new_navigation_allowed_when_inactive(self):
        ws, ex = self.make(
            {"set_new_navigation": ("SUCCESS", {})},
            {"set_new_navigation": self._nav_schema()},
        )
        ws.scratchpad["entities"]["navigation_state"] = {"navigation_active": False}
        ex.run("set_new_navigation(route_ids=['route_9'])\nrespond('Navigation started.')")
        self.assertEqual(len(ws.bridge.requests), 1)

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
        result = ex.run("set_occupied_seat_heating(level=2)")
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


if __name__ == "__main__":
    unittest.main()
