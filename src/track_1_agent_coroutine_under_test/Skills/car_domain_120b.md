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
  - Direction only, such as "increase the fan", "make it warmer", or "dim the lights": no amount or final value is specified. The same applies to "turn on" for a multi-level control such as seat heating when no level is given. Do not assume level 1, level 3, one degree, or any other default. First apply strict policy, explicit user constraints, stored preferences, and current state if they uniquely resolve the value; otherwise ask a short clarification such as "What fan level would you like?" or "What seat-heating level should I use?" before calling a setter.
  - Explicit delta, such as "increase it by two levels" or "lower both temperatures by 2 degrees": read the current value for every affected control, calculate `current +/- delta`, and call the setter with the calculated target. No clarification is needed unless a calculated target is invalid or another ambiguity remains.
  - Explicit target, such as "set the fan to level 2" or "set the driver temperature to 24 degrees": use that target directly after any policy-required state reads.
  A direction-only request does not change state. If a later follow-up supplies a delta, calculate it from the actual current state; never include an imagined change from the earlier vague request. Explicitly named controls and zones constrain scope: "driver seat" does not mean all occupied seats, and "fan speed" does not mean air-recirculation mode.
- For relative fan-speed requests with a stated amount, prefer `increase_fan_speed(steps=...)` or `decrease_fan_speed(steps=...)`; these helpers read the current climate settings and then call `set_fan_speed` with the calculated level. If the current fan speed is unavailable, say that the relative change cannot be calculated because the car system did not provide the current fan speed; do not ask the user to supply system state. For driver/passenger climate sync, prefer `sync_climate_zone(source_zone=..., target_zone=...)` so source and target are not reversed. "Set driver to match passenger" means `source_zone="PASSENGER", target_zone="DRIVER"`; "set passenger to match driver" means `source_zone="DRIVER", target_zone="PASSENGER"`. If one request has multiple sync clauses naming the same target side, keep that same target for every included subsystem; do not make a second opposite-direction call for seat heating. For AC plus stored air-circulation preference, call `set_air_conditioning_on_safe(use_preferred_air_circulation=True)` after you explicitly resolve that the request asks for stored preference; otherwise leave the flag false. For seat heating, explicit zones constrain scope: pass `seat_zone="DRIVER"` or `seat_zone="PASSENGER"` when that zone is resolved; omit `seat_zone` only when the request really covers all occupied front seats. For energy-saving cleanup of seat heating on unoccupied front seats, call `turn_off_unoccupied_seat_heating()`; it reads occupancy/current levels and does not change occupied seats.
- For broad comfort requests, do not choose a side effect before the user picks an option and gives any missing amount. Use `present_climate_comfort_options(intent="too_warm")` or `present_climate_comfort_options(intent="stuffy_air")`; the helper asks options and performs no side effects. After the follow-up, call the appropriate setter or helper with the explicit value.

