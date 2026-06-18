import json
import unittest

from track_1_agent_coroutine_under_test.coroutine_repl import (
    BlockingPythonExecutor,
    CoroutineWorkspace,
    ResponseReady,
    UnknownToolResponseValue,
    WORKSPACE_HELPER_NAMES,
    result_by_tool,
    result_value,
    routes_value,
)


class FakeBridge:
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def request_tool_calls(self, calls: list[dict]) -> list[dict]:
        self.requests.append(calls)
        results = []
        for call in calls:
            tool_name = call["tool_name"]
            if tool_name == "get_location_id_by_location_name":
                result = {"id": "loc_rom_1"}
            elif tool_name == "get_current_navigation_state":
                result = {
                    "navigation_active": True,
                    "waypoints_id": ["loc_min_1", "loc_rom_1"],
                    "routes_to_final_destination_id": ["route_1"],
                    "details": {
                        "waypoints": [
                            {"id": "loc_min_1", "name": "Minsk"},
                            {"id": "loc_rom_1", "name": "Rome"},
                        ],
                        "routes": [{"route_id": "route_1"}],
                    },
                }
            else:
                raise AssertionError(f"Unexpected tool call: {call}")
            results.append(
                {
                    "tool_name": tool_name,
                    "tool_call_id": f"call-{len(results)}",
                    "content": json.dumps({"status": "SUCCESS", "result": result}),
                }
            )
        return results


def tool_schema(
    name: str,
    properties: dict,
    *,
    required: list[str] | None = None,
    description: str = "",
) -> dict:
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


