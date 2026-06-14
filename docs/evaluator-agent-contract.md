# Evaluator-Agent Contract

This document is the practical reference for building a CAR-bench agent against
the evaluator used in this repository.

It answers two questions:

1. What the evaluator sends to the agent.
2. What the evaluator expects back from the agent.

It also includes the current local tool catalog shipped in this repo so you can
build a different agent implementation without reverse-engineering the starter.

## Scope

This is about the benchmark-visible boundary only.

- The evaluator owns task loading, hidden state, tool execution, simulated user
  turns, and scoring.
- Your agent owns decision-making for the next assistant step.
- Your agent must not execute CAR-bench tools directly.

The relevant sources in this repo are:

- [development-guide.md](/Users/ivan/Documents/Hackatons/CAR%20CHALLENGE/docs/development-guide.md)
- [agent-under-test-harnessing.md](/Users/ivan/Documents/Hackatons/CAR%20CHALLENGE/docs/agent-under-test-harnessing.md)
- [car_bench_evaluator.py](/Users/ivan/Documents/Hackatons/CAR%20CHALLENGE/src/evaluator/car_bench_evaluator.py)
- [tool_call_types.py](/Users/ivan/Documents/Hackatons/CAR%20CHALLENGE/src/tool_call_types.py)

## Mental Model

One CAR-bench task is one A2A conversation identified by `context_id`.

For each assistant step:

1. The evaluator sends your agent the current allowed context.
2. Your agent returns one benchmark-visible response.
3. If your response contains tool calls, the evaluator executes them.
4. The evaluator sends tool results back on the next turn.
5. If your response contains only user-facing text, the evaluator advances the
   simulated user and sends the next user utterance.

The evaluator is the only component allowed to mutate benchmark state.

## Wire Format

Messages are exchanged as A2A `Message` objects composed of protobuf `Part`
objects.

The only part kinds you need here are:

- `text`: natural-language content
- `data`: machine-readable structured payload

In this repo, parts are built with:

- `new_text_part(...)`
- `new_data_part(...)`

and parsed by checking:

```python
part.WhichOneof("content")
```

## What The Evaluator Sends

### First Turn

On the first turn, the evaluator sends two parts:

1. A `text` part containing the combined system prompt and initial user request.
2. A `data` part containing the available tool definitions.

Canonical shape:

```text
text part:
System: <full CAR-bench wiki / policy prompt>

User: <initial user request>
```

```json
data part:
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Weather Information: ...",
        "parameters": {
          "type": "object",
          "properties": {
            "...": {}
          },
          "required": []
        }
      }
    }
  ]
}
```

Notes:

- The `System:` section is the real policy prompt the benchmark expects the
  agent to follow.
- The `User:` section is the initial task request.
- The `tools` list is the authoritative runtime contract for this task. Do not
  hardcode parameters from a local catalog and assume they are always present.

### Tool-Result Turn

If your previous response requested tool calls, the evaluator executes them and
returns one `data` part:

```json
{
  "tool_results": [
    {
      "tool_name": "get_weather",
      "tool_call_id": "call_abc123",
      "content": "{\"temperature\":15,\"condition\":\"sunny\"}"
    }
  ]
}
```

Notes:

- `content` is a string payload, often JSON encoded as text.
- `tool_call_id` lets your agent match a result to a previous tool call.
- The local wrapper constructs this shape in
  [car_bench_evaluator.py](/Users/ivan/Documents/Hackatons/CAR%20CHALLENGE/src/evaluator/car_bench_evaluator.py).

### User Follow-Up Turn

If your previous response was user-facing text without tool calls, the evaluator
can send a normal `text` part containing the next user utterance:

```text
Yes, please do that.
```

### Metadata

The evaluator also attaches `Message.metadata` with a small source tag:

```json
{"source": "user"}
```

or:

```json
{"source": "environment"}
```

This is optional convenience metadata. Your agent should still parse the actual
message parts, not rely only on metadata.

## What Your Agent Must Return

Your agent returns one A2A `Message` containing one or more parts.

The benchmark-visible contract is:

- user-facing speech in a `text` part
- tool requests in a `data` part under `tool_calls`
- optional debug reasoning in a `data` part under `reasoning_content`

### Valid Response: Text Only

```text
text part:
I found two routes. The fastest one takes 18 minutes. Do you want me to start navigation?
```

Use this when you are only speaking to the user and not calling tools.

### Valid Response: Tool Calls Only

```json
data part:
{
  "tool_calls": [
    {
      "tool_name": "get_weather",
      "arguments": {
        "location_or_poi_id": "loc_123",
        "month": 6,
        "day": 13,
        "hour": 18,
        "minute": 0
      }
    }
  ]
}
```

### Valid Response: Text Plus Tool Calls

```text
text part:
Let me check that for you.
```