```python
# User: "Increase the fan speed by two levels."
increase_fan_speed(steps=2)
# If current fan_speed is unavailable, answer:
# "I can't increase the fan speed by 2 levels because I looked it up and the car system did not provide the current fan speed."

# User: "I'm feeling too warm." The action is not resolved yet.
present_climate_comfort_options(intent="too_warm")

# Follow-up: "Turn down the seat heating for both of us to level 1."
set_seat_heating(seat_zone="ALL_ZONES", level=1)

# User: "Turn off seat heating where nobody is sitting to save energy."
turn_off_unoccupied_seat_heating()

# User: "It feels stuffy, get some air circulating." The amount/action is not resolved yet.
present_climate_comfort_options(intent="stuffy_air")

# Follow-up: "Increase the fan speed by two levels and keep everything else the same."
increase_fan_speed(steps=2)

# User: "make the driver side match the passenger side"
sync_climate_zone(source_zone="PASSENGER", target_zone="DRIVER")

# User: "sync the driver climate and driver seat heating to match passenger"
sync_climate_zone(source_zone="PASSENGER", target_zone="DRIVER", include_temperature=True, include_seat_heating=True)

# User: "copy the driver settings to the passenger side"
sync_climate_zone(source_zone="DRIVER", target_zone="PASSENGER")

# User: "Set the driver zone temperature to 24 degrees Celsius. Turn on driver seat heating and set steering wheel heating to match the seat heating level."
set_climate_temperature_safe(seat_zone="DRIVER", temperature=24)
set_occupied_seat_heating(seat_zone="DRIVER", level=2)
set_steering_wheel_heating(level=2)
```
- Before turning on front or all-window defrost, prefer `set_window_defrost_safe(defrost_window="FRONT")` or `set_window_defrost_safe(defrost_window="ALL")`. The helper gathers the full precondition set, closes windows that are open more than 20%, safely closes controllable windows whose position is unavailable before enabling AC, applies a stored defrost airflow preference if it still includes `WINDSHIELD` such as `WINDSHIELD_FEET`, otherwise preserves any current airflow mode that already includes `WINDSHIELD`, otherwise sets airflow to `WINDSHIELD`, and reports missing required tools directly. `defrost_front_window()` is the shorthand for front defrost.
- For window movement, first resolve the target window and target percentage through the disambiguation protocol. If the percentage is not resolved, ask the user before any window side effect. Once the target is resolved, prefer `open_close_window_safe(window=..., percentage=..., target_is_explicit=True)`. Use `target_is_explicit=True` only when that exact percentage came from the user, policy, or a resolved follow-up. The helper checks AC state first for openings above 25% and asks confirmation if AC is on or if AC status was checked but unavailable. Do not bypass that by calling the raw window tool manually.
- When the same user request asks to open windows and turn AC on, preserve the policy order instead of treating the two requests independently. If the window percentage is already resolved, open the requested windows first, then call `set_air_conditioning_on_safe()` so policy 011 can close windows above 20% before AC is enabled, then set any requested air-circulation mode. Do not ask to reopen the windows after AC is already on unless the user explicitly says they want the windows open despite the AC/window energy conflict. If the user accepts the policy-closing behavior, the correct final state can be AC on with fresh-air circulation and windows closed.
- For sunroof movement, first resolve the target percentage through the disambiguation protocol. If the percentage is not resolved, ask the user before any sunroof side effect. Once the target is resolved, prefer `open_sunroof_safe(percentage=..., target_is_explicit=True)`. Use `target_is_explicit=True` only when that exact percentage came from the user, policy, or a resolved follow-up. If weather was checked but the condition is unavailable, the helper asks confirmation instead of claiming the capability is unavailable.
- For high beams and fog lights, prefer the lighting policy helpers instead of manually sequencing the raw status read and setter. The helpers perform the required exterior-light/weather reads, apply the policy, request confirmation when needed, and then call the raw setter only when appropriate.
- For broad exterior-light requests, resolve the intent from the task context yourself, then pass an explicit helper intent. Do not pass raw user wording and do not make the helper infer from user text. If the user only says to "turn on the lights" and gives no interior, ambient, reading-light, or color clue, do not ask which lights first; use `set_exterior_lights_safe(intent="improve_visibility")` so the helper can check weather and exterior-light state before any side effect. If the user says headlights, use `intent="turn_on_headlights"`. If the user says exterior lights off, use `intent="turn_off_exterior_lights"`.
- Requests about reading lights, cabin lights, lights inside the car, or lights for occupied seats are interior-light requests, not exterior visibility requests. If the target is occupied seats and the on/off state is resolved, prefer `set_occupied_reading_lights(on=True/False)`. If the target is one explicit reading-light position, call `set_reading_light(...)` directly with that position.

```python
# User: "turn on the high beam headlights"
set_high_beams_on_safe()

# User: "turn on the fog lights"
set_fog_lights_on_safe()

# User: "turn on the lights" with no interior/ambient/reading/color clue.
# The helper checks weather and exterior-light state before acting.
set_exterior_lights_safe(intent="improve_visibility")

# User asks to turn on headlights while low beams may already be on.
set_exterior_lights_safe(intent="turn_on_headlights")

# User asks to turn off exterior lights; helper reads state and only turns off
# lights known to be on.
set_exterior_lights_safe(intent="turn_off_exterior_lights")

# User asks to turn on reading lights for occupied seats.
set_occupied_reading_lights(on=True)

# User asks to turn off the driver's reading light.
set_reading_light(position="DRIVER", on=False)
```
- For simultaneous window plus AC requests, execute the user's requested window opening before AC, then let the AC helper enforce policy 011. Do not create a second confirmation to reopen the windows after AC closes them unless the user explicitly insists on windows open with AC.

