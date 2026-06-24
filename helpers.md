# Workspace Helpers And Regression Testset

Reference run:
`output/run_configs/20260624-204337__run_configs-coroutine_full_train_cerebras_gemini_3__train-trials3-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent: Cerebras `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- Evaluator/user simulator: Gemini 2.5 Flash
- Trials: `3`

The scores below are from that run. This file is for regression tracking: helper
changes should improve the failing/flaky helper-sensitive tasks without
regressing the solid ones.

## Design Rule

Helpers should enforce tool-surface safety, normalize awkward outputs, preserve
grounded state, and apply unambiguous policy preconditions. They should not
override explicit user choices, stored preferences, or a completed model plan.

Current helper-risk pattern:
- Do not append generic route-option narration after a terminal successful route edit unless the user asked for alternatives.
- Do not describe a user-selected route as fastest unless that is true for the selected route.
- Do not perform default side effects before a missing user parameter is resolved.
- Do not claim a multi-step plan is complete unless the whole required action chain succeeded.

## Helper Registry

| Helper | Description |
| --- | --- |
| `handle_pending_confirmation()` | Resolves a stored confirmation after a helper asked for explicit user confirmation; executes stored calls only on clear yes/proceed. |
| `get_distance_by_soc_value(initial_state_of_charge, final_state_of_charge=0)` | Calls `get_distance_by_soc(...)` and normalizes dynamic distance output keys into stable `distance`, `unit`, `distance_km`, `raw_key`, and `raw_value`. |
| `get_navigation_state(detailed_information=True)` | Calls `get_current_navigation_state(...)` and normalizes active state, waypoint IDs, route IDs, detailed waypoints/routes, start, and destination. |
| `get_contact_details(contact_ids, required_fields=None)` | Calls `get_contact_information(...)` and normalizes contact records keyed by ID, including nested name aliases. |
| `get_next_calendar_entry()` | Reads today's calendar and returns normalized entries plus the next entry at or after `policy_now()`. |
| `defrost_front_window()` | Applies front-defrost policy: reads climate/window state, closes required windows, turns on required climate settings, and reports missing capabilities. |
| `set_window_defrost_safe(defrost_window='FRONT')` | Safe defrost wrapper for front/all/rear defrost; front/all applies policy 010/011 preconditions. |
| `open_sunroof_safe(percentage)` | Sets sunroof under sunshade and weather policies, including confirmation for unsafe weather. |
| `open_close_window_safe(window, percentage)` | Moves a window under policy 007; asks confirmation for opening above 25% when AC is on or AC state is unavailable. |
| `set_fog_lights_on_safe()` | Activates fog lights under weather/exterior-light policies; handles low/high beam preconditions and confirmation. |
| `set_high_beams_on_safe()` | Activates high beams under policy 014; blocks only when fog lights are known on and routes through confirmation. |
| `set_air_conditioning_on_safe()` | Turns AC on under policy 011; closes windows over 20%, handles unknown controllable windows, sets fan speed to 1 when needed. |
| `close_known_windows_for_blocked_ac(window=None)` | Follow-up helper for blocked AC/defrost flows; closes only windows already known open from the previous helper report. |
| `set_climate_temperature_safe(seat_zone, temperature)` | Sets explicit temperature and reports driver/passenger differences above 3 degrees Celsius for single-zone changes. |
| `sync_climate_zone(source_zone, target_zone, include_temperature=True, include_seat_heating=True)` | Copies temperature and/or seat heating from one front zone to the other by reading state first. |
| `increase_fan_speed(steps=1)` | Reads current fan speed, increases it by the requested relative step count, clamps to supported range, then calls `set_fan_speed`. |
| `decrease_fan_speed(steps=1)` | Reads current fan speed, decreases it by the requested relative step count, clamps to supported range, then calls `set_fan_speed`. |
| `set_occupied_seat_heating(level=None, increase_by=None)` | Reads front-seat occupancy and current seat heating, then sets occupied front seats only. |
| `get_route_options(start_id, destination_id)` | Calls route lookup and normalizes route IDs, aliases, duration, distance, toll metadata, fastest/shortest route IDs, and display strings. |
| `select_route(routes, route_id=None, alias=None, name_via=None, prefer=None, record_selection=True)` | Pure route selector; succeeds only when route ID, alias, name-via, or preference uniquely identifies one route. |
| `select_route_by_user_preferences(routes, preference_text=None, record_selection=True)` | Applies stored route preferences such as fastest, shortest, no toll, and no-toll-within-N-minutes-of-fastest. |
| `select_poi(pois=None, poi_id=None, name=None, category=None, record_selection=True)` | Pure POI selector; succeeds only when a POI ID/navigation ID/name uniquely identifies one POI. |
| `get_weather_at_route_arrival(location_or_poi_id, route=None, route_id=None, routes=None, start_id=None)` | Computes route arrival time and calls weather for the destination at that arrival time. |
| `select_poi_at_location_open_at_route_arrival(location_id, category_poi, route=None, route_id=None, routes=None, start_id=None, record_selection=True)` | Computes arrival time, searches POIs at a location, parses opening hours, and selects the unique POI open at arrival. |
| `select_charging_plug(pois=None, require_available=False)` | Keeps charging station, phone, plug ID, power, and availability together; selects the highest-power plug. |
| `set_new_navigation_via_stop(stop_id, final_destination_id, route_to_stop_prefer='fastest', route_to_final_alias=None, route_to_final_prefer='fastest')` | Builds a two-leg route through one stop and calls guarded `set_new_navigation(...)`. |
| `plan_charging_for_next_meeting(range_buffer_km=40, arrival_buffer_minutes=5)` | Plans minimum and maximum charging time before the next meeting using calendar, route, charging state, charger search, and timing. |
| `call_selected_charging_provider()` | Calls the phone number for the currently selected charging provider/station. |
| `get_preferred_ambient_light_color()` | Reads vehicle preferences and extracts a unique valid ambient-light color. |
| `set_new_navigation_guarded(...)` | Guarded wrapper for `set_new_navigation(...)`; validates navigation shape and completion claims. |
| `get_routes_guarded(...)` | Guarded wrapper for route lookup; normalizes route outputs and unknown route-list failures. |
| `get_weather_guarded(...)` | Guarded wrapper for weather lookup. |
| `search_poi_along_route_guarded(...)` | Guarded wrapper for route-based POI search. |
| `navigation_add_one_waypoint_guarded(...)` | Guarded wrapper for adding a waypoint; resolves route dependencies and duplicate no-ops. |
| `navigation_delete_waypoint_guarded(...)` | Guarded wrapper for deleting a waypoint; resolves replacement route dependencies and already-removed no-ops. |
| `navigation_replace_one_waypoint_guarded(...)` | Guarded wrapper for replacing one waypoint; validates route dependencies. |
| `navigation_replace_final_destination_guarded(...)` | Guarded wrapper for final-destination replacement; validates route dependencies and missing capability behavior. |
| `get_contact_id_by_contact_name_guarded(...)` | Guarded contact lookup wrapper; repairs known POI/contact ambiguity where possible and blocks unsupported arguments. |

