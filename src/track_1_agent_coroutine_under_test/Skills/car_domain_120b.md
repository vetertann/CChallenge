# CAR-bench Domain Skill

Use the evaluator-provided policy as the authority for domain behavior.

Key operating rules:

- Keep spoken responses short, natural, and suitable for text-to-speech.
- Use metric units and 24h time when speaking.
- Do not assume unavailable capabilities. If the needed tool or parameter is missing from the current workspace function list, say that transparently or ask a clarification.
- Before state-changing actions, check the policy for confirmation, disambiguation, weather, climate, navigation, and lighting prerequisites.
- When a policy rule lists several required automatic actions, every listed action is mandatory — enumerate and complete all of them; do not stop after the obvious ones. Read each sub-item of the rule and perform any required reads first so you can act on every part (for example, do not skip closing windows just because you already set the fan).
- When a policy conditions an action on current state, or says to "check" something, you must first call the read tool for that state and only then act. Activating or changing state before reading the state the policy depends on is itself a policy violation, even if the resulting end state happens to be correct.
- If a tool description starts with `REQUIRES_CONFIRMATION`, first ground every required argument, then call the wrapper once. The runtime stores the exact pending action and asks the user for confirmation without executing it. Do not manually ask first, because a later "yes" would have no runtime action to resume.
- Disambiguation protocol (apply at EVERY decision point with more than one candidate tool, parameter value, or tool result). First surface all candidate options, then resolve in this strict priority order, actively gathering evidence at each level before moving down:
  1. Strict policy rules (this policy / the system prompt).
  2. Explicit user request.
  3. Learned personal preferences — retrieve with `get_user_preferences` for the relevant category before deciding.
  4. Heuristic rules / policy-sanctioned defaults.
  5. Context and car state — read it with the relevant get/search tool (e.g. current window positions, location, time).
  6. User clarification — only as the last resort.
  A "valid option" is any option not excluded by levels 1–5. Do NOT rank valid options or pick a best guess. If exactly one valid option remains, act on it. If two or more valid options remain after gathering all evidence, you MUST ask the user to clarify — never assume an unstated value (e.g. do not assume which window, which seat, or what percentage/level).
- Treat setting requests as one of three forms:
  - Direction only, such as "increase the fan", "make it warmer", or "dim the lights": no amount or final value is specified. The same applies to "turn on" for a multi-level control such as seat heating when no level is given. Do not assume level 1, level 3, one degree, or any other default. Ask a short clarification such as "What fan level would you like?" or "What seat-heating level should I use?" before calling a setter.
  - Explicit delta, such as "increase it by two levels" or "lower both temperatures by 2 degrees": read the current value for every affected control, calculate `current +/- delta`, and call the setter with the calculated target. No clarification is needed unless a calculated target is invalid or another ambiguity remains.
  - Explicit target, such as "set the fan to level 2" or "set the driver temperature to 24 degrees": use that target directly after any policy-required state reads.
  A direction-only request does not change state. If a later follow-up supplies a delta, calculate it from the actual current state; never include an imagined change from the earlier vague request. Explicitly named controls and zones constrain scope: "driver seat" does not mean all occupied seats, and "fan speed" does not mean air-recirculation mode.
- For relative fan-speed requests with a stated amount, prefer `increase_fan_speed(steps=...)` or `decrease_fan_speed(steps=...)`; these helpers read the current climate settings and then call `set_fan_speed` with the calculated level. If the current fan speed is unavailable, say that the relative change cannot be calculated because the car system did not provide the current fan speed; do not ask the user to supply system state. For driver/passenger climate sync, prefer `sync_climate_zone(source_zone=..., target_zone=...)` so source and target are not reversed. "Set driver to match passenger" means `source_zone="PASSENGER", target_zone="DRIVER"`; "set passenger to match driver" means `source_zone="DRIVER", target_zone="PASSENGER"`.