```python
# User first clarified: "Open all windows to 50%, turn on AC, and use fresh air."
open_close_window_safe(window="ALL", percentage=50, target_is_explicit=True)
set_air_conditioning_on_safe()
set_air_circulation(mode="FRESH_AIR")
respond("AC is on with fresh-air circulation. I closed the windows as required when turning AC on.")
```
- Treat navigation changes, vehicle setting changes, communication actions, calls, and safety-relevant controls as side effects.
- For an active navigation edit, always use the exact add/delete/replace wrapper that matches the user's requested edit. Never call `delete_current_navigation()` and rebuild with `set_new_navigation()` as a substitute. If the exact edit wrapper is unavailable in the task, still call that public wrapper with grounded arguments so the runtime emits the required missing-capability response.
- Store grounded IDs, selected options, and stable derived facts in `scratchpad["entities"]` and `scratchpad["facts"]` so follow-up turns can continue from compact authoritative state.
- After `respond(...)`, normally let the code finish. If you need an early stop inside a branch, call `stop_after_response()` immediately after `respond(...)`; do not raise `SystemExit`.
- Successful helpers are building blocks for compound requests. Complete every remaining subgoal, then compose one response using all relevant `scratchpad["facts"]["pending_helper_messages"]`; mandatory policy disclosures are preserved by the runtime even if later helper messages are added.
- When an identical read returns `cached: True` and `no_progress: True`, reuse its successful result instead of issuing the same read again. A successful state-changing action invalidates the cache and permits a fresh read.
- Treat route options and route selections as revision-bound facts. After any successful navigation mutation, use the updated `navigation_state` and do not reuse invalidated route options from an earlier revision.
- `scratchpad["entities"]["selected_route"]` is one mutable latest-selection slot. For multi-leg navigation, copy the first leg's route ID into its own variable before selecting the second leg, then pass the ordered route-ID variables to `set_new_navigation`.
- If the user explicitly asks to set/start navigation and you have the grounded route IDs required for that navigation, the next action is the navigation mutation (`set_new_navigation(...)` or the matching active-route edit wrapper). Do not answer "the navigation control call has not completed" while the required route IDs are already known; call the navigation tool.
- Before asking for a contact field or charging-plug ID, inspect normalized stored entities: contacts may expose `first_name`, `last_name`, and `display_name`; charging POIs may expose `charging_plugs`, `plug_ids`, and `available_plug_ids`.
- Facts available through a read or search tool are not missing user information. For "my next meeting" or "my next calendar event", call `get_next_calendar_entry()` first. Its `next_entry` exposes direct time/location aliases: `start_hour`, `start_minute`, `start_time_hour`, `start_time_minute`, `start_minutes`, `location`, and `location_name`. For current charging state, current navigation, or nearby chargers, call the relevant tool first and ask only if its successful result still does not resolve the required fact. When the user asks whether a trip needs charging, you must read `get_charging_specs_and_status()` before answering; route distance or POI search results alone do not prove whether the car can reach the destination.
- Do not claim that a search found zero results unless that search was actually called successfully for the same category and current route, location, and revision.
- If a side effect depends on choosing among options, do not choose a default unless the user or policy allows it. Apply the user's stated preference to the actual options returned by tools.
- If the user asks for route selection according to their preferences, prefer `select_route_by_user_preferences(route_options["routes"])` after `get_route_options(...)`. It reads stored route-selection preferences and applies supported rules such as fastest, shortest, avoiding tolls, or toll-free within a minute threshold. If it returns `UNAVAILABLE` or `AMBIGUOUS`, continue with the normal disambiguation protocol instead of guessing.
- If a tool or policy requires confirmation, call its wrapper with the fully grounded intended arguments. The runtime presents the confirmation request and `handle_pending_confirmation()` executes that stored action after a clear yes.
- For outbound communication, ground recipients and every requested message fact before the first wrapper call so the stored confirmation covers a complete final message. Do not trigger confirmation while research, route planning, charging calculations, or message composition remains unfinished.
- For EV trip-planning emails that include route/travel details, gather the charging status/range facts before the first `send_email(...)` confirmation request. The route distance and duration alone are not enough to decide whether charging stops should be mentioned. If remaining range is unavailable after `get_charging_specs_and_status()`, say that charging-stop planning cannot be completed from the available car data instead of sending an incomplete route-only email.
- If the user chooses to charge at the current location before a long trip, keep this as a current-location charging plan: search chargers with `search_poi_at_location(location_id=policy_location_id(), category_poi="charging_stations")`, select the grounded station/plug, call `calculate_charging_time_by_soc(...)`, then call `get_distance_by_soc(initial_state_of_charge=target_soc, final_state_of_charge=0)` before deciding whether another charging stop is needed. Do not replace this with an along-route charger unless the user asks for an along-route stop.
- If the user asks how many route charging stops are needed when repeatedly charging between two SOC values, prefer `estimate_charging_stops_for_route_by_soc_window(...)`. Pass the grounded destination id, the lower SOC, the upper SOC, and the resolved route selector if one is available. This helper calls the official route lookup and `get_distance_by_soc(initial_state_of_charge=<upper>, final_state_of_charge=<lower>)`; do not estimate the SOC-window range from `remaining_range` arithmetic when the official tool is available.
- If an evaluator tool returns an execution error, do not retry the same tool with the same grounded arguments. Retry only when you can change a specific argument based on new evidence; otherwise use another supported tool path, answer with the grounded facts already available, or explain the limitation.
- For charging questions asking for the minimum and maximum charging time while still arriving on time to the next meeting, prefer `plan_charging_for_next_meeting(range_buffer_km=..., arrival_buffer_minutes=...)`. It returns `min_charging_minutes`, `max_charging_minutes`, the selected fastest charger/plug, provider phone facts, and `navigation_route_ids` for current location -> charger -> meeting. Maximum is the remaining schedule window after required driving time and requested arrival buffer; it is not "time to full". If the follow-up asks to set navigation through that charging stop, use the returned/stored two-leg `navigation_route_ids`, not the direct route to the meeting. If calculating manually, derive the minimum target SOC from grounded range/SOC facts and use `calculate_charging_time_by_soc` for the minimum. Use `calculate_charging_soc_by_time` only when the user asks for the SOC or range reached after charging for a given duration.
- `get_distance_by_soc` is directional: `initial_state_of_charge` must be greater than or equal to `final_state_of_charge`. Do not use it to invert a target distance into a required SOC. Derive required SOC from grounded current range/SOC or full-range facts, then optionally validate range with `get_distance_by_soc(initial_state_of_charge=target_soc, final_state_of_charge=0)`.
- If the user explicitly asks you to place a phone call and `call_phone_by_number` is available with a grounded phone number, call it. Do not ask for extra confirmation unless the tool description or policy requires confirmation. If the user asks to call a charging-station provider to reserve or check a plug, your supported action is the phone call; do not refuse just because there is no separate reservation API. Prefer `call_selected_charging_provider()` when a charger was already selected.
- A POI's `navigation_id`/`poi_id` identifies the actual station, restaurant, or other place. Its `host_location_id`/`corresponding_location_id` identifies only the containing city or area. When the user asks to navigate to the POI, route to the POI ID, not the host location. Keep the POI name and ID together in your variables and response planning, e.g. `selected_poi = {"name": "Mesón del Asador", "navigation_id": "poi_res_...", "host_location_id": "loc_mad_..."}`.
- On a follow-up that switches the route to the current final destination, read `get_navigation_state(...)` and use its current `destination_id`. Do not reuse a destination remembered before the most recent navigation edit.
- Current navigation is preflighted into `scratchpad["entities"]["navigation_state"]` before the first model decision when available. Use its waypoint order and route shape directly; call `get_navigation_state(...)` only if that state is absent or stale.
- For a final-destination replacement, inspect the current waypoint order and branch explicitly. If the active route is a single start-to-destination segment and route lookup returns multiple alternatives, present the fastest/shortest route information and wait unless an explicit model-resolved route choice, stored preferences, or unique route metadata already selects exactly one route. If exactly one route is selected or only one route exists, call `navigation_replace_final_destination(...)` with that grounded route. For multi-stop route construction or replacement, policy 022 supplies the proactive-fastest default per new segment unless the user or stored preferences specify another route.
- For deleting an intermediate waypoint, the replacement route must connect the deleted waypoint's previous and next waypoints. If the user/model has already selected one grounded connecting route, pass that `route_id_without_waypoint`; the wrapper preserves it. If no connecting route is selected and no stored/explicit route preference applies, policy 022 supplies the fastest previous-to-next default. Do not let the default override an explicit non-fastest route choice.
- Before offering one particular route for the user to accept, record it with `select_route(..., route_id=...)`. If the next user message accepts that route, the fresh `selected_route` is an explicit choice and should be reused without another clarification. Presenting several alternatives does not itself choose one.
- Route dicts include `display` with route id, via, full distance, duration, aliases, and toll disclosure. Prefer `route["display"]` when presenting route facts so distance/duration are not accidentally shortened and tolls are mentioned in the same message as the route.
- When a contact lookup returns several people with the recipient's first name, do not choose the first result. Resolve the surname or other identity from the conversation and already-grounded contacts; ask only if multiple candidates still fit. Do not include the recipient's own contact card in a message containing colleagues' contact details unless the user explicitly asks for it.
- If two contact searches express two known constraints, intersect their `contact_ids`. Prefer `unique_id_intersection(last_name_lookup, first_name_lookup)`, which returns the one shared grounded ID and rejects empty or ambiguous intersections. The second normalized lookup also exposes `unique_intersection_with_previous_contact_id` when the overlap with the immediately previous lookup is exactly one ID.
- After reading calendar entries, a contact-name lookup exposes `intersection_with_calendar_attendee_ids` and, when exactly one lookup result is among recent meeting attendees, `unique_calendar_attendee_contact_id`. The unique attendee is ranked first while `unconstrained_contact_ids` preserves the raw order. If the current request asks to message or call a meeting attendee, call `get_contact_id_by_contact_name(..., constrain_to_recent_calendar_attendees=True)` so the result is narrowed to recent attendee IDs, then use that ID with `get_contact_details(...)` before asking which same-name contact the user meant.
- When sending one contact's details to another contact, keep recipient and subject roles in separate variables. After both grounded IDs are resolved, prefer `send_contact_details_to_contact(recipient_contact_id=..., subject_contact_id=..., required_fields=[...])` instead of relying on `last_contacts`, because later lookups overwrite that convenience alias. If you need to read contacts manually before composing a custom email, call `get_contact_details(..., role="email_recipient")` for the recipient and `get_contact_details(..., role="contact_details_subject")` for the contact whose details will appear in the message.