class CoroutineBatchHelperTest(unittest.TestCase):
    def make_workspace(self) -> tuple[CoroutineWorkspace, FakeBridge]:
        bridge = FakeBridge()
        workspace = CoroutineWorkspace(bridge)
        workspace.available_tools = {
            "get_location_id_by_location_name": tool_schema(
                "get_location_id_by_location_name",
                {"location": {"type": "string"}},
            ),
            "get_current_navigation_state": tool_schema(
                "get_current_navigation_state",
                {"detailed_information": {"type": "boolean"}},
            ),
        }
        return workspace, bridge

    def test_mixed_batch_dispatches_raw_tool_and_navigation_helper(self) -> None:
        workspace, bridge = self.make_workspace()

        results = workspace.call_batch_sync(
            [
                ("get_navigation_state", {"detailed_information": True}),
                ("get_location_id_by_location_name", {"location": "Rome"}),
            ]
        )

        location = result_value(result_by_tool(results, "get_location_id_by_location_name"))
        navigation = result_value(result_by_tool(results, "get_navigation_state"))
        self.assertEqual(location["location_id"], "loc_rom_1")
        self.assertEqual(navigation["destination_id"], "loc_rom_1")
        self.assertEqual(
            [[call["tool_name"] for call in request] for request in bridge.requests],
            [["get_location_id_by_location_name"], ["get_current_navigation_state"]],
        )

    def test_executor_batch_uses_helper_aware_dispatch(self) -> None:
        workspace, _ = self.make_workspace()
        executor = BlockingPythonExecutor(workspace)

        execution = executor.run(
            """
results = batch([("get_navigation_state", {"detailed_information": True})])
navigation = result_value(result_by_tool(results, "get_navigation_state"))
print(navigation["destination_id"])
"""
        )

        self.assertIsNone(execution.error)
        self.assertEqual(execution.stdout.strip(), "loc_rom_1")
        self.assertNotIn("get_navigation_state", workspace.scratchpad["gates"])

    def test_every_registered_helper_is_dispatched_as_a_helper(self) -> None:
        workspace, bridge = self.make_workspace()
        called: list[tuple[str, dict]] = []

        for helper_name in WORKSPACE_HELPER_NAMES:
            setattr(
                workspace,
                helper_name,
                lambda _name=helper_name, **kwargs: called.append((_name, kwargs)) or {"name": _name},
            )

        results = workspace.call_batch_sync(
            [(helper_name, {"marker": helper_name}) for helper_name in WORKSPACE_HELPER_NAMES]
        )

        self.assertEqual(bridge.requests, [])
        self.assertEqual([name for name, _ in called], list(WORKSPACE_HELPER_NAMES))
        self.assertEqual([result["tool_name"] for result in results], list(WORKSPACE_HELPER_NAMES))
        for result in results:
            self.assertEqual(result["result"]["name"], result["tool_name"])

    def test_callable_batch_names_are_canonicalized_without_repr_leaks(self) -> None:
        workspace, bridge = self.make_workspace()
        workspace.available_tools["set_air_conditioning"] = tool_schema(
            "set_air_conditioning",
            {"on": {"type": "boolean"}},
        )
        executor = BlockingPythonExecutor(workspace)

        execution = executor.run(
            """
batch([
    (set_air_conditioning_on_safe, {}),
    (set_fan_speed, {"level": 3}),
])
"""
        )

        self.assertIsNone(execution.error)
        self.assertEqual(bridge.requests, [])
        self.assertIn("set_fan_speed", execution.response_text or "")
        self.assertNotIn("<bound method", execution.response_text or "")
        self.assertNotIn("<function", execution.response_text or "")

    def test_unknown_callable_in_batch_is_a_model_code_error(self) -> None:
        workspace, bridge = self.make_workspace()
        executor = BlockingPythonExecutor(workspace)

        execution = executor.run(
            """
def custom_call():
    return None

batch([(custom_call, {})])
"""
        )

        self.assertEqual(bridge.requests, [])
        self.assertIsNone(execution.response_text)
        self.assertEqual(execution.error["type"], "ValueError")
        self.assertIn("preloaded wrapper or bound workspace helper", execution.error["message"])

    def test_policy_sensitive_raw_batch_call_delegates_to_helper(self) -> None:
        workspace, bridge = self.make_workspace()
        called: list[str] = []
        workspace.set_high_beams_on_safe = (
            lambda: called.append("set_high_beams_on_safe")
            or {"status": "SUCCESS", "enabled": True}
        )

        results = workspace.call_batch_sync(
            [("set_head_lights_high_beams", {"on": True})]
        )

        self.assertEqual(bridge.requests, [])
        self.assertEqual(called, ["set_high_beams_on_safe"])
        self.assertEqual(results[0]["tool_name"], "set_high_beams_on_safe")
        self.assertEqual(results[0]["status"], "SUCCESS")

    def test_helper_batch_preserves_non_success_status(self) -> None:
        workspace, bridge = self.make_workspace()
        workspace.select_route = lambda **kwargs: {
            "status": "NOT_FOUND",
            "reason": "no routes available",
        }

        results = workspace.call_batch_sync(
            [("select_route", {"routes": []})]
        )

        self.assertEqual(bridge.requests, [])
        self.assertEqual(results[0]["status"], "NOT_FOUND")
        with self.assertRaises(RuntimeError):
            result_value(results[0])

    def test_route_extractor_turns_unknown_field_into_literal_limitation(self) -> None:
        workspace, _ = self.make_workspace()
        unknown = UnknownToolResponseValue(
            workspace,
            "result.get_routes_from_start_to_destination.routes",
        )

        with self.assertRaises(ResponseReady):
            routes_value({"routes": unknown})

        self.assertEqual(
            workspace._response_text,
            (
                "I acknowledge that I can't complete the requested action because "
                "the required tool response field "
                "get_routes_from_start_to_destination.routes is unavailable."
            ),
        )

    def test_result_value_stops_when_success_payload_is_entirely_unavailable(self) -> None:
        workspace, _ = self.make_workspace()
        unknown = UnknownToolResponseValue(
            workspace,
            "result.get_routes_from_start_to_destination.routes",
        )
        result = {
            "status": "SUCCESS",
            "result": {"routes": unknown, "status": "SUCCESS"},
            "tool_name": "get_routes_from_start_to_destination",
        }

        with self.assertRaises(ResponseReady):
            result_value(result)

        self.assertEqual(
            workspace._response_text,
            (
                "I acknowledge that I can't complete the requested action because "
                "the required tool response field "
                "get_routes_from_start_to_destination.routes is unavailable."
            ),
        )

    def test_result_value_preserves_partially_available_payload(self) -> None:
        workspace, _ = self.make_workspace()
        unknown = UnknownToolResponseValue(
            workspace,
            "result.get_charging_specs_and_status.remaining_range",
        )
        result = {
            "status": "SUCCESS",
            "result": {
                "state_of_charge": 50,
                "remaining_range": unknown,
                "status": "SUCCESS",
            },
            "tool_name": "get_charging_specs_and_status",
        }

        value = result_value(result)

        self.assertEqual(value["state_of_charge"], 50)
        self.assertIs(value["remaining_range"], unknown)
        self.assertIsNone(workspace._response_text)

    def test_parsed_success_exposes_unknown_result_field_at_top_level(self) -> None:
        workspace, _ = self.make_workspace()
        parsed = workspace._parse_tool_result(
            {
                "tool_name": "get_charging_specs_and_status",
                "tool_call_id": "call-range",
                "content": json.dumps(
                    {
                        "status": "SUCCESS",
                        "result": {
                            "state_of_charge": 70,
                            "remaining_range": "unknown",
                        },
                    }
                ),
            }
        )

        self.assertEqual(parsed["state_of_charge"], 70)
        with self.assertRaises(ResponseReady):
            f"{parsed['remaining_range']}"
        self.assertIn(
            "get_charging_specs_and_status.remaining_range",
            workspace._response_text or "",
        )

    def test_select_route_exposes_result_alias(self) -> None:
        workspace, _ = self.make_workspace()
        route = {
            "route_id": "route-fast",
            "alias": ["fastest", "first"],
            "distance_km": 1168.11,
            "duration_hours": 14,
            "duration_minutes": 38,
        }

        selected = workspace.select_route([route], prefer="fastest")

        self.assertEqual(selected["status"], "SUCCESS")
        self.assertIs(selected["result"], selected["route"])
        self.assertEqual(selected["result"]["route_id"], "route-fast")
        self.assertEqual(selected["result"]["duration"], "14h 38m")

    def test_unresolved_confirmation_content_is_blocked_before_prompt(self) -> None:
        workspace, bridge = self.make_workspace()
        workspace.available_tools["send_email"] = tool_schema(
            "send_email",
            {
                "email_addresses": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "content_message": {"type": "string"},
            },
            required=["email_addresses", "content_message"],
            description="REQUIRES_CONFIRMATION, Email Tool",
        )

        with self.assertRaises(ResponseReady):
            workspace.call_tools_sync(
                [
                    (
                        "send_email",
                        {
                            "email_addresses": ["person@example.com"],
                            "content_message": (
                                "The route is None km and takes approximately unknown duration."
                            ),
                        },
                    )
                ]
            )

        self.assertEqual(bridge.requests, [])
        self.assertNotIn("pending_confirmation", workspace.scratchpad["facts"])
        self.assertIn("content_message", workspace._response_text or "")

    def test_confirmation_content_can_use_none_as_ordinary_word(self) -> None:
        workspace, _ = self.make_workspace()
        calls = [
            {
                "tool_name": "send_email",
                "arguments": {
                    "content_message": "None of the proposed dates work for me.",
                },
            }
        ]

        self.assertIsNone(workspace._find_unresolved_confirmation_argument(calls))

    def test_invalid_confirmation_arguments_fail_before_prompt(self) -> None:
        workspace, bridge = self.make_workspace()
        workspace.available_tools["send_email"] = tool_schema(
            "send_email",
            {
                "email_addresses": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "content_message": {"type": "string"},
            },
            required=["email_addresses", "content_message"],
            description="REQUIRES_CONFIRMATION, Email Tool",
        )

        with self.assertRaises(ValueError):
            workspace.call_tools_sync(
                [
                    (
                        "send_email",
                        {
                            "email_addresses": [workspace.set_air_conditioning_on_safe],
                            "content_message": "hello",
                        },
                    )
                ]
            )

        self.assertEqual(bridge.requests, [])
        self.assertIsNone(workspace._response_text)
        self.assertNotIn("pending_confirmation", workspace.scratchpad["facts"])

    def test_user_response_filters_runtime_artifacts(self) -> None:
        workspace, _ = self.make_workspace()

        workspace.respond("The <function bad at 0x123> is unavailable.")

        self.assertEqual(
            workspace._response_text,
            "I hit an internal issue while preparing the response.",
        )


if __name__ == "__main__":
    unittest.main()