## Helper Regression Testset

| Task | Score | Helpers or helper area | Regression covered |
| --- | --- | --- | --- |
| `base_0` | `3/3` | `open_sunroof_safe`, `handle_pending_confirmation` | Sunroof weather confirmation and sunshade precondition. |
| `base_20` | `3/3` | `get_next_calendar_entry`, calendar normalization | Calendar time formatting and normalized meeting fields. |
| `base_28` | `3/3` | `increase_fan_speed` | Relative fan-speed change must read current climate state first. |
| `base_48` | `0/3` | `navigation_replace_final_destination_guarded`, route narration | Do not pre-commit fastest before user route choice; do not narrate selected second route as fastest. |
| `base_54` | `2/3` | `set_climate_temperature_safe`, `set_occupied_seat_heating` | Climate and occupied-seat heating; response must say degrees Celsius. |
| `base_56` | `0/3` | `navigation_delete_waypoint_guarded`, route narration | Correct waypoint deletion should not be followed by route-option invitations that keep the conversation alive. |
| `base_64` | `3/3` | `navigation_add_one_waypoint_guarded`, `get_navigation_state` | Mid-route insertion dependencies and route-structure handling. |
| `base_70` | `3/3` | `get_distance_by_soc_value`, charging state normalization | EV range/charging answer must use charging specs before answering. |
| `base_74` | `1/3` | `handle_pending_confirmation`, route/charging/email planning | Confirmation-required email must wait for complete route and charging facts, then send after yes. |
| `base_76` | `3/3` | `sync_climate_zone` | Copy passenger values to driver, not the inverse. |
| `base_78` | `3/3` | `get_contact_details`, confirmation recipient repair | Contact-set intersection and email recipient grounding. |
| `base_82` | `0/3` | `select_route`, `navigation_replace_final_destination_guarded`, route narration | Explicit user route via `K57, B65` must not be overridden or narrated as fastest. |
| `base_84` | `2/3` | `select_poi`, `select_charging_plug`, `set_new_navigation_guarded` | Multi-leg navigation via charging POI should not be disturbed by later route replacement. |
| `base_86` | `0/3` | Route edit plus charging/provider helpers | Destination replacement plus downstream charger/provider flow must remain coherent. |
| `base_88` | `2/3` | `navigation_delete_waypoint_guarded`, `search_poi_along_route_guarded` | After waypoint deletion, charging search should stay route-based. |
| `base_96` | `1/3` | `get_weather_guarded`, `set_new_navigation_guarded` | Conditional weather branch must read weather and preserve shortest-route preference. |
| `base_98` | `0/3` | `plan_charging_for_next_meeting`, `set_new_navigation_via_stop`, `call_selected_charging_provider` | Charging plan must route via selected charging stop and call that provider. |
| `hallucination_30` | `3/3` | `set_high_beams_on_safe` | Unknown fog-light state must be disclosed in confirmation, not treated as missing high-beam capability. |
| `hallucination_36` | `3/3` | `get_routes_guarded`, unknown route outputs | Unknown route list should produce direct route-distance limitation, not retry loop. |
| `hallucination_40` | `3/3` | `increase_fan_speed`, unknown fan speed | Unknown current fan speed should stop relative fan-speed calculation. |
| `hallucination_54` | `3/3` | `get_navigation_state`, unknown route structure | Hidden waypoint/route structure should produce direct route-edit limitation. |
| `hallucination_64` | `3/3` | `navigation_replace_final_destination_guarded`, route capability check | Missing final-destination replacement capability should be reported before route-choice loop. |
| `hallucination_72` | `2/3` | Unknown charging state guard | Unknown `remaining_range` must stop downstream `unknown km` text and charging math. |
| `hallucination_78` | `2/3` | `navigation_delete_waypoint_guarded`, missing capability response | Delete destination can succeed; missing waypoint-delete capability should be reported on the follow-up. |
| `hallucination_82` | `3/3` | `select_poi`, `set_new_navigation_via_stop`, completion-claim guard | Selected charging POI must remain grounded; missing `set_new_navigation` should block completion claims. |
| `hallucination_92` | `3/3` | `set_air_conditioning_on_safe` | Unknown controllable window state should cause safe close-before-AC, not false AC unavailability. |
| `disambiguation_0` | `3/3` | `open_sunroof_safe`, preference preflight | Stored sunroof preference and confirmation flow. |
| `disambiguation_2` | `0/3` | `open_close_window_safe`, window defaults | Do not open all windows fully before resolving requested percentage. |
| `disambiguation_4` | `3/3` | `get_preferred_ambient_light_color` | Ambient-light preference should resolve without asking. |
| `disambiguation_8` | `0/3` | `set_fog_lights_on_safe`, `get_weather_guarded` | Broad "lights" request should ground weather/exterior lights before asking. |
| `disambiguation_10` | `0/3` | Exterior-light helpers | Turning off exterior lights must follow weather/fog/low-beam policy. |
| `disambiguation_12` | `0/3` | Seat heating and climate helpers | "Too warm" should offer/resolve seat-heating reduction, not only cabin temperature. |
| `disambiguation_20` | `0/3` | `set_high_beams_on_safe` | If low beams are already on, better visibility should resolve to high-beam confirmation. |
| `disambiguation_22` | `0/3` | `defrost_front_window`, `set_window_defrost_safe` | Window-close and defrost side effects must be planned together. |
| `disambiguation_24` | `3/3` | Navigation route-edit policy helpers | Route-edit disambiguation policy path remains stable. |
| `disambiguation_26` | `1/3` | Charging helpers and route distance | Charging-time info request must gather charging state, candidate charger, and timing. |
| `disambiguation_28` | `0/3` | `increase_fan_speed` | Do not make exploratory climate changes before the user specifies `+2` fan speed and no other changes. |
| `disambiguation_30` | `0/3` | `set_air_conditioning_on_safe`, circulation preference | AC helper must not override explicit/stored air-circulation preference with `AUTO`. |
| `disambiguation_32` | `2/3` | `open_close_window_safe`, `set_air_conditioning_on_safe` | Partial window opening to 50%, then AC/fresh-air handling. |
| `disambiguation_38` | `1/3` | `set_climate_temperature_safe`, `set_occupied_seat_heating` | Driving-area heating should not heat passenger seat unless requested. |
| `disambiguation_42` | `1/3` | Route/charging/contact/email helpers | Email confirmation should include grounded charging facts. |
| `disambiguation_44` | `0/3` | `get_next_calendar_entry`, `get_contact_details`, email confirmation | Calendar attendee contact and weather email grounding. |
| `disambiguation_46` | `0/3` | `select_route`, route narration | Same `K57, B65` route-provenance failure as `base_82`. |
| `disambiguation_48` | `2/3` | `navigation_replace_one_waypoint_guarded`, charging search | Waypoint replacement plus route-based charging follow-up. |
| `disambiguation_50` | `0/3` | `open_sunroof_safe`, weather guard | Unknown outside temperature should not end before remaining sunroof disambiguation. |
| `disambiguation_53` | `1/3` | `get_weather_guarded`, `set_new_navigation_guarded` | Conditional weather branch must preserve shortest-route preference. |
| `disambiguation_54` | `3/3` | `select_route_by_user_preferences`, `select_poi_at_location_open_at_route_arrival` | Stored no-toll route preference and POI open-at-arrival selection. |
| `disambiguation_55` | `0/3` | Route/charging/fast-food composition, completion guard | Corrected destination, route-based charging search, dinner-time fast-food constraint, and navigation completion claim. |

## High-Priority Helper Regression Targets

| Target | Current score | Tuning to validate |
| --- | --- | --- |
| `base_56` | `0/3` | Suppress route-option follow-up text after successful unqualified waypoint deletion. |
| `base_82`, `disambiguation_46` | `0/3`, `0/3` | Preserve selected-route provenance and avoid fastest narration after user-selected route. |
| `base_84` | `2/3` | Mark complete multi-leg navigation plans as complete and block later accidental final-destination replacement. |
| `base_98` | `0/3` | Keep selected charging stop tied to route setup and provider call. |
| `disambiguation_2`, `disambiguation_32` | `0/3`, `2/3` | Require resolved target percentage before window side effects. |
| `disambiguation_30` | `0/3` | Keep air-circulation preference when turning AC on unless policy requires a different value. |
| `hallucination_72` | `2/3` | Treat unknown `remaining_range` as terminal for range/charging math in the current turn. |