```json
data part:
{
  "tool_calls": [
    {
      "tool_name": "get_current_navigation_state",
      "arguments": {
        "detailed_information": true
      }
    }
  ]
}
```

### Optional Debug Reasoning

```json
data part:
{
  "reasoning_content": "Need weather before opening sunroof because of policy."
}
```

This is for debugging only. It is not the action contract.

## Canonical Tool-Call Shape

Use this exact payload shape:

```json
{
  "tool_calls": [
    {
      "tool_name": "tool_name_here",
      "arguments": {
        "arg1": "value"
      }
    }
  ]
}
```

This matches [tool_call_types.py](/Users/ivan/Documents/Hackatons/CAR%20CHALLENGE/src/tool_call_types.py).

Important:

- Use `tool_name`, not some alternative field name.
- Use `arguments` as an object, not a JSON string.
- Use only tool names and parameters present in the incoming `tools` payload.
- If you need multiple tools in one turn, include multiple entries in
  `tool_calls`.

## What Not To Return

Do not do these:

- Do not execute benchmark tools yourself.
- Do not return custom action schemas that the evaluator does not know.
- Do not hide tool calls in `metadata`.
- Do not put private plans or hidden observations in place of benchmark-visible
  message parts.
- Do not invent unavailable tools or removed parameters.

## Practical Parsing Rules For Your Agent

If you are writing another agent from scratch, the minimum robust parser is:

1. Read all parts in order.
2. If you see a `text` part on the first turn, split the initial prompt into
   `System:` and `User:` sections.
3. If you see a `data` part with `tools`, cache that tool schema for the
   current `context_id`.
4. If you see a `data` part with `tool_results`, parse the list and feed it into
   your conversation state.
5. Maintain state per `context_id`; never leak one task into another.
6. When finished with a turn, return only benchmark-visible text and/or
   benchmark-visible tool calls.

## Dynamic Tool Availability

The local tool catalog below is useful for orientation, but the evaluator can
change the actual per-task tool surface.

Two mechanisms matter:

1. Some scenarios may remove `planning_tool` and `think`.
2. Some CAR-bench tasks intentionally remove entire tools or individual
   parameters to test hallucination behavior.

In the CAR-bench code, removals are applied by name using dot notation such as:

- `tool_name`
- `tool_name.parameter_name`
- `tool_name.parameter_name.sub_parameter_name`

That means the only safe rule is:

- treat the incoming `tools` payload as ground truth for the current turn

If the local catalog and the live `tools` payload disagree, the live payload
wins.

## Current Local Tool Catalog

The benchmark overview mentions 58 tools, but the current local registry in
[tools/__init__.py](/Users/ivan/Documents/Hackatons/CAR%20CHALLENGE/third_party/car-bench/car_bench/envs/car_voice_assistant/tools/__init__.py)
exposes 57 tools in `ALL_TOOLS`. `get_fuel_information` exists in the codebase
but is commented out of the active registry.

These are the current registered tool names and their local descriptions.

### Preferences

| Tool | Purpose |
| --- | --- |
| `get_user_preferences` | Retrieve stored user preferences for selected categories and subcategories. |

### Cross-Domain

| Tool | Purpose |
| --- | --- |
| `calculate_math` | Calculate a mathematical expression. |
| `calculate_datetime` | Add offsets to a datetime and return the resulting datetime. |
| `think` | Record internal thought in the benchmark log without changing state. |
| `planning_tool` | Create, update, list, inspect, activate, mark, and delete multi-step plans. |

### Vehicle Control And Vehicle State

| Tool | Purpose |
| --- | --- |
| `open_close_sunroof` | Open or close the sunroof to a target percentage. |
| `open_close_sunshade` | Open or close the sunshade to a target percentage. |
| `open_close_trunk_door` | Open or close the trunk door. Marked `REQUIRES_CONFIRMATION`. |
| `open_close_window` | Move a specified window to a target percentage. |
| `set_air_circulation` | Set air circulation mode. |
| `set_air_conditioning` | Turn air conditioning on or off. |
| `set_ambient_lights` | Turn ambient lights on or off and set color. |
| `set_climate_temperature` | Set climate temperature for selected seat zones. |
| `set_fan_airflow_direction` | Set fan airflow direction. |
| `set_fan_speed` | Set fan speed. |
| `set_fog_lights` | Turn fog lights on or off. |
| `set_head_lights_high_beams` | Turn high beams on or off. Marked `REQUIRES_CONFIRMATION`. |
| `set_head_lights_low_beams` | Turn low beams on or off. |
| `set_reading_light` | Turn one or more reading lights on or off. |
| `set_seat_heating` | Set seat-heating level for selected zones. |
| `set_steering_wheel_heating` | Set steering-wheel heating level. |
| `set_window_defrost` | Turn window defrost on or off for a selected window group. |
| `get_ambient_light_status_and_color` | Read ambient-light on/off state and current color. |
| `get_car_color` | Read the vehicle exterior color. |
| `get_climate_settings` | Read current fan speed, airflow direction, AC state, circulation mode, and defrost state. |
| `get_exterior_lights_status` | Read low beam, high beam, and fog-light state. |
| `get_reading_lights_status` | Read interior reading-light state. |
| `get_seat_heating_level` | Read seat-heating levels. |
| `get_seats_occupancy` | Read seat occupancy. |
| `get_steering_wheel_heating_level` | Read steering-wheel heating level. |
| `get_sunroof_and_sunshade_position` | Read sunroof and sunshade positions. |
| `get_temperature_inside_car` | Read cabin temperature by seat zone. |
| `get_trunk_door_position` | Read trunk-door position. |
| `get_vehicle_window_positions` | Read current window positions. |