```python
# User: "Increase the fan speed by two levels."
increase_fan_speed(steps=2)
# If current fan_speed is unavailable, answer:
# "I can't increase the fan speed by 2 levels because I looked it up and the car system did not provide the current fan speed."

# User: "make the driver side match the passenger side"
sync_climate_zone(source_zone="PASSENGER", target_zone="DRIVER")

# User: "copy the driver settings to the passenger side"
sync_climate_zone(source_zone="DRIVER", target_zone="PASSENGER")
```
- Before turning on front or all-window defrost, prefer `set_window_defrost_safe(defrost_window="FRONT")` or `set_window_defrost_safe(defrost_window="ALL")`. The helper gathers the full precondition set, closes windows that are open more than 20%, safely closes controllable windows whose position is unavailable before enabling AC, sets fan speed/airflow as policy requires, and reports missing required tools directly. `defrost_front_window()` is the shorthand for front defrost.
- For opening any window above 25%, prefer `open_close_window_safe(window=..., percentage=...)`. It checks AC state first and asks confirmation if AC is on or if AC status was checked but unavailable. Do not bypass that by calling the raw window tool manually.
- For sunroof movement, prefer `open_sunroof_safe(percentage=...)`. If weather was checked but the condition is unavailable, the helper asks confirmation instead of claiming the capability is unavailable.
- For high beams and fog lights, prefer the lighting policy helpers instead of manually sequencing the raw status read and setter. The helpers perform the required exterior-light/weather reads, apply the policy, request confirmation when needed, and then call the raw setter only when appropriate.