```python
# User: "Send an email to Tina from that meeting"
recipient_lookup = get_contact_id_by_contact_name(
    contact_first_name="Tina",
    constrain_to_recent_calendar_attendees=True,
)
recipient = get_contact_details(
    [recipient_lookup["contact_ids"][0]],
    required_fields=["email"],
    role="email_recipient",
)["first"]
```

```python
# User asks to send one person's phone number to another person.
recipient_lookup = get_contact_id_by_contact_name(contact_first_name="Avery")
subject_lookup = get_contact_id_by_contact_name(contact_last_name="Bennett")
recipient_id = id_value(recipient_lookup)
subject_id = id_value(subject_lookup)
send_contact_details_to_contact(
    recipient_contact_id=recipient_id,
    subject_contact_id=subject_id,
    required_fields=["phone_number"],
)
```

```python
# Custom email body, but still keep contact roles explicit.
recipient = get_contact_details(
    recipient_id,
    required_fields=["email"],
    role="email_recipient",
)["first"]
subject = get_contact_details(
    subject_id,
    required_fields=["phone_number"],
    role="contact_details_subject",
)["first"]
send_email(
    email_addresses=[recipient["email"]],
    content_message=f"Phone number: {subject['phone_number']}",
)
```
- Charging status exposes numeric `remaining_range_km`; use it instead of comparing the formatted `remaining_range` string to a distance.
- For "fastest charger" and charging-time calculations, prefer `select_charging_plug(pois)` after a charging-station search. It selects the highest-power plug and keeps station id, plug id, power, availability, phone number, and navigation id together. Use `require_available=True` only when current availability is a hard user constraint; for time calculation, an occupied high-power plug can still be the fastest charger if the user allows it.
- If the user explicitly chooses a named POI from search results, first resolve that exact POI with `select_poi(...)`, then pass only that POI to downstream helpers. Use `role="charging_stop"`, `role="meal_stop"`, `role="destination"`, or another explicit plan role when that role is already resolved, so later steps can use `scratchpad["entities"]["selected_<role>_poi"]` instead of a generic latest-POI alias. Do not call `select_charging_plug(pois=all_results)` after the user picked a specific station, because that helper chooses the highest-power plug across everything it receives.
- If the user asks for a POI that will still be open when you arrive at a route stop, first select or remember the route to that stop, then call `select_poi_at_location_open_at_route_arrival(...)`. Do not use `filters=["any::currently_open"]` unless the user explicitly means open now. Current-open status answers a different question than arrival-open status.
- If charging is needed for an active route or planned trip, search charging stations along the selected route with `search_poi_along_the_route(...)` unless the user explicitly asks for chargers near a specific city or POI. Do not replace a route-based charging search with `search_poi_at_location(...)` just because a waypoint or destination city is known.
- If navigation needs an intermediate route stop of one POI category and another POI category open at the same route position during a resolved time window, prefer `set_navigation_via_route_stop_with_open_poi(...)`. The model supplies the grounded destination, stop category, companion category, route preference, and clock window; the helper derives route-kilometer buckets from route facts and policy time, searches both categories along the selected route, pairs same-position POIs, checks opening hours, and sets the two-leg navigation through the stop.
- After deleting or replacing a waypoint, use the newly created route segment for charging searches that are still about that trip. For example, after deleting Bonn from Brussels -> Bonn -> Berlin, a charging-station search for the trip should use `search_poi_along_the_route(route_id=<new Brussels-Berlin route>, ...)`, not `search_poi_at_location(location_id="Berlin", ...)`.
- If navigation is inactive and the user only asked to plan, inspect, email, or search along a route, do not call `set_new_navigation(...)` just to make route POI search easier. Use the known planned route id with `search_charging_stations_on_route(route_id=..., at_kilometer=...)`.
- When initially answering a planning-only route request, do not ask whether to "start navigation" or "set navigation" unless the user asked for navigation. Say that the route is selected for planning, charging search, email, or route details. Offering navigation in that first response turns a planning task into an unintended side effect.
- In a plan-only conversation, "confirm the fastest route" or "use that route" selects the route for the requested planning, email, or search work. It is not a navigation mutation unless the user explicitly asks to start, set, or update navigation.
- Do not add `set_new_navigation(...)` as an extra side effect in a route planning turn whose requested actions are route inspection, charging search, contact lookup, or email. Route selection is enough for those actions unless navigation itself was explicitly requested.
- For an active trip where the user gives a route kilometer such as "around 100 km from here", prefer `search_charging_stations_on_active_route(at_kilometer=100)`. The model resolves the number; the helper reads active navigation and calls `search_poi_along_the_route(...)`. Do not use `search_poi_at_location(...)` for that request unless the user explicitly asks for chargers near a specific city, POI, or current location.
- For an active multi-stop navigation, "along the way" means the current active route segments in `navigation_state["route_ids"]`. Do not replace those segments with a newly fetched direct start-to-final route unless the user explicitly asks to remove the intermediate stop(s) or switch to a direct route.
- Weather reads expose the active slot directly: after `get_weather(...)`, use `weather["temperature_c"]`, `weather["condition"]`, and `scratchpad["entities"]["last_weather"]` instead of assuming the nested `current_slot` is missing. If a conditional request such as opening the sunroof depends on outside temperature, read weather first; if the temperature qualifies, continue normal disambiguation for any still-missing sunroof percentage before calling `open_sunroof_safe(...)`.
- If navigation depends on weather at the destination, check weather at route-arrival time rather than current time at the remote destination. When the primary branch is a POI inside a primary location, such as a charging station in a city unless it is raining there, prefer `navigate_to_poi_unless_arrival_weather(primary_location_id=..., fallback_destination_id=..., category_poi=..., avoid_conditions=[...], poi_prefer=..., route_prefer=...)` or its longer alias `navigate_to_poi_by_arrival_weather(...)`. When both branches are direct destinations, prefer `navigate_by_arrival_weather(primary_destination_id=..., fallback_destination_id=..., avoid_conditions=[...], route_prefer=...)`. The model supplies the blocked conditions and any resolved POI/route preference; the helper selects the route, checks arrival-time weather, branches, and calls `set_new_navigation(...)`. If the user explicitly asks for the shortest route in this protocol, pass `route_prefer="shortest"`; if they ask for fastest, pass `route_prefer="fastest"`. Use `get_weather_at_route_arrival(location_or_poi_id=destination_id)` for read-only checks. Do not manually chain route lookup, weather lookup, POI search, and `set_new_navigation(...)` when the full helper represents the request. Do not force `shortest` or another non-default route unless that preference is grounded.

