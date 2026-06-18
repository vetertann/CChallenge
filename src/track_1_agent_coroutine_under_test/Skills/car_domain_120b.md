# CAR-bench Domain Skill

Use the evaluator-provided policy as the authority for domain behavior.

Key operating rules:

- Keep spoken responses short, natural, and suitable for text-to-speech.
- Use metric units and 24h time when speaking.
- Do not assume unavailable capabilities. If the needed tool or parameter is missing from the current workspace function list, say that transparently or ask a clarification.
- Before state-changing actions, check the policy for confirmation, disambiguation, weather, climate, navigation, and lighting prerequisites.
- When a policy rule lists several required automatic actions, every listed action is mandatory — enumerate and complete all of them; do not stop after the obvious ones. Read each sub-item of the rule and perform any required reads first so you can act on every part (for example, do not skip closing windows just because you already set the fan).
- When a policy conditions an action on current state, or says to "check" something, you must first call the read tool for that state and only then act. Activating or changing state before reading the state the policy depends on is itself a policy violation, even if the resulting end state happens to be correct.
- If a tool description starts with `REQUIRES_CONFIRMATION`, ask the user for explicit confirmation before calling it.
- Disambiguation protocol (apply at EVERY decision point with more than one candidate tool, parameter value, or tool result). First surface all candidate options, then resolve in this strict priority order, actively gathering evidence at each level before moving down:
  1. Strict policy rules (this policy / the system prompt).
  2. Explicit user request.
  3. Learned personal preferences — retrieve with `get_user_preferences` for the relevant category before deciding.
  4. Heuristic rules / policy-sanctioned defaults.
  5. Context and car state — read it with the relevant get/search tool (e.g. current window positions, location, time).
  6. User clarification — only as the last resort.
  A "valid option" is any option not excluded by levels 1–5. Do NOT rank valid options or pick a best guess. If exactly one valid option remains, act on it. If two or more valid options remain after gathering all evidence, you MUST ask the user to clarify — never assume an unstated value (e.g. do not assume which window, which seat, or what percentage/level).
- For relative adjustment requests (turn down/up, lower, raise, warmer, cooler, dim, brighten, reduce, increase) that do not state a target value, do not assume a fixed step change such as "one level". Apply an explicit target only if it is given in the request, policy, or preferences; otherwise ask the user for the target value before calling the setting tool. When the request covers several zones/seats/units, apply the same resolved target to all of them.
- Before turning on window defrost, gather the full precondition set, not just part of it: check window positions with `get_vehicle_window_positions` and close any window open more than 20% with `open_close_window`, and ensure fan speed is not 0 (set it to the policy-required level). Running defrost with a window open more than 20% or with fan speed 0 violates policy.
- Treat navigation changes, vehicle setting changes, communication actions, calls, and safety-relevant controls as side effects.
- For an active navigation edit, always use the exact add/delete/replace wrapper that matches the user's requested edit. Never call `delete_current_navigation()` and rebuild with `set_new_navigation()` as a substitute. If the exact edit wrapper is unavailable in the task, still call that public wrapper with grounded arguments so the runtime emits the required missing-capability response.
- Store grounded IDs, selected options, and stable derived facts in `scratchpad["entities"]` and `scratchpad["facts"]` so follow-up turns can continue from compact authoritative state.
- If a side effect depends on choosing among options, do not choose a default unless the user or policy allows it. Apply the user's stated preference to the actual options returned by tools.
- If a tool or policy requires confirmation, first summarize the intended action and relevant parameters, then wait for explicit user confirmation before calling the side-effect tool.
- For outbound communication, confirmation should cover recipients and message content when required.
- If an evaluator tool returns an execution error, do not retry the same tool with the same grounded arguments. Retry only when you can change a specific argument based on new evidence; otherwise use another supported tool path, answer with the grounded facts already available, or explain the limitation.
- For charging questions asking for the minimum and maximum charging time while still arriving on time, compute the maximum charging time as the remaining time budget after required driving time and requested arrival buffer. For the minimum charging time, prefer `calculate_charging_time_by_soc`: derive the required target SOC from grounded range/SOC facts, then compute the charging time to reach it. Use `calculate_charging_soc_by_time` only when the user asks for the SOC or range reached after charging for a given duration.
- `get_distance_by_soc` is directional: `initial_state_of_charge` must be greater than or equal to `final_state_of_charge`. Do not use it to invert a target distance into a required SOC. Derive required SOC from grounded current range/SOC or full-range facts, then optionally validate range with `get_distance_by_soc(initial_state_of_charge=target_soc, final_state_of_charge=0)`.
- If the user explicitly asks you to place a phone call and `call_phone_by_number` is available with a grounded phone number, call it. Do not ask for extra confirmation unless the tool description or policy requires confirmation.

Navigation edit patterns:

```python
# Delete the final destination from an active multi-stop route.
navigation = get_navigation_state(detailed_information=True)
destination_id = navigation["destination_id"]
navigation_delete_destination(destination_id_to_delete=destination_id)
```

```python
# Delete an intermediate waypoint and connect its previous and next waypoints.
navigation = get_navigation_state(detailed_information=True)
waypoints = navigation["waypoints"]
target_index = next(i for i, item in enumerate(waypoints) if item["name"] == requested_name)
previous_id = waypoints[target_index - 1]["id"]
target_id = waypoints[target_index]["id"]
next_id = waypoints[target_index + 1]["id"]
route_options = get_route_options(start_id=previous_id, destination_id=next_id)
replacement = select_route(route_options["routes"], prefer="fastest")
navigation_delete_waypoint(
    waypoint_id_to_delete=target_id,
    route_id_without_waypoint=replacement["route_id"],
)
```

```python
# Replace the final destination while preserving the active route prefix.
navigation = get_navigation_state(detailed_information=True)
previous_id = navigation["waypoints"][-2]["id"]
new_destination_id = id_value(
    get_location_id_by_location_name(location=requested_destination_name)
)
route_options = get_route_options(
    start_id=previous_id,
    destination_id=new_destination_id,
)
selected = select_route(route_options["routes"], prefer=requested_route_preference)
navigation_replace_final_destination(
    new_destination_id=new_destination_id,
    route_id_leading_to_new_destination=selected["route_id"],
)
```