### Weather

| Tool | Purpose |
| --- | --- |
| `get_weather` | Get weather for a location or POI and a specified time slot on the current day. |

### Navigation

| Tool | Purpose |
| --- | --- |
| `search_poi_at_location` | Search for POIs of a category around a location. |
| `search_poi_along_the_route` | Search for POIs of a category along a route. |
| `get_routes_from_start_to_destination` | Get route alternatives between a start and destination. |
| `get_location_id_by_location_name` | Resolve a location or city name to a location ID. |
| `get_current_navigation_state` | Read whether navigation is active and the current route state. |
| `convert_route_distance_and_time` | Convert route distance to time or time to distance for a specific route. |
| `set_new_navigation` | Replace current navigation and start a new route. |
| `navigation_add_one_waypoint` | Insert one waypoint into an active route. |
| `navigation_replace_one_waypoint` | Replace one waypoint in an active multi-stop route. |
| `navigation_replace_final_destination` | Replace the final destination in an active route. |
| `navigation_delete_waypoint` | Delete one waypoint from an active multi-stop route. |
| `navigation_delete_destination` | Delete the current final destination when route structure allows it. |
| `delete_current_navigation` | Clear the current navigation state and deactivate navigation. |

### Charging

| Tool | Purpose |
| --- | --- |
| `get_charging_specs_and_status` | Get battery capacity, max AC/DC charging power, state of charge, and remaining range. |
| `get_distance_by_soc` | Estimate drivable distance between an initial and final state of charge. |
| `calculate_charging_time_by_soc` | Estimate charging time from start SOC to target SOC. |
| `calculate_charging_soc_by_time` | Estimate reached SOC after charging for a specified duration. |

### Productivity And Communication

| Tool | Purpose |
| --- | --- |
| `get_contact_id_by_contact_name` | Resolve a contact name to one or more contact IDs. |
| `get_entries_from_calendar` | Get current-day calendar entries. |
| `get_contact_information` | Get name, phone number, and email for contact IDs. |
| `call_phone_by_number` | Call a phone number. |
| `send_email` | Send an email. Marked `REQUIRES_CONFIRMATION`. |

## Tool Schema Details

The catalog above is only a human summary. The evaluator sends each tool in
OpenAI function format, including exact parameter schema.

That means another agent should:

- read `function.name`
- read `function.description`
- read `function.parameters`
- respect `required`
- respect any parameter enums or nested object structure actually present in the
  incoming payload

Example:

```json
{
  "type": "function",
  "function": {
    "name": "set_new_navigation",
    "description": "Navigation Control: sets and starts new navigation...",
    "parameters": {
      "type": "object",
      "properties": {
        "waypoint_ids": {
          "type": "array"
        },
        "route_ids": {
          "type": "array"
        }
      },
      "required": ["waypoint_ids", "route_ids"]
    }
  }
}
```

## Policy-Relevant Tool Notes

Some tool descriptions are intentionally prefixed with
`REQUIRES_CONFIRMATION`. Your agent is expected to obey the policy prompt and
ask for explicit user confirmation before calling them.

Also, some policies require prerequisite reads before writes. Common examples
from the CAR-bench wiki are:

- check weather before certain sunroof or fog-light actions
- inspect current window or AC state before certain climate actions
- disambiguate among multiple valid routes, contacts, POIs, or parameter values

Those rules live in the `System:` prompt, not in the tool schema alone.

## Recommended Implementation Strategy

If you want to build a separate agent implementation, the safest architecture is:

1. Parse inbound parts into a normalized internal turn object.
2. Store per-`context_id` transcript and latest tool schema.
3. Ask your model for exactly one next assistant step.
4. Convert the model output into canonical A2A parts.
5. Validate outgoing tool names and argument keys against the latest incoming
   `tools` payload before sending.

If you follow the live `tools` payload and return canonical `tool_calls`, the
evaluator side does not care which model provider or internal architecture you
use.