```python
# User: "Navigate to the primary city unless it will be raining there when we arrive;
# otherwise use the fallback city. For the route, use the shortest option."
primary_id = id_value(get_location_id_by_location_name(location=primary_city))
fallback_id = id_value(get_location_id_by_location_name(location=fallback_city))
navigate_by_arrival_weather(
    primary_destination_id=primary_id,
    fallback_destination_id=fallback_id,
    avoid_conditions=["rain"],
    route_prefer="shortest",
)
```

```python
# User: "Navigate to a charging station in the primary city unless it will be
# raining there when we arrive; otherwise navigate to the fallback city. Pick the
# fastest charging station, and use the shortest route."
primary_location_id = id_value(get_location_id_by_location_name(location=primary_city))
fallback_id = id_value(get_location_id_by_location_name(location=fallback_city))
navigate_to_poi_unless_arrival_weather(
    primary_location_id=primary_location_id,
    fallback_destination_id=fallback_id,
    category_poi="charging_stations",
    avoid_conditions=["rain"],
    poi_prefer="fastest_charging",
    route_prefer="shortest",
)
```
- For charging on a later segment of an active multi-stop route, account for energy/range consumed before that segment. A current-location range must not be treated as the range still available at the intermediate waypoint. Derive the range or SOC expected on arrival at that waypoint, then calculate the kilometer on the following segment where the requested reserve is reached.
- For linear range arithmetic on a later segment: derive full-range distance from grounded current SOC and remaining range, subtract the distance traveled before the segment from current remaining range, convert that arrival range back to arrival SOC, then calculate the distance from the segment start until the requested reserve SOC. Use those derived values only to choose parameters for official charging and POI tools.
- For active-route charging requests where the user gives a reserve SOC such as "keep 15% battery", prefer `find_charging_stop_on_active_route_by_soc(reserve_state_of_charge=15)`. The model resolves the number from the request; the helper handles active-route segment math, calls the official distance/routing/POI tools, and stores selected charger/provider facts for follow-ups such as calling the provider. Do not pass raw user text to this helper.
- For conditional requests, call the read that decides the condition first, choose exactly one branch from that result, and perform only that branch. Use `policy_now()` when the user means current weather but gives no time.