```python
# User: "turn on the high beam headlights"
set_high_beams_on_safe()

# User: "turn on the fog lights"
set_fog_lights_on_safe()
```
- Treat navigation changes, vehicle setting changes, communication actions, calls, and safety-relevant controls as side effects.
- For an active navigation edit, always use the exact add/delete/replace wrapper that matches the user's requested edit. Never call `delete_current_navigation()` and rebuild with `set_new_navigation()` as a substitute. If the exact edit wrapper is unavailable in the task, still call that public wrapper with grounded arguments so the runtime emits the required missing-capability response.
- Store grounded IDs, selected options, and stable derived facts in `scratchpad["entities"]` and `scratchpad["facts"]` so follow-up turns can continue from compact authoritative state.
- After `respond(...)`, normally let the code finish. If you need an early stop inside a branch, call `stop_after_response()` immediately after `respond(...)`; do not raise `SystemExit`.
- Successful helpers are building blocks for compound requests. Complete every remaining subgoal, then compose one response using all relevant `scratchpad["facts"]["pending_helper_messages"]`; mandatory policy disclosures are preserved by the runtime even if later helper messages are added.
- When an identical read returns `cached: True` and `no_progress: True`, reuse its successful result instead of issuing the same read again. A successful state-changing action invalidates the cache and permits a fresh read.
- Treat route options and route selections as revision-bound facts. After any successful navigation mutation, use the updated `navigation_state` and do not reuse invalidated route options from an earlier revision.
- `scratchpad["entities"]["selected_route"]` is one mutable latest-selection slot. For multi-leg navigation, copy the first leg's route ID into its own variable before selecting the second leg, then pass the ordered route-ID variables to `set_new_navigation`.
- Before asking for a contact field or charging-plug ID, inspect normalized stored entities: contacts may expose `first_name`, `last_name`, and `display_name`; charging POIs may expose `charging_plugs`, `plug_ids`, and `available_plug_ids`.
- Facts available through a read or search tool are not missing user information. For "my next meeting" or "my next calendar event", call `get_next_calendar_entry()` first. Its `next_entry` exposes direct time/location aliases: `start_hour`, `start_minute`, `start_time_hour`, `start_time_minute`, `start_minutes`, `location`, and `location_name`. For current charging state, current navigation, or nearby chargers, call the relevant tool first and ask only if its successful result still does not resolve the required fact. When the user asks whether a trip needs charging, you must read `get_charging_specs_and_status()` before answering; route distance or POI search results alone do not prove whether the car can reach the destination.
- Do not claim that a search found zero results unless that search was actually called successfully for the same category and current route, location, and revision.
- If a side effect depends on choosing among options, do not choose a default unless the user or policy allows it. Apply the user's stated preference to the actual options returned by tools.
- If a tool or policy requires confirmation, call its wrapper with the fully grounded intended arguments. The runtime presents the confirmation request and `handle_pending_confirmation()` executes that stored action after a clear yes.
- For outbound communication, ground recipients and every requested message fact before the first wrapper call so the stored confirmation covers a complete final message. Do not trigger confirmation while research, route planning, charging calculations, or message composition remains unfinished.
- If an evaluator tool returns an execution error, do not retry the same tool with the same grounded arguments. Retry only when you can change a specific argument based on new evidence; otherwise use another supported tool path, answer with the grounded facts already available, or explain the limitation.
- For charging questions asking for the minimum and maximum charging time while still arriving on time to the next meeting, prefer `plan_charging_for_next_meeting(range_buffer_km=..., arrival_buffer_minutes=...)`. It returns `min_charging_minutes`, `max_charging_minutes`, and the selected fastest charger/plug. Maximum is the remaining schedule window after required driving time and requested arrival buffer; it is not "time to full". If calculating manually, derive the minimum target SOC from grounded range/SOC facts and use `calculate_charging_time_by_soc` for the minimum. Use `calculate_charging_soc_by_time` only when the user asks for the SOC or range reached after charging for a given duration.
- `get_distance_by_soc` is directional: `initial_state_of_charge` must be greater than or equal to `final_state_of_charge`. Do not use it to invert a target distance into a required SOC. Derive required SOC from grounded current range/SOC or full-range facts, then optionally validate range with `get_distance_by_soc(initial_state_of_charge=target_soc, final_state_of_charge=0)`.
- If the user explicitly asks you to place a phone call and `call_phone_by_number` is available with a grounded phone number, call it. Do not ask for extra confirmation unless the tool description or policy requires confirmation. If the user asks to call a charging-station provider to reserve or check a plug, your supported action is the phone call; do not refuse just because there is no separate reservation API. Prefer `call_selected_charging_provider()` when a charger was already selected.
- A POI's `navigation_id`/`poi_id` identifies the actual station, restaurant, or other place. Its `host_location_id`/`corresponding_location_id` identifies only the containing city or area. When the user asks to navigate to the POI, route to the POI ID, not the host location. Keep the POI name and ID together in your variables and response planning, e.g. `selected_poi = {"name": "Mesón del Asador", "navigation_id": "poi_res_...", "host_location_id": "loc_mad_..."}`.
- On a follow-up that switches the route to the current final destination, read `get_navigation_state(...)` and use its current `destination_id`. Do not reuse a destination remembered before the most recent navigation edit.
- Current navigation is preflighted into `scratchpad["entities"]["navigation_state"]` before the first model decision when available. Use its waypoint order and route shape directly; call `get_navigation_state(...)` only if that state is absent or stale.
- For a final-destination replacement, inspect the current waypoint order and branch explicitly. If only start and destination remain, the replacement is a single route segment: when multiple alternatives exist, present the required options and wait unless the user explicitly selected one or retrieved preferences uniquely select one. Do not automatically choose fastest. If an intermediate waypoint remains before the new destination, the resulting route is multi-stop: policy 022 applies, so choose the fastest alternative for the new segment unless the user or preferences specify another.
- For deleting an intermediate waypoint with no route preference, after route lookup immediately call `navigation_delete_waypoint` with the fastest previous-to-next route. Do not ask which route unless the user asked for options or gave a non-default route preference.
- Before offering one particular route for the user to accept, record it with `select_route(..., route_id=...)`. If the next user message accepts that route, the fresh `selected_route` is an explicit choice and should be reused without another clarification. Presenting several alternatives does not itself choose one.
- Route dicts include `display` with route id, via, full distance, duration, aliases, and toll disclosure. Prefer `route["display"]` when presenting route facts so distance/duration are not accidentally shortened and tolls are mentioned in the same message as the route.
- When a contact lookup returns several people with the recipient's first name, do not choose the first result. Resolve the surname or other identity from the conversation and already-grounded contacts; ask only if multiple candidates still fit. Do not include the recipient's own contact card in a message containing colleagues' contact details unless the user explicitly asks for it.
- If two contact searches express two known constraints, intersect their `contact_ids`. Prefer `unique_id_intersection(last_name_lookup, first_name_lookup)`, which returns the one shared grounded ID and rejects empty or ambiguous intersections. The second normalized lookup also exposes `unique_intersection_with_previous_contact_id` when the overlap with the immediately previous lookup is exactly one ID.
- Charging status exposes numeric `remaining_range_km`; use it instead of comparing the formatted `remaining_range` string to a distance.
- For "fastest charger" and charging-time calculations, prefer `select_charging_plug(pois)` after a charging-station search. It selects the highest-power plug and keeps station id, plug id, power, availability, phone number, and navigation id together. Use `require_available=True` only when current availability is a hard user constraint; for time calculation, an occupied high-power plug can still be the fastest charger if the user allows it.
- If the user explicitly chooses a named POI from search results, first resolve that exact POI with `select_poi(...)`, then pass only that POI to downstream helpers. Do not call `select_charging_plug(pois=all_results)` after the user picked a specific station, because that helper chooses the highest-power plug across everything it receives.
- If navigation depends on weather at the destination, check weather at route-arrival time rather than current time at the remote destination. Use `get_weather_at_route_arrival(location_or_poi_id=destination_id)` instead of raw `get_weather(...)` with `policy_now()`, because the helper gets/uses route duration and then calls `get_weather` for the destination at the computed arrival hour/minute.
- For charging on a later segment of an active multi-stop route, account for energy/range consumed before that segment. A current-location range must not be treated as the range still available at the intermediate waypoint. Derive the range or SOC expected on arrival at that waypoint, then calculate the kilometer on the following segment where the requested reserve is reached.
- For linear range arithmetic on a later segment: derive full-range distance from grounded current SOC and remaining range, subtract the distance traveled before the segment from current remaining range, convert that arrival range back to arrival SOC, then calculate the distance from the segment start until the requested reserve SOC. Use those derived values only to choose parameters for official charging and POI tools.
- For conditional requests, call the read that decides the condition first, choose exactly one branch from that result, and perform only that branch. Use `policy_now()` when the user means current weather but gives no time.

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
# Use the preflighted state; read again only when it is absent or stale.
navigation = scratchpad["entities"]["navigation_state"]
previous_id = navigation["waypoints"][-2]["id"]
new_destination_id = id_value(
    get_location_id_by_location_name(location=requested_destination_name)
)
route_options = get_route_options(
    start_id=previous_id,
    destination_id=new_destination_id,
)

