# A2A Integer Coercion Issue

## Title

A2A data Part coerces integer tool arguments to floats, causing `calculate_charging_soc_by_time` to fail

## Summary

When an A2A agent returns CAR-bench tool calls in a protobuf `data` Part, integer tool arguments are decoded by the evaluator as floats. This breaks CAR-bench tools that require Python integers internally.

In particular, `calculate_charging_soc_by_time` receives `charging_time=40.0` instead of `40`, then raises:

```text
Error: 'float' object cannot be interpreted as an integer
```

This appears to be caused by the A2A protobuf `Struct/Value` representation, where JSON numbers are represented as doubles, combined with evaluator-side `MessageToDict(part.data)`.

## Reproduction

Run train `base_98` through the A2A starter harness.

```toml
[config]
num_trials = 3
task_split = "train"
tasks_base_task_id_filter = ["base_98"]
max_steps = 50
```

During the task, the agent emits the evaluator-visible A2A tool call payload with integer values:

```json
{
  "tool_calls": [
    {
      "tool_name": "calculate_charging_soc_by_time",
      "arguments": {
        "charging_station_id": "poi_cha_363177",
        "charging_station_plug_id": "plg_cha_961357",
        "start_state_of_charge": 20,
        "charging_time": 40
      }
    }
  ]
}
```

This matches the tool schema exposed by the evaluator in the first turn:

```json
{
  "start_state_of_charge": {
    "minimum": 0,
    "type": "integer",
    "description": "The start state of charge of the electric vehicle (in percentage).",
    "maximum": 100
  },
  "charging_time": {
    "minimum": 0,
    "type": "integer",
    "description": "The charging time in minutes."
  }
}
```

The evaluator returns:

```text
Error: 'float' object cannot be interpreted as an integer
```

## Observed Code Path

The agent sends tool calls as an A2A `data` Part:

```python
# src/track_1_agent_under_test/car_bench_agent.py
parts.append(new_data_part({"tool_calls": result.tool_calls}))
```

The installed A2A helper serializes via protobuf `Value`:

```python
def new_data_part(data, media_type=None):
    return Part(
        data=ParseDict(data, struct_pb2.Value()),
        media_type=media_type or "",
    )
```

The evaluator parses it with:

```python
# src/evaluator/car_bench_evaluator.py
data = MessageToDict(part.data)
...
"arguments": json.dumps(tc.get("arguments", {}))
```

After this round trip, integer-looking arguments are floats, for example `40` becomes `40.0`.

The CAR-bench tool then invokes:

```python
# car_bench/envs/car_voice_assistant/tools/charging/get_apis/calculate_charging_soc_by_time.py
for time_minute in range(charging_time):
    ...
```

`range(40.0)` raises the observed `TypeError`.

## Expected Behavior

Integer schema arguments should arrive at CAR-bench tools as Python `int`, or evaluator/tool execution should normalize integer-valued floats back to ints according to the tool schema before invoking tool implementations.

For the example above, `charging_time` should be `40`, not `40.0`.

## Actual Behavior

`charging_time` arrives as `40.0`, which causes `calculate_charging_soc_by_time` to fail with a Python `TypeError`.

The tool failure is returned to the agent as an observation string:

```text
Error: 'float' object cannot be interpreted as an integer
```

However, this generic Python exception is not always reflected in the final `tool_execution_errors` reward field. In observed `base_98` runs, the task could still finish with `r_tool_execution=1.0` and `tool_execution_errors=[]` even after this tool returned the error observation.

This makes the issue partly silent in aggregate metrics: a participant may see a passing task while one of the tool calls failed internally due to A2A numeric coercion.

## Scoring Visibility Concern

CAR-bench tool implementations append explicit validation failures to `tool_execution_errors_during_runtime`, and the final reward calculation uses that list to compute `r_tool_execution`.

The `calculate_charging_soc_by_time` failure happens later, when the implementation executes:

```python
for time_minute in range(charging_time):
    ...
```

If `charging_time` is `40.0`, this raises a generic `TypeError`. The environment catches the exception and returns it as an observation:

```python
except Exception as e:
    observation = f"Error: {e}"
```

Because this generic exception path does not append to `tool_execution_errors_during_runtime`, the final result may not expose the tool failure through `r_tool_execution`.

Observed in train `base_98`:

```json
{
  "r_tool_execution": 1.0,
  "tool_execution_errors": []
}
```

while the trajectory included:

```text
calculate_charging_soc_by_time -> Error: 'float' object cannot be interpreted as an integer
```

This does not make the failure harmless: it can still mislead the agent, waste turns/tokens, and fail tasks where the missing tool result is necessary for the final answer or action.

## Why This Matters

This is hard for participant agents to avoid because the model or agent can emit valid JSON integer arguments, but the A2A `data` transport path changes their Python type before CAR-bench tool execution.

It can affect any CAR-bench tool whose implementation depends on a real Python `int`, not only `calculate_charging_soc_by_time`.

It is also hard to detect from final scores alone because some generic tool exceptions are returned as observations without appearing in `tool_execution_errors`.

## Additional Affected Tool

`planning_tool` appears affected by the same A2A integer-to-float coercion.

The evaluator-provided schema declares integer step indices:

```json
{
  "steps": {
    "items": {
      "properties": {
        "step_dependent_on": {
          "type": "array",
          "items": {"type": "integer"}
        }
      }
    }
  },
  "step_updates": {
    "items": {
      "properties": {
        "step_index": {"type": "integer"}
      }
    }
  }
}
```

After A2A `data` Part serialization and evaluator `MessageToDict(...)`, values become floats:

```json
{
  "tool_calls": [
    {
      "tool_name": "planning_tool",
      "arguments": {
        "steps": [
          {
            "step_description": "a",
            "step_dependent_on": [0.0]
          }
        ],
        "step_updates": [
          {
            "step_index": 0.0
          }
        ]
      }
    }
  ]
}
```

`planning_tool` then rejects the values because its implementation checks for real Python integers:

```python
if not all(isinstance(dep, int) for dep in step["step_dependent_on"]):
    raise ValueError(...)

if not isinstance(update["step_index"], int):
    raise ValueError(...)
```

Observed direct failures with post-A2A-shaped values:

```text
Step 1 'step_dependent_on' must contain only integers
Step index must be an integer
```

## Other Integer-Schema Tool Audit

Other input parameters exposed as `type: integer` were checked. These appear less likely to raise Python type errors from integer-valued floats because they are used for numeric comparison/arithmetic rather than Python int-only operations:

| Tool | Integer-schema arguments | Runtime risk from `1 -> 1.0` |
| --- | --- | --- |
| `get_entries_from_calendar` | `month`, `day` | Low: compared to current date integers; `1.0 == 1` is true in Python. |
| `search_poi_along_the_route` | `at_kilometer` | Low: used in arithmetic distance checks. |
| `convert_route_distance_and_time` | `time_minutes`, `distance_km` | Low: implementation already treats them as numeric values. |
| `get_distance_by_soc` | `initial_state_of_charge`, `final_state_of_charge` | Low: used in comparisons/arithmetic. Output keys may contain `.0`, but no runtime type error observed. |
| `calculate_charging_time_by_soc` | `start_state_of_charge`, `target_state_of_charge` | Low: used in comparisons/arithmetic. Output keys may contain `.0`, but no runtime type error observed. |

## Possible Fixes

1. In the evaluator, before converting participant A2A `tool_calls` into CAR-bench tool calls, normalize arguments using the evaluator-provided tool schema. If a parameter schema says `type: integer` and the value is a float with no fractional part, cast it to `int`.
2. Alternatively, in CAR-bench tool execution, coerce schema-validated integer parameters before invoking tool implementations.
3. As a narrower fix, update brittle tools such as `calculate_charging_soc_by_time` to cast `charging_time = int(charging_time)` after validation when the value is integral.

Option 1 or 2 is preferable because the issue is introduced at the A2A boundary and may affect multiple tools.