Navigation edit patterns:

```python
# Navigation through a charging stop where fast food is open during a resolved time window.
destination_id = id_value(get_location_id_by_location_name(location=resolved_destination_name))
plan = set_navigation_via_route_stop_with_open_poi(
    destination_id=destination_id,
    stop_category_poi="charging_stations",
    companion_category_poi="fast_food",
    window_start_hour=19,
    window_start_minute=0,
    window_end_hour=19,
    window_end_minute=45,
    route_prefer="fastest",
)
respond(
    f"Navigation is set via {plan['selected_stop']['name']}, "
    f"where {plan['selected_companion_poi']['name']} is open at the stop."
)
```

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
# User: "Use my route preferences."
route_options = get_route_options(start_id=start_id, destination_id=destination_id)
preferred = select_route_by_user_preferences(route_options["routes"])
if preferred["status"] == "SUCCESS":
    route_id = preferred["selected_route_id"]
else:
    respond("I couldn't resolve one route from your stored preferences. Which route should I use?")
```

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
selected_stop = select_poi(
    scratchpad["entities"]["last_pois"],
    name="Ionity",
    role="charging_stop",
)
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
# User selected a current-location charger before a long route email.
charging = get_charging_specs_and_status()
pois = search_poi_at_location(
    location_id=policy_location_id(),
    category_poi="charging_stations",
)
plug = select_charging_plug(pois=pois)
charge_time = calculate_charging_time_by_soc(
    charging_station_id=plug["charging_station_id"],
    charging_station_plug_id=plug["charging_station_plug_id"],
    start_state_of_charge=charging["state_of_charge"],
    target_state_of_charge=100,
)
full_range = get_distance_by_soc_value(
    initial_state_of_charge=100,
    final_state_of_charge=0,
)
needs_later_stop = full_range["distance_km"] < selected_route["distance_km"]
```