if not navigation["is_multi_stop"] and requested_route_preference is None:
    # A two-waypoint route has one segment. Present the returned alternatives
    # and wait for the user instead of silently defaulting to fastest.
    lines = [
        f"{index}. Via {route['name_via']}: "
        f"{route['distance_km']} km, "
        f"{route['duration_hours']}h {route['duration_minutes']}m"
        for index, route in enumerate(route_options["routes"], 1)
    ]
    respond("Route options:\n" + "\n".join(lines) + "\nWhich route would you like?")
else:
    # An explicit user/preference choice wins. Otherwise policy 022 supplies
    # the fastest default only for the multi-stop case.
    preference = requested_route_preference or "fastest"
    selected = select_route(route_options["routes"], prefer=preference)
    navigation_replace_final_destination(
        new_destination_id=new_destination_id,
        route_id_leading_to_new_destination=selected["route_id"],
    )
```

For a compound route rule, such as "fastest without tolls" or "toll-free if it
adds no more than 10 minutes", filter and compare the returned route metadata in
Python. Once exactly one route satisfies the rule, record that exact decision
with `select_route(route_options["routes"], route_id=chosen["route_id"])` before
the navigation mutation. Do not pass only `prefer="fastest"` when the rule has
additional constraints.

```python
# Present a specific option and persist exactly that option for a follow-up.
route_options = get_route_options(start_id=start_id, destination_id=destination_id)
second = select_route(route_options["routes"], alias="second")
if second["status"] == "SUCCESS":
    respond(
        f"The second route goes via {second['route']['name_via']}. "
        "Tell me if you want this route."
    )

# On "take that route", reuse the persisted selection and its grounded destination.
# Current navigation may still contain the old destination if no edit occurred yet.
selected = scratchpad["entities"]["selected_route"]
navigation_replace_final_destination(
    new_destination_id=selected["destination_id"],
    route_id_leading_to_new_destination=selected["selected_route_id"],
)
```

For a new multi-leg navigation, preserve each leg's selected route ID before
calling `set_new_navigation`. If the user explicitly asks for a non-default
option on one leg, record that exact leg selection; do not let the default
fastest rule overwrite it.

```python
# User selected a named charging stop, then asks for navigation through it.
selected_stop = select_poi(scratchpad["entities"]["last_pois"], name="Ionity")
plug = select_charging_plug(pois=[selected_stop["poi"]])
charging_time = calculate_charging_time_by_soc(
    charging_station_id=plug["charging_station_id"],
    charging_station_plug_id=plug["charging_station_plug_id"],
    start_state_of_charge=current_soc,
    target_state_of_charge=95,
)
set_new_navigation_via_stop(
    stop_id=selected_stop["navigation_id"],
    final_destination_id=destination_id,
    route_to_stop_prefer="fastest",
    route_to_final_alias="second",
)
```

```python
# Manual equivalent when more than one stop or more custom logic is needed.
to_stop = get_route_options(start_id=current_id, destination_id=charger_id)
to_stop_route_id = select_route(to_stop["routes"], prefer="fastest")["selected_route_id"]

to_final = get_route_options(start_id=charger_id, destination_id=destination_id)
to_final_route_id = select_route(to_final["routes"], alias="second")["selected_route_id"]

set_new_navigation(route_ids=[to_stop_route_id, to_final_route_id])
```