```python
# Active multi-stop route, user asks for a charger around the point where 15% remains.
# The user-facing "15%" has already been resolved by the model; the helper does
# not inspect raw user text.
charging_search = find_charging_stop_on_active_route_by_soc(
    reserve_state_of_charge=15,
)
plug = charging_search["selected_charging_plug"]
respond(
    f"I found {plug['station_name']} near kilometer "
    f"{charging_search['search_at_kilometer']} on the active route segment."
)
```

```python
# Active road trip, user asks for chargers about 100 km from here.
# The model resolves the kilometer; the helper reads active route state and
# emits the route-based POI search.
charging_search = search_charging_stations_on_active_route(at_kilometer=100)
plug = charging_search["selected_charging_plug"]
charge_time = calculate_charging_time_by_soc(
    charging_station_id=plug["charging_station_id"],
    charging_station_plug_id=plug["charging_station_plug_id"],
    start_state_of_charge=current_soc,
    target_state_of_charge=80,
)
```

```python
# Planned route only, user did not ask to start navigation.
# Do not call set_new_navigation just to search route POIs.
route_options = get_route_options(start_id=current_id, destination_id=destination_id)
selected_route = select_route(route_options["routes"], prefer="fastest")
charging_search = search_charging_stations_on_route(
    route_id=selected_route["route_id"],
    at_kilometer=150,
)
plug = charging_search["selected_charging_plug"]
respond(
    f"I found {plug['station_name']} near kilometer "
    f"{charging_search['at_kilometer']:.0f} of the planned route."
)
```

```python
# Follow-up after route planning: user says "use the fastest route" and asks
# for charging/email work, but still does not ask to start navigation.
selected_route = select_route(last_route_options["routes"], prefer="fastest")
charging_search = search_charging_stations_on_route(
    route_id=selected_route["route_id"],
    at_kilometer=150,
)
# Continue contact/email flow. Do not call set_new_navigation here.
```

```python
# Charging-stop count for a trip where the user will repeatedly charge at 10% up to 80%.
# The model resolves Madrid, 10, 80, and "fastest"; the helper does the route/SOC math
# through official evaluator tools.
destination_id = id_value(get_location_id_by_location_name(location="Madrid"))
estimate = estimate_charging_stops_for_route_by_soc_window(
    destination_id=destination_id,
    charge_from_state_of_charge=10,
    charge_to_state_of_charge=80,
    route_prefer="fastest",
)
respond(
    f"The fastest route is about {estimate['route_distance_km']:.0f} km. "
    f"Driving from 80% down to 10% gives about "
    f"{estimate['range_per_charge_window_km']:.0f} km, so you need about "
    f"{estimate['estimated_charging_stops']} charging stops."
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

If a previous Python block already fetched both legs but stopped before the
mutation, continue by using the stored route IDs and calling
`set_new_navigation(route_ids=[to_stop_route_id, to_final_route_id])`. Do not
ask the user again and do not say the navigation call has not completed when the
remaining step is simply to execute it.

```python
# User asks for navigation to a city, then to a supermarket there that is open
# when the car arrives.
city_id = id_value(get_location_id_by_location_name(location=requested_city_name))
to_city_options = get_route_options(start_id=policy_location_id(), destination_id=city_id)

# Apply the actual route rule from the user/policy/preferences, then remember
# the exact first leg. Do not let later route selection overwrite this variable.
to_city = select_route_by_user_preferences(to_city_options["routes"])
if to_city["status"] != "SUCCESS":
    respond("I couldn't resolve one route from your stored preferences. Which route should I use?")
    stop_after_response()
to_city_route_id = to_city["selected_route_id"]
to_city_route = to_city["route"]

open_poi = select_poi_at_location_open_at_route_arrival(
    location_id=city_id,
    category_poi="supermarkets",
    route=to_city_route,
)
if open_poi["status"] == "SUCCESS":
    poi_id = open_poi["navigation_id"]
    to_poi_options = get_route_options(start_id=city_id, destination_id=poi_id)
    to_poi_route_id = select_route(to_poi_options["routes"], prefer="fastest")["selected_route_id"]
    set_new_navigation(route_ids=[to_city_route_id, to_poi_route_id])
elif open_poi["status"] == "AMBIGUOUS":
    names = [poi["name"] for poi in open_poi["open_pois"]]
    respond("Several places are open when we arrive: " + ", ".join(names) + ". Which one should I use?")
else:
    respond("I couldn't find a matching place that is open when we arrive.")
```
