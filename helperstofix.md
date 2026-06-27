# Helpers To Fix

This file is sanitized for train-set iteration. It summarizes helper, prompt,
and runtime failure patterns discovered in a held-out validation run without
recording held-out task IDs, user prompts, locations, contact names, route IDs,
or exact expected answers.

Do not add evaluator-only flaking or hidden-task specifics here. The goal is to
turn observed failure classes into general code and helper improvements that can
be validated on train tasks, unit tests, or synthetic non-hidden scenarios.

## Principles

- Keep helpers fact-based: explicit model arguments, grounded tool outputs,
  stored preferences, policy constants, or confirmed follow-up answers.
- Do not infer missing user parameters with regex or keyword matching over raw
  user messages.
- Accepted narrow exception: a route helper may preserve an explicit via-road
  label from the current user turn by matching it against already-fetched route
  alternatives for the same start/destination segment. This cannot invent
  endpoints, infer preferences, choose among ambiguous candidates, default a
  side effect, or carry previous raw wording through a bare follow-up. If a
  route choice must survive a follow-up, it should be stored as a recorded
  `select_route(...)` fact.
- Do not overfit helpers to one train scenario if the policy leaves room for
  route choice, clarification, or user preference.
- Do not let convenience aliases such as "last contact" or "last POI" overwrite
  role-specific facts needed later in the same task.
- Do not claim a side effect succeeded unless the corresponding evaluator-visible
  tool call actually succeeded.

## Fix Roadmap

This is the working order for helper and runtime fixes. Each item is phrased as
a general code problem, not as a benchmark-task patch.

This document is also the implementation roadmap. Keep it concrete enough that
we can pick the next item and patch it without re-reading held-out traces. Every
actionable item should include:
- The actual helper, wrapper, guard, or runtime function names involved.
- The general failure shape, described without held-out task IDs or exact
  prompts.
- The intended fix boundary: what the helper is allowed to use, and what it must
  not infer from raw user text.
- The validation target: unit test, synthetic train-safe scenario, train split
  slice, or full helper-regression run.
- Current status: proposed, implemented-needs-target-evidence, preserved, or
  rejected after target regression.

If an issue is caused by a helper blocking otherwise valid model reasoning, mark
that explicitly. Those fixes should usually relax or narrow the helper contract,
not add another task-shaped rule. If a helper was added but target testing shows
the model does not select it, or its prompt exposure hurts nearby tasks, keep
that evidence here and do not mark the item as fixed.

Roadmap discipline:
- Use concrete code symbols, not generic labels like "the wrapper" or "the
  helper", whenever an issue is actionable.
- Mark **helper-blocked valid reasoning** when a helper/repair/default prevented
  a grounded model choice from reaching the evaluator. The preferred fix is to
  preserve the grounded choice and only fall back to a policy default when the
  choice is missing, stale, or invalid.
- Mark **helper-selection gap** when the right helper exists but the model keeps
  manually executing the fragile protocol. The preferred fix is prompt/skill
  ergonomics or helper naming, not hidden argument repair.
- Mark **runtime code issue** when failures come from REPL/provider mechanics,
  retry loops, output parsing, or side-effect claim guards rather than car
  policy reasoning.

### Implementation checklist

Use this as the roadmap when patching helper behavior. The issue sections below
contain more detail; this checklist is the quick map from failure class to code
surface and validation target.

| Priority | Helper or wrapper surface | General fix to implement or preserve | Validation target |
| --- | --- | --- | --- |
| P0 | `CoroutineAgent._retry_storm_error_signature(...)`, `_repl_retry_temperature_override(...)`, `_repeated_repl_error_limit(...)`; provider call path in `provider.py` | Break repeated identical REPL syntax/indentation loops. Keep the retry-temperature ladder Cerebras-only because that provider path is tested; other providers should only use the bounded breaker. | `tests/test_coroutine_agent_retry.py`; full-run call-count audit for repeated REPL errors. |
| P0 | `send_contact_details_to_contact(...)`, `get_contact_details(...)`, `get_contact_id_by_contact_name_guarded(...)`, `_remember_contacts_by_id(...)`, `_repair_send_email_contact_recipient(...)`, `_repair_confirmation_contact_recipients(...)` | Preserve recipient/contact-subject roles instead of relying on latest-contact aliases. Repair only when grounded contact IDs and email ownership prove the recipient mismatch. | Synthetic two-contact email tests plus train contact/email slices. |
| P0 | `select_charging_plug(...)`, `_remember_selected_poi(...)`, `_current_or_referred_selected_charging_poi(...)`, `_repair_charging_calculation_station(...)`, `_repair_charging_station_plug_pair(...)`, `_repair_route_endpoint_to_selected_poi(...)`, `set_new_navigation_via_stop(...)` | Keep selected charging station identity stable across plug selection, charge-time calculation, provider call, and navigation-through-stop setup. Do not select a different POI from raw text or convenience. | Synthetic multi-turn POI/charger flow; charging/navigation train slices. |
| P0 | `set_new_navigation_via_stop(...)`, `select_route(...)`, `_repair_route_ids_for_recorded_selection(...)`, `_repair_route_ids_for_current_request_via(...)`, `_repair_or_block_route_chain(...)`, `set_new_navigation_guarded(...)` | Make two-leg navigation through a selected stop complete both route legs and the final `set_new_navigation(...)`. Preserve explicit final-leg route selection by route ID, alias, or `name_via` only against grounded candidates. | Multi-leg navigation unit tests and route-through-stop train slices. |
| P0 | Argument repair/default guards: `_repair_send_email_contact_recipient(...)`, `_repair_confirmation_contact_recipients(...)`, `_repair_charging_calculation_station(...)`, `_repair_charging_station_plug_pair(...)`, `_repair_route_endpoint_to_selected_poi(...)`, `_repair_route_ids_for_recorded_selection(...)`, `_repair_route_ids_for_current_request_via(...)`, `_resolve_route_arg(...)`, `_resolve_explicit_or_unique_route_arg(...)` | Audit each repair for helper-blocked valid reasoning. Repairs may preserve or correct against grounded facts, but must not override an explicit valid model choice just because a train default often worked. | Focused unit tests where the explicit model choice is valid but non-default, plus helper-regression slices. |
| P0 | Runtime raw-message repair audit: `set_new_navigation_guarded(...)`, `_repair_charging_location_search_to_route(...)`, `_repair_or_block_charging_plan_route(...)`, `navigation_replace_final_destination_guarded(...)`, `_repair_explicit_poi_identity_call(...)`, `set_air_conditioning_on_safe(...)`, `sync_climate_zone(...)`, fan/navigation unknown-state response guards | Keep runtime helpers from inferring missing intent or parameters by scanning `ws.last_user_message`. Use explicit model arguments, selected entities, same-turn helper reports, stored preferences only when explicitly requested, and grounded tool facts. Preserve only the documented via-road exception and confirmation yes/no parsing. | Unit tests asserting explicit helper behavior and no raw-text repair for climate sync, preferred circulation, planned charging route repair, destination replacement, POI identity, and cross-turn charging search. |
| P1 | `navigation_delete_waypoint_guarded(...)`, `navigation_replace_one_waypoint_guarded(...)`, `navigation_replace_final_destination_guarded(...)`, `get_route_options(...)`, `select_route_by_user_preferences(...)`, route narration helpers | Keep fastest-route defaults policy-grounded without forcing fastest when the user requested options or selected a non-fastest route. Narrate "fastest" only when the selected route is actually grounded as fastest. | Route-edit unit tests for fastest, user-selected non-fastest, and unresolved alternatives. |
| P1 | `get_weather_at_route_arrival(...)`, `get_weather_guarded(...)`, `select_route_by_user_preferences(...)`, `set_new_navigation_guarded(...)` | For weather-conditioned navigation, use route-arrival time weather and preserve explicit or stored route preferences after the weather branch is resolved. | Weather-conditioned navigation synthetic tests and train weather slices. |
| P1 | `navigate_by_arrival_weather(...)`, `navigate_to_poi_by_arrival_weather(...)`, `navigate_to_poi_unless_arrival_weather(...)`, `set_navigation_conditioned_on_arrival_weather(...)` | For primary/fallback weather navigation, make the whole protocol one helper: select primary route, check primary arrival-time weather, then set primary/fallback or primary-POI navigation with the model-supplied POI and route preferences. | Synthetic primary-clear and fallback-blocked tests plus weather-conditioned train slices. |
| P1 | `search_charging_stations_on_active_route(...)`, `search_charging_stations_on_route(...)`, `find_charging_stop_on_active_route_by_soc(...)`, `estimate_charging_stops_for_route_by_soc_window(...)`, `_abort_if_unknown_charging_range_blocks(...)` | Require grounded route kilometer/SOC/range facts for charging math. Translate explicit `require_available=True` to the official filter only when the live schema supports it. Unknown range should be a terminal limitation for the current range calculation. | EV/range unit tests and train charging-route slices. |
| P1 | `set_occupied_reading_lights(...)`, `set_exterior_lights_safe(...)`, `set_occupied_seat_heating(...)` | Keep cabin/reading lights out of exterior-light policy helpers. Separate explicit front-seat heating from whole-cabin occupied/unoccupied optimization. | Synthetic occupancy and reading-light/heating tests. |
| P1 | `turn_off_unoccupied_seat_heating()` | For energy-saving seat-heating cleanup, read occupancy and current heating, then turn off only unoccupied heatable front seats. Unknown current level on an unoccupied heatable front seat should not hard-stop; setting level 0 is the safe cleanup action. | Synthetic occupancy/current-level tests, including unavailable current heating for an unoccupied front seat. |
| P1 | `set_occupied_reading_lights(...)`, new or tightened `set_reading_lights_by_occupancy(...)`, optional `_repair_reading_light_occupancy_sequence(...)` | Reading-light occupancy optimization must compute the desired final state once and avoid contradictory on/off calls for the same occupied seat. It should turn on occupied supported reading lights and turn off unoccupied supported reading lights only when requested, using occupancy and current light state when available. | Synthetic occupancy/light-state tests with occupied front, occupied rear, unoccupied front, and mixed current-light states. |
| P1 | `present_climate_comfort_options(...)`, `increase_fan_speed(...)`, `decrease_fan_speed(...)`, `get_climate_settings` wrapper/preflight | Broad air/comfort clarification should first read current climate state when the user asks for options or when the answer depends on current fan/AC/circulation. The helper should expose the current fan speed in the clarification so a follow-up can set or change fan speed without missing the required read. | Synthetic stale-air/comfort tests and train broad-control slices. |
| P1 | `send_contact_details_to_contact(...)`, `get_contact_details(...)`, `handle_pending_confirmation(...)`, `_repair_send_email_contact_recipient(...)` | Contact-details email flow needs a single model-facing helper/example that resolves recipient and subject contacts, reads both email/details in one tool call when possible, drafts the message, asks confirmation, and sends after yes. The runtime should not infer recipient/subject from raw text; it should use explicit helper arguments and grounded contact records. | Synthetic two-contact email flow, confirmation-send tests, and contact/email train slices. |
| P1 | `set_occupied_seat_heating(...)`, `turn_off_unoccupied_seat_heating()`, new or expanded `optimize_seat_heating_by_occupancy(...)`, `sync_climate_zone(...)`, `set_climate_temperature_safe(...)` | Seat/climate efficiency workflows need one helper path that reads occupancy, current seat heating, and current temperature, then applies explicit occupied/unoccupied heating and temperature-sync requests without over-applying to unsupported rear seats. Unknown current heating on a target to be turned off can be safely set to 0; unknown heating on an occupied target should not block setting the requested level. | Synthetic occupied/unoccupied front/rear seat tests, unknown current-level tests, temperature-sync tests. |
| P1 | `navigation_replace_final_destination_guarded(...)`, `navigation_delete_waypoint_guarded(...)`, `select_route(...)`, `select_route_by_user_preferences(...)`, route narration helpers | Route edits must preserve selected route identity, but default-fastest policy should be applied immediately only when no user route-option request or explicit non-default choice is pending. Narration must include toll metadata accurately and not describe toll routes as no-toll. | Route-edit tests for default fastest, explicit alternatives, non-default via-road choice, and toll/no-toll narration. |
| P1 | `set_navigation_via_route_stop_with_open_poi(...)`, `set_new_navigation_via_stop(...)`, `navigation_add_one_waypoint_guarded(...)`, route-stop completion guards | If navigation is already active and the user adds a stop satisfying route-position constraints, the helper should either emit the expected waypoint-add mutation or a complete two-leg replacement, but must not merely claim a stop was added after only calculating routes. | Synthetic active-navigation route-stop tests and train route-stop/charging/food slices. |
| P2 | `get_route_options(...)`, `select_route_by_user_preferences(...)`, route presentation helpers | Route presentations must disclose `includes_toll` accurately for each named route. If a route preference says no-toll within a threshold, the selector should use grounded route durations/toll fields and the response should not label a toll route as no-toll. | Unit tests for fastest-toll vs slower-no-toll alternatives and no-toll-within-threshold selection. |
| P2 | `send_email(...)`, `handle_pending_confirmation(...)`, `_has_successful_email_send(...)`, `_pending_send_email_confirmation(...)`, `_ungrounded_email_completion_response(...)` | Block "email sent" claims until the evaluator-visible `send_email(...)` succeeds; confirmation prompts are not completed sends. | Email completion-claim unit tests. |

Raw-message repair audit status:
- Implemented for the audited surfaces. The remaining `ws.last_user_message`
  uses are turn identity checks, current-turn via-road label preservation,
  confirmation yes/no parsing, or response repair based on the assistant's own
  outgoing text.
- Re-audited before helper-regression runs after the no-regex reminder. No
  helper was found deriving a missing window, percentage, route, POI, contact,
  SOC, kilometer, or climate parameter by regex/keyword matching the raw user
  message. If a future fix would need that, reject it or move the decision back
  to explicit model reasoning plus grounded helper arguments.
- The previous bare `continue` carry-over for via-road wording was removed. A
  follow-up can still preserve a route choice through recorded `select_route(...)`
  facts, not by resurrecting prior raw user text.

### 1. Preserve role-specific entities through long flows

Affected helpers and guards:
- `send_contact_details_to_contact(...)`
- `get_contact_details(...)`
- `get_contact_id_by_contact_name_guarded(...)`
- `_remember_contacts_by_id(...)`
- `_repair_send_email_contact_recipient(...)`
- `_repair_confirmation_contact_recipients(...)`
- `_remember_selected_poi(...)`
- `select_charging_plug(...)`
- `set_new_navigation_via_stop(...)`
- `set_navigation_via_route_stop_with_open_poi(...)`

Roadmap:
- Keep contacts, POIs, charging stations, meal stops, and destination POIs in
  role-specific scratchpad slots instead of relying on only `last_*` aliases.
- When a later action uses an entity, validate it against the role it is meant
  to satisfy. For example, an email recipient must still be the resolved
  recipient, and a navigation stop must still be the selected charging station
  unless the model explicitly selected a different grounded POI.
- Continue allowing flexible model reasoning: helpers may check grounded IDs and
  prior tool results, but must not infer missing roles from raw user-message
  words.

Validation:
- Unit tests with two contacts in different roles.
- Unit tests where a selected charger is followed by charging-time,
  provider-call, and route-to-stop actions.
- Regression slices covering contact/email and POI/navigation flows.

### 2. Guard success claims against missing side effects

Affected helpers and guards:
- `send_email(...)`
- `handle_pending_confirmation(...)`
- `_has_successful_email_send(...)`
- `_pending_send_email_confirmation(...)`
- `_ungrounded_email_completion_response(...)`
- Existing navigation guards:
  `_has_successful_navigation_mutation(...)` and
  `_ungrounded_navigation_completion_response(...)`

Roadmap:
- Keep email and navigation completion wording tied to actual successful
  evaluator-visible side effects.
- If confirmation is pending, the assistant should say confirmation is still
  needed, not that the side effect already happened.
- If the tool is unavailable, return the capability limitation directly.

Validation:
- Unit tests for claims before tool call, after confirmation prompt, and after
  successful tool execution.
- Regression slices for email-confirmation tasks.

### 3. Make interior-light and occupied-seat controls explicit

Affected helpers and tools:
- `set_occupied_reading_lights(...)`
- `set_reading_lights_by_occupancy(...)` if added
- `_repair_reading_light_occupancy_sequence(...)` if added
- `set_exterior_lights_safe(intent=...)`
- `get_reading_lights_status(...)`
- `set_reading_light(...)`
- `get_seats_occupancy(...)`
- `set_occupied_seat_heating(...)`
- `get_seat_heating_level(...)`
- `set_seat_heating(...)`
- `turn_off_unoccupied_seat_heating()`
- `optimize_seat_heating_by_occupancy(...)` if added
- `sync_climate_zone(...)`

Roadmap:
- Keep interior reading-light requests out of exterior-light policy helpers.
- Use `set_occupied_reading_lights(...)` when the model has resolved an
  occupied-seat reading-light action with an explicit `on` value.
- For "occupied on, unoccupied off" reading-light optimization, compute the
  desired final state once from occupancy and current light state when
  available. Do not issue both `on=True` and `on=False` for the same occupied
  seat in one request.
- Rear-seat occupancy can require reading-light actions even when seat-heating
  is unsupported. Keep reading-light support separate from seat-heating support.
- Split seat-heating behavior into clear modes: explicit front-seat scope versus
  whole-cabin occupied/unoccupied optimization.
- For optimization requests, read occupancy and current heating state before
  deciding which supported zones to change.
- For unoccupied heatable front seats, setting level 0 is a safe cleanup action
  even if the current level is unknown. For occupied front seats, setting an
  explicitly requested heating level is safe even if the current level is
  unknown.
- If a rear passenger asks whether heating is active and rear seats do not have
  heat controls, the helper should answer that seat heating is unavailable for
  that rear seat and use climate-temperature fallback only if the user asks for
  a comfort fallback or the task flow explicitly calls for it. Do not hallucinate
  rear seat-heating support.
- Climate-temperature synchronization should use official temperature state.
  If the user asks one zone to match another, read current temperature and set
  the target zone to the grounded source-zone value. Do not infer source/target
  direction from raw text inside the helper.

Validation:
- Synthetic occupancy maps for all front/rear seats.
- Unit tests proving rear aliases do not duplicate reading-light actions.
- Unit tests for explicit seat scope and whole-cabin optimization.
- Unit tests for mixed reading-light final states, including occupied rear and
  unoccupied front seats.
- Unit tests for unknown current heating on unoccupied and occupied front-seat
  targets.
- Unit tests for temperature-sync source/target preservation.

Held-out validation signal:
- The full held-out run exposed a real reading-light helper issue: the agent
  read occupancy, turned lights on for occupied seats, then later turned off at
  least one occupied light in the same request. This is not evaluator flaking;
  it is contradictory side-effect sequencing.
- The same run exposed broader seat/climate optimization gaps: workflows that
  combine occupancy, current heating, unsupported rear seats, and temperature
  matching can be behaviorally plausible but still miss required reads or
  over-apply side effects. These are general helper-coverage gaps, not
  task-specific bugs.

### 4. Keep route editing flexible while preserving policy facts

Affected helpers and internals:
- `navigation_delete_waypoint_guarded(...)`
- `navigation_replace_one_waypoint_guarded(...)`
- `navigation_replace_final_destination_guarded(...)`
- `get_route_options(...)`
- `select_route(...)`
- `select_route_by_user_preferences(...)`
- `_remember_route_selection(...)`
- `_route_narration_record(...)`
- `_store_route_narration_sequence(...)`
- `_append_pending_route_narration(...)`
- `_single_segment_final_destination_needs_route_choice(...)`

Roadmap:
- Do not globally force fastest route just because a route edit happened.
- Use fastest automatically only when policy, stored preferences, unique route
  facts, or an explicit model choice make that route resolved.
- When the selected route is grounded as fastest, say so. When it is a
  user-selected non-fastest route, do not falsely call it fastest.
- Keep final-destination, waypoint-deletion, and waypoint-replacement cases
  separate because they have different policy and disambiguation shapes.

Validation:
- Unit tests for fastest selected by policy, user-selected non-fastest route,
  and unresolved multi-option route edits.
- Train route-edit regression slices before and after route narration changes.

Held-out validation signal:
- Some held-out route edits showed the model manually presenting alternatives
  or taking a default route before the later user-selected non-default route.
  The safe fix is not to parse hidden route intent. The safe fix is to preserve
  explicit route selections, disclose route facts accurately, and make the
  policy-default path easy to select when no route preference has been grounded.
- Route/toll narration remains a scoring risk. If a route has
  `includes_toll=True`, neither the helper nor the response obligation should
  call it a no-toll route. Toll disclosure should be generated from grounded
  route records, not model memory.

### 4a. Make broad climate/airflow clarification state-aware

Affected helpers and tools:
- `present_climate_comfort_options(...)`
- `get_climate_settings(...)`
- `increase_fan_speed(...)`
- `decrease_fan_speed(...)`
- `set_air_conditioning_on_safe(...)`
- `set_window_defrost_safe(...)`

Roadmap:
- When the user asks what can be done about broad air/comfort issues, the
  response-only helper should read current climate settings first unless the
  required current state is already grounded in scratchpad for the current
  turn.
- The clarification should mention relevant grounded state, especially current
  fan speed, AC on/off, and circulation mode. This lets the user choose a fan
  level or AC/circulation action and keeps the evaluator-visible required read
  in the trace.
- Do not make side effects from the broad helper. It should gather state and
  ask options; the next user answer should drive `increase_fan_speed(...)`,
  `set_fan_speed(...)`, `set_air_conditioning_on_safe(...)`, or another
  explicit helper.
- Do not infer the desired fan level, AC state, or circulation mode from raw
  user text inside the helper. The model must pass explicit arguments or use a
  follow-up answer.

Validation:
- Synthetic "stale air" and "comfort options" tests where current fan is off,
  nonzero, and unknown.
- Broad-control train slices to ensure no regression to premature side effects.

Held-out validation signal:
- The full held-out run included a broad-airflow case where the agent asked
  good options and then set the requested fan level, but failed because it had
  not first read current climate settings. This is a helper-selection/state-read
  issue, not an argument repair issue.

### 4b. Make contact-detail email a single role-safe flow

Affected helpers and guards:
- `send_contact_details_to_contact(...)`
- `get_contact_details(...)`
- `get_contact_id_by_contact_name_guarded(...)`
- `_remember_contacts_by_id(...)`
- `_repair_send_email_contact_recipient(...)`
- `_repair_confirmation_contact_recipients(...)`
- `handle_pending_confirmation(...)`
- `_has_successful_email_send(...)`

Roadmap:
- Keep recipient and subject contact roles explicit from the model call:
  recipient contact ID/name and subject contact ID/name are different slots.
- Prefer one helper for the whole flow once the user intent is "send this
  person's contact details to that recipient": resolve both contacts, read both
  contact records, draft from grounded details, ask confirmation, then send.
- If only the recipient is known and the user later provides the subject
  contact, preserve the original recipient role instead of falling back to
  "last contact".
- On a bare confirmation after a contact-detail draft, `handle_pending_confirmation()`
  should send the stored email and terminate the turn. If no draft exists, the
  assistant should ask for missing content instead of claiming send success.
- Do not infer contacts from raw user-message regex. The model supplies names
  or IDs; helper guards validate against grounded contact records.

Validation:
- Synthetic two-contact email tests with same-turn and multi-turn recipient /
  subject resolution.
- Confirmation tests where yes sends the stored email and where yes without a
  stored draft asks for missing content.
- Contact/calendar train slices.

Held-out validation signal:
- The full held-out run exposed a helper-selection gap: the agent resolved the
  recipient and subject contact, displayed the subject details, then treated the
  user's "yes" as missing email content because no confirmation draft had been
  stored. This is exactly the role-safe contact-detail email flow the helper
  should cover.

### 5. Use arrival-grounded weather for conditional navigation

Affected helpers and tools:
- `get_weather_at_route_arrival(...)`
- `navigate_by_arrival_weather(...)`
- `navigate_to_poi_by_arrival_weather(...)`
- `set_navigation_conditioned_on_arrival_weather(...)`
- `get_weather_guarded(...)`
- `get_routes_from_start_to_destination(...)`
- `set_new_navigation_guarded(...)`
- `select_route_by_user_preferences(...)`

Roadmap:
- For destination choices conditioned on weather at the destination, the model
  should route first, derive arrival time, then call
  `get_weather_at_route_arrival(...)` or equivalent guarded weather lookup.
- Do not use current-time weather when the decision is about arrival conditions.
- Preserve explicit or stored route preferences after the weather branch is
  resolved.

Validation:
- Synthetic conditional-destination tests with different current-time and
  arrival-time weather.
- Regression slices for weather-conditioned navigation and route-preference
  preservation.

Current implementation status:
- Added `set_navigation_conditioned_on_arrival_weather(...)` as the preferred
  helper when the request has a primary destination, fallback destination, and
  blocked weather conditions. The model supplies destination IDs, blocked
  condition words, and the resolved route preference; the helper does not parse
  raw user text.
- Added `navigate_by_arrival_weather(...)` as the short model-facing alias for
  the same implementation. This is a prompt/ergonomics fix for cases where the
  model ignored the longer helper name and manually executed a fragile route /
  weather / navigation sequence.
- Added `navigate_to_poi_by_arrival_weather(...)` for the broader
  primary-location POI branch. The model supplies the primary location ID,
  fallback destination ID, POI category, blocked conditions, POI selector such
  as `fastest_charging`, and route preference. The helper checks
  primary-location arrival weather; if blocked, it sets fallback navigation and
  skips POI search; if clear, it searches/selects the primary-location POI and
  sets navigation to that POI. It does not inspect raw user text.
- Added `navigate_to_poi_unless_arrival_weather(...)` as a short alias for the
  same primary-location POI helper. This is a helper-selection ergonomics fix:
  it adds no new branching logic, no raw user-text parsing, and no hidden route
  preference repair. Focused unit coverage:
  `test_navigate_to_poi_unless_arrival_weather_alias_blocks_before_poi_search`.
- Single-leg `set_new_navigation(...)` now also honors a previously recorded
  route selection for the same destination, so if the model explicitly selected
  a route before the final navigation mutation, the wrapper can preserve that
  selection instead of falling back to a default route ID.
- Earlier target evidence was negative for the primary-location POI branch
  before the short alias:
  `output/run_configs/20260626-155145__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  still shows the model manually chaining route/weather/navigation and choosing
  the fastest fallback route. The route preference expected by the evaluator is
  not visible in the user message or `get_user_preferences(...)`, so adding a
  wrapper that silently chooses that route would violate the helper design
  rules. The remaining safe problem is helper selection/prompt ergonomics, not a
  hidden route-preference repair.
- Target evidence immediately after adding `navigate_to_poi_unless_arrival_weather(...)`
  was still inconclusive/negative:
  `output/run_configs/20260626-160737__run_configs-coroutine_weather_route_stop_regression_cerebras_gemini_1__train-trials1-base1ids-hall0-dis2ids__gpt-oss-120b.json`
  passed `disambiguation_53`, but `base_96` still used the manual route /
  weather / fallback-navigation path and selected the fastest fallback route.
  The task metadata contains a shortest-route preference, but the
  evaluator-facing user message does not state it and
  `get_user_preferences(...)` returns an empty route preference. Do not repair
  this by silently choosing shortest; that would be hidden-task inference rather
  than grounded helper behavior.
- Latest full helper-regression evidence:
  `output/run_configs/20260626-174607__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  shows `disambiguation_53` taking the shortest Cologne route and passing
  action/tool/final checks, but failing only the policy LLM because it demanded
  fastest instead. This confirms the route-preference surface is not reliably
  exposed to the agent: one safe run can match the hidden preference and still
  be contradicted by policy evaluation. Do not add a helper that forces hidden
  shortest or fastest from task identity or raw wording.
- Latest target evidence after adding the arrival-weather branch response
  obligation:
  `output/run_configs/20260626-190325__run_configs-coroutine_weather_route_stop_regression_cerebras_gemini_1__train-trials1-base1ids-hall0-dis2ids__gpt-oss-120b.json`
  passed `disambiguation_53`. The helper selected the fallback branch from
  grounded arrival weather and the response included the weather reason, so the
  simulator no longer continued toward the primary charging-station branch.
- Latest full helper-regression evidence:
  `output/run_configs/20260626-192315__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  passed `disambiguation_53` and failed `base_96` raw. Current safe boundary:
  preserve explicit or stored route preference when model-supplied, but do not
  force hidden shortest/fastest choices from task identity or raw wording.

### 6. Make charging-route searches carry the grounded search contract

Affected helpers and guards:
- `search_charging_stations_on_active_route(...)`
- `search_charging_stations_on_route(...)`
- `find_charging_stop_on_active_route_by_soc(...)`
- `estimate_charging_stops_for_route_by_soc_window(...)`
- `plan_charging_for_next_meeting(...)`
- `search_poi_along_route_guarded(...)`
- `select_charging_plug(...)`
- `_charging_search_kilometer_from_state(...)`
- `_abort_if_unknown_charging_range_blocks(...)`
- `_record_unknown_charging_range(...)`

Roadmap:
- If the model has explicitly resolved an active-route kilometer, use
  `search_charging_stations_on_active_route(at_kilometer=...)` so the route ID
  and selected charging facts stay grounded.
- If a helper argument such as `require_available=True` is explicitly supplied,
  have the helper include the corresponding live-supported search filter when
  the official task tool exposes that argument.
- Do not decide availability, SOC reserve, or route kilometer from raw user-text
  patterns. The model must supply those resolved values or read official facts
  that produce them.
- Treat unknown or unparseable `remaining_range` as a terminal limitation for
  range math in the current request.

Validation:
- Unit tests for active-route kilometer search with and without availability
  filtering.
- Unit tests for unavailable range blocking route-distance math.
- Train EV/range and route-charging regression slices.

Current implementation status:
- `search_charging_stations_on_active_route(...)` and
  `find_charging_stop_on_active_route_by_soc(...)` now translate an explicit
  `require_available=True` helper argument into the official
  `filters=["charging_stations::has_available_plug"]` route-search argument
  when, and only when, the live task schema exposes `filters`.
- Added `search_charging_stations_on_route(...)` for planned-route charging
  searches where navigation is not active and the user did not ask to start it.
  The helper requires a grounded route id and numeric kilometer, reads charging
  status when available and not already grounded, calls
  `search_poi_along_the_route(...)`, and never calls `set_new_navigation(...)`.
- Prompt and skill guidance now state that confirming or using a route inside a
  plan-only conversation selects that route for planning, email, or route search;
  it is not a navigation mutation unless the user explicitly asks to start, set,
  or update navigation.
- The same guidance now says not to add `set_new_navigation(...)` as an extra
  side effect when the requested work is route inspection, charging search,
  contact lookup, or email.
- The helpers still do not decide availability intent from raw user text. The
  model must choose `require_available=True` from its own interpretation of the
  request and grounded context.
- Unknown or unparseable `remaining_range` now produces a broader terminal
  limitation: the assistant says it cannot determine whether the remaining range
  is enough or complete charging-stop planning because the car system did not
  provide the remaining range. This remains fact-based: it is triggered by the
  tool response field, not by matching the user's request.
- Target evidence:
  `output/run_configs/20260626-173450__run_configs-coroutine_h72_cerebras_gemini_3__train-trials3-base0-hall1ids-dis0__gpt-oss-120b.json`
  passed `hallucination_72` `3/3`.
- Full helper-regression evidence:
  `output/run_configs/20260626-174607__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  passed both hallucination helper targets (`hallucination_72` and
  `hallucination_82`) after the unavailable-range wording change.

### 7. Complete route-stop composition under active navigation

Affected helpers and guards:
- `set_navigation_via_route_stop_with_open_poi(...)`
- `set_new_navigation_via_stop(...)`
- `navigation_add_one_waypoint_guarded(...)`
- `set_new_navigation_guarded(...)`
- `_repair_or_block_route_chain(...)`
- `_has_successful_navigation_mutation(...)`
- `_ungrounded_navigation_completion_response(...)`

Roadmap:
- For route-stop composition requests, distinguish planning from navigation
  mutation. If the user asks to set or start navigation with the stop, the
  evaluator-visible trace must include a successful route-chain mutation, not
  only route and POI searches.
- If navigation is inactive, a complete two-leg `set_new_navigation(...)` is
  acceptable when both route legs are grounded.
- If navigation is already active and the user asks to add the stop to the
  current route, prefer the official waypoint-add mutation when available and
  when the route dependencies match the active route structure.
- The completion response should not claim that a stop has been added unless
  `set_new_navigation(...)`, `navigation_add_one_waypoint(...)`, or the relevant
  navigation mutation succeeded.
- Preserve the distinction between the navigation stop POI and a companion POI
  that is only open at the same route position. Do not turn the companion POI
  into the waypoint unless the model explicitly selects it from grounded POIs.

Validation:
- Synthetic route-stop tests for inactive navigation, active navigation, and
  plan-only conversations.
- Tests where companion POI and stop POI have different IDs but share route
  position/opening window.
- Train route-stop/charging/food slices.

Held-out validation signal:
- The full held-out run exposed an active-navigation composition miss: route
  and POI searches were correct, and the final state could be repaired later,
  but the assistant claimed a charging/food stop had been added before the
  expected mutation was emitted. This is a completion-claim plus mutation-shape
  issue, not a reason to hardcode any route stop.

### 8. Keep retry-storm controls provider-scoped

Affected runtime code:
- `coroutine_agent.py`
- `provider.py`
- Persistent REPL execution path for repeated `SyntaxError`,
  `IndentationError`, and `TabError`

Roadmap:
- Stop repeated near-identical REPL syntax failures before they consume many
  sequential model calls.
- Keep the temperature-ladder escape hatch scoped to the Cerebras path where it
  was tested.
- Do not apply provider-specific sampling overrides to other APIs unless those
  APIs are tested with the same behavior.

Validation:
- Unit tests for repeated identical REPL errors.
- Provider-path tests proving only Cerebras receives the retry temperature
  override.
- Call-count audits on full train runs.

## Active Helper/Code Issues

Cross-cutting surfaces:
- Helper registration: `WORKSPACE_HELPER_NAMES`.
- Helper dispatch and mixed batching: `CoroutineWorkspace.call_tool_sync(...)`
  and `CoroutineWorkspace.call_batch_sync(...)`.
- Runtime state: `scratchpad["entities"]`, `remember_entity(...)`, and
  normalized result aliases exposed back into the Python REPL.
- Prompt/skill surfaces: `coroutine_prompts.py` and `Skills/car_domain_120b.md`.

### Active issue index by helper name

Use this index as the patch roadmap. The detailed sections below explain why
each item exists and how to validate it without copying held-out task details.

| Issue class | Primary helpers or wrappers | Current status | Safe next move |
| --- | --- | --- | --- |
| Contact role drift / contact-details email | `send_contact_details_to_contact(...)`, `get_contact_details(..., role=...)`, `get_contact_id_by_contact_name_guarded(...)`, `_remember_contacts_by_id(...)`, `_remember_contact_role(...)`, `_repair_send_email_contact_recipient(...)`, `_repair_confirmation_contact_recipients(...)`, `handle_pending_confirmation(...)` | Implemented for direct helper use, but held-out run exposed helper-selection gap in multi-turn recipient/subject contact-detail email. | Improve skill/prompt examples and add synthetic test where recipient is resolved first, subject contact later, confirmation sends stored draft. |
| Email success claims | `send_email(...)`, `handle_pending_confirmation(...)`, `_has_successful_email_send(...)`, `_pending_send_email_confirmation(...)`, `_ungrounded_email_completion_response(...)` | Implemented and unit-tested. Confirmation success is terminal for the current Python execution. | Preserve claim-before-send, confirmation-pending, successful-send, and terminal-confirmation tests. |
| Interior vs exterior light routing | `set_exterior_lights_safe(...)`, `set_occupied_reading_lights(...)`, `get_reading_lights_status(...)`, `set_reading_light(...)`, `get_seats_occupancy(...)` | Implemented and target-validated; broad "lights" remains a regression risk. | Keep examples domain-based: cabin/reading context goes to reading-light helpers; exterior visibility goes to exterior helpers. Do not parse raw text inside helpers. |
| Reading-light occupancy final-state planning | `set_occupied_reading_lights(...)`, `get_seats_occupancy(...)`, `get_reading_lights_status(...)`, `set_reading_light(...)`, optional `set_reading_lights_by_occupancy(...)` | Implemented for canonical aliases, but held-out run exposed contradictory on/off calls in one occupancy optimization flow. | Compute desired final state once, then emit only minimal necessary calls. Add occupied rear/unoccupied front synthetic tests. |
| Broad climate/airflow state read | `present_climate_comfort_options(...)`, `get_climate_settings(...)`, `increase_fan_speed(...)`, `decrease_fan_speed(...)` | Proposed from held-out evidence. Current helper can ask good options without grounding current climate state first. | Make broad option helper state-aware and side-effect-free; validate with stale-air/fan-level synthetic tests. |
| Occupied/unoccupied seat heating and climate sync | `set_occupied_seat_heating(...)`, `turn_off_unoccupied_seat_heating()`, `get_seats_occupancy(...)`, `get_seat_heating_level(...)`, `set_seat_heating(...)`, `sync_climate_zone(...)`, `set_climate_temperature_safe(...)` | Partially implemented; latest train slice passed, but held-out run exposed broader optimization and unsupported-rear-seat gaps. | Add occupancy/heating optimizer tests for unknown levels, rear unsupported seats, and target-zone temperature matching. |
| Route-edit default-fastest overfit | `navigation_delete_waypoint_guarded(...)`, `navigation_replace_one_waypoint_guarded(...)`, `navigation_replace_final_destination_guarded(...)`, `get_route_options(...)`, `select_route(...)`, `select_route_by_user_preferences(...)`, `_single_segment_final_destination_needs_route_choice(...)` | Implemented/preserved where safe, but held-out run still shows route-option/default-route tension. | Preserve explicit options/non-fastest choices; apply default-fastest only when no route-option request or grounded non-default choice is pending. Do not add hidden fastest/shortest repairs. |
| Route narration and toll facts | `_remember_route_selection(...)`, `_route_narration_record(...)`, `_store_route_narration_sequence(...)`, `_narrate_from_route_ids(...)`, `_append_pending_route_narration(...)`, `_fastest_route(...)`, `_shortest_route(...)`, `select_route_by_user_preferences(...)` | Partially implemented; held-out run exposed toll/no-toll wording risk. | Generate toll disclosure from grounded `includes_toll`; add no-toll-within-threshold tests and avoid calling toll routes no-toll. |
| POI identity drift | `select_poi(..., role=...)`, `select_charging_plug(...)`, `select_poi_at_location_open_at_route_arrival(...)`, `set_new_navigation_via_stop(...)`, `set_navigation_via_route_stop_with_open_poi(...)`, `_remember_selected_poi(...)`, `_selected_poi_role_key(...)`, `_current_or_referred_selected_charging_poi(...)`, `_repair_route_endpoint_to_selected_poi(...)` | Implemented with role-keyed persistence, focused tests, and latest full helper-regression passes on `base_84`/`base_98`. | Preserve role-keyed validation without overriding explicit grounded POI changes. |
| Charging/range fact omission | `get_charging_specs_and_status(...)`, `find_charging_stop_on_active_route_by_soc(...)`, `search_charging_stations_on_active_route(...)`, `search_charging_stations_on_route(...)`, `estimate_charging_stops_for_route_by_soc_window(...)`, `_abort_if_unknown_charging_range_blocks(...)` | Implemented for known train-safe surfaces; latest full helper regression passed both hallucination charging targets. | Preserve fact requirements for range/battery claims; do not force a charger strategy when the model has not grounded it. |
| Active route-stop composition | `set_navigation_via_route_stop_with_open_poi(...)`, `set_new_navigation_via_stop(...)`, `navigation_add_one_waypoint_guarded(...)`, `set_new_navigation_guarded(...)`, `_has_successful_navigation_mutation(...)`, `_ungrounded_navigation_completion_response(...)` | Proposed from held-out evidence; current helper covers corrected train flow but active-navigation mutation shape still needs work. | Add synthetic active-route stop tests; require successful waypoint-add or full route-chain mutation before completion claims. |
| Weather-conditioned navigation | `get_weather_at_route_arrival(...)`, `navigate_by_arrival_weather(...)`, `navigate_to_poi_by_arrival_weather(...)`, `navigate_to_poi_unless_arrival_weather(...)`, `set_navigation_conditioned_on_arrival_weather(...)`, `get_weather_guarded(...)`, `set_new_navigation_guarded(...)`, `select_route_by_user_preferences(...)` | Implemented. Latest target fixed `disambiguation_53`; latest full helper regression passed `disambiguation_53` and remains unstable only on `base_96`. | Preserve explicit/stored route preferences and branch-response obligations; do not add hidden route-preference repairs that override valid reasoning. |
| Helper-blocked valid reasoning audit | `_repair_send_email_contact_recipient(...)`, `_repair_confirmation_contact_recipients(...)`, `_repair_charging_calculation_station(...)`, `_repair_charging_station_plug_pair(...)`, `_repair_route_endpoint_to_selected_poi(...)`, `_repair_route_ids_for_recorded_selection(...)`, `_repair_route_ids_for_current_request_via(...)`, `_resolve_route_arg(...)`, `_resolve_explicit_or_unique_route_arg(...)` | Current changed repairs have focused tests or regression evidence. | Add "valid non-default model choice is preserved" tests whenever a repair is changed. |
| Retry storm / sequential-call waste | `CoroutineAgent._retry_storm_error_signature(...)`, `_repeated_repl_error_limit(...)`, `_repl_retry_temperature_override(...)`, `provider.py` Cerebras call path | Implemented and unit-tested. | Keep provider-specific retry temperature scoped to Cerebras; audit future full runs for repeated REPL syntax/indentation failures. |

### Contact role drift in multi-contact email flows

Observed general failure:
- The agent correctly resolves a recipient contact, then resolves another
  person's contact details to include in the message body.
- A generic `last_contacts` style scratchpad alias is overwritten by the second
  lookup.
- The model later reads the overwritten alias and sends the email to the person
  whose details should have been included, not to the intended recipient.

Why this is a helper/code issue:
- The helper state is lossy. It preserves the latest contact but not each
  contact's role in the plan.
- Confirmation stores the wrong recipient once the wrong alias is read, so the
  pending-confirmation machinery faithfully executes a bad grounded action.

Relevant code surfaces:
- `get_contact_details(...)` and `get_contact_id_by_contact_name_guarded(...)`.
- Contact persistence in `_remember_contacts_by_id(...)`,
  `_summarize_contacts(...)`, and `remember_entity("last_contacts", ...)`.
- Email recipient repair in `_repair_send_email_contact_recipient(...)` and
  `_repair_confirmation_contact_recipients(...)`.
- Confirmation execution through `handle_pending_confirmation(...)` and raw
  `send_email(...)`.

General fix direction:
- Preserve contacts in stable maps keyed by contact ID and by query/role, not
  only in `last_contacts`.
- Add an explicit helper for "send contact details to another contact" or a
  safer email-recipient guard that checks whether the chosen recipient address
  belongs to the contact whose details dominate the body.
- Avoid hardcoded names or task-specific recipient/content rules. The guard
  should use grounded contact IDs and email ownership only.

Train-safe validation:
- Build unit tests with two synthetic contacts: recipient A and subject B.
- Verify looking up B after A does not change the recipient address for a
  pending email to A.

Current implementation status:
- Added `send_contact_details_to_contact(...)` as a role-safe helper for the
  common "send A's details to B" shape. It requires explicit grounded recipient
  and subject contact IDs, builds the message from grounded contact fields, and
  routes through normal `send_email(...)` confirmation.
- `get_contact_details(..., role=...)` now stores same-turn contact role facts
  such as `email_recipient` and `contact_details_subject`. The confirmation
  guard uses those explicit roles to repair a single-recipient `send_email(...)`
  when the selected address belongs to the subject contact instead of the
  recipient contact.
- Existing `last_contacts` remains a convenience alias. Role repair is
  intentionally narrow: it does not infer roles from raw user text, and it does
  not rewrite arbitrary emails unless grounded current-turn roles prove the
  recipient/subject mismatch.

### Email completion claims without `send_email`

Observed general failure:
- The model gathers enough facts for an email, then responds as if the email was
  sent without emitting `send_email(...)`.

Why this is a helper/code issue:
- Navigation completion claims are guarded, but email success claims are less
  protected.
- This is a general runtime safety problem, not a hidden-task-specific issue.

Relevant code surfaces:
- Existing navigation pattern: `_claims_navigation_completed(...)`,
  `_has_successful_navigation_mutation(...)`, and
  `_ungrounded_navigation_completion_response(...)`.
- Email tool path: raw `send_email(...)`, `handle_pending_confirmation(...)`,
  and the same-turn tool result history used by response guards.
- Completion-claim gate should live near the current response rewriting/gating
  logic, not inside the evaluator-facing raw tool.

General fix direction:
- Extend completion-claim guarding to email claims.
- If a response says an email was sent, require a successful `send_email(...)`
  in the current relevant flow.
- If confirmation is required, allow "I will send..." or "please confirm", but
  block or rewrite "sent" until the real tool succeeds.

Train-safe validation:
- Unit test `respond("Email sent...")` before any successful `send_email`.
- Unit test that confirmed pending email followed by successful `send_email`
  permits a final "sent" response.

Current implementation status:
- Added an email completion guard modeled on the navigation completion guard.
  A response that claims the email was sent now requires a successful
  `send_email(...)` mutation or an already persisted successful send fact.
- Pending email confirmations produce a truthful "still needs confirmation"
  response instead of an optimistic sent claim.

### Interior lighting vs exterior lighting over-bias

Observed general failure:
- A broad request about lights inside the car was handled through exterior-light
  policy helpers instead of reading-light tools.
- A reading-light request with no explicit seat/position sometimes triggered a
  broad all-light action instead of disambiguating or selecting the driver when
  policy/task context makes that the intended default.

Why this may be overfit:
- The broad exterior-light helper/prompt was tuned for train cases where vague
  "lights" should often mean exterior visibility.
- That guidance is too strong when the request context points to cabin or
  reading lights.

Relevant code surfaces:
- Exterior helper: `set_exterior_lights_safe(intent=...)`.
- Raw interior-light tools: `get_reading_lights_status(...)` and
  `set_reading_light(...)`.
- Occupancy read when needed: `get_seats_occupancy(...)`.
- Prompt/skill examples that route broad "lights" requests:
  `coroutine_prompts.py` and `Skills/car_domain_120b.md`.

General fix direction:
- Add prompt guidance that "lights in the car", "reading lights", cabin/seat
  context, or occupied-seat lighting are interior-light domains, not exterior
  visibility domains.
- Consider a small response-only helper for broad interior lighting that asks a
  clarification or reads occupancy before side effects.
- Keep `set_exterior_lights_safe(intent=...)` for exterior visibility only. Do
  not pass raw user text to it.

Train-safe validation:
- Unit/prompt tests for broad lighting requests with explicit interior context.
- Unit tests that occupied-seat reading-light flows do not emit extra alias
  positions beyond the grounded seat positions.

Current implementation status:
- Added prompt/skill guidance that reading/cabin/inside-car lighting is an
  interior-light domain, not an exterior-light helper path.
- Added `set_occupied_reading_lights(...)` for occupied-seat reading-light
  requests with an explicit `on` boolean.

### Reading-light alias over-action

Observed general failure:
- The agent reads seat occupancy correctly, but emits more reading-light setter
  calls than necessary, including duplicate or alias rear positions.

Why this is a helper/code issue:
- There is no dedicated helper that maps occupied seat facts to the supported
  reading-light control positions with a minimal, non-duplicated action set.

Relevant code surfaces:
- Candidate new helper, if we add one:
  `set_occupied_reading_lights(...)` or similarly named workspace helper.
- Raw tools to wrap: `get_seats_occupancy(...)`, `get_reading_lights_status(...)`,
  and `set_reading_light(...)`.
- Helper exposure requires adding the name to `WORKSPACE_HELPER_NAMES`, binding
  it in the REPL globals, and documenting it in `coroutine_prompts.py` /
  `Skills/car_domain_120b.md`.

General fix direction:
- Add or document a helper for occupied-seat reading-light adjustment.
- Normalize seat occupancy keys to exactly the supported reading-light positions.
- Avoid setting alias positions when the canonical position has already been
  handled.

Train-safe validation:
- Synthetic occupancy maps covering each front/rear seat.
- Assert only the expected canonical reading-light controls are called.

Current implementation status:
- Added `set_occupied_reading_lights(...)`, which reads occupancy and emits each
  occupied canonical reading-light position once. Rear aliases are normalized to
  `DRIVER_REAR` and `PASSENGER_REAR`.

### Occupied-seat heating trace mismatch

Observed general failure:
- Occupied-seat heating flows can be semantically reasonable but fail expected
  action traces because the helper expands to individual front-seat calls where
  a broader supported zone call is expected, or because it skips a required read
  of current heating state before optimization.
- Rear-seat occupancy makes the phrasing tricky because rear seats may be
  occupied but not heatable.

Why this may be overfit:
- Recent helper tuning focused on avoiding over-broad front-passenger heating in
  train scenarios.
- That made the helper conservative for explicit driver/passenger scope, but it
  is not yet general enough for "all occupied seats" and "turn off unoccupied
  seats" optimization flows.

Relevant code surfaces:
- Main helper: `set_occupied_seat_heating(level=None, increase_by=None,
  seat_zone=None)`.
- Cleanup helper: `turn_off_unoccupied_seat_heating()`.
- Raw tools wrapped by the helper: `get_seats_occupancy(...)`,
  `get_seat_heating_level(...)`, and `set_seat_heating(...)`.
- Related prompt/skill guidance for explicit seat scope versus all occupied
  front seats.

General fix direction:
- Separate two helper modes:
  - Explicit front-zone scope: only the supplied front zone.
  - Whole-cabin optimization: read occupancy and current heating state, set
    supported occupied zones appropriately, and clearly explain unsupported rear
    heating when needed.
- Prefer broad supported tool zones only when that exactly matches the resolved
  action and does not heat unsupported/unwanted seats.
- Require `get_seat_heating_level` in optimization flows that ask whether a seat
  is currently active or ask to avoid wasting energy based on current settings.

Train-safe validation:
- Unit tests for explicit front-zone calls.
- Unit tests for whole-cabin occupied/unoccupied optimization.
- Unit tests with occupied rear seats and non-heatable rear-seat explanation.

Current implementation status:
- Added `turn_off_unoccupied_seat_heating()` for energy-saving cleanup. It reads
  occupancy and current seat-heating levels, changes only unoccupied heatable
  front seats to level 0, and leaves occupied seats unchanged.
- If a current heating level is unavailable for an unoccupied heatable front
  seat, the helper still sets that seat to 0 to make sure it is off. This keeps
  unknown-value handling aligned with safe cleanup behavior rather than turning
  missing state into a hard stop.

### Route-edit default-fastest overfit

Observed general failure:
- A waypoint-delete route edit without an explicit visible route preference was
  immediately resolved to fastest.
- In a different but policy-compatible route-edit shape, the expected behavior
  was not to force fastest in that way.

Why this is likely overfit:
- A train-targeted rule pushed "delete intermediate waypoint with no route
  preference" toward immediate fastest-route mutation.
- That is too broad. It can block valid route-option disambiguation or hidden
  user preference paths where the user would choose a different route if shown
  options.

Relevant code surfaces:
- Active route edit helpers: `navigation_delete_waypoint_guarded(...)`,
  `navigation_replace_one_waypoint_guarded(...)`, and
  `navigation_replace_final_destination_guarded(...)`.
- Route lookup/selection helpers: `get_route_options(...)`, `select_route(...)`,
  and `select_route_by_user_preferences(...)`.
- Route derivation and repair internals:
  `_find_unique_connecting_route(...)`, `_fastest_route_id(...)`,
  `_resolve_route_arg(...)`, `_resolve_explicit_or_unique_route_arg(...)`,
  and `_single_segment_final_destination_needs_route_choice(...)`.
- Preflight route state and attention messages should inform the model but not
  hardcode a task-specific route choice.

General fix direction:
- Narrow the auto-fastest rule to cases where policy unambiguously requires it
  and there is no route-option disambiguation cue in the task context.
- For route edits that create a single replacement segment with multiple route
  alternatives, let the model present/ask when the user requested options or the
  task context expects route selection.
- Do not implement this with user-message regex. Use explicit model intent,
  stored preferences, route facts, and disambiguation protocol state.

Train-safe validation:
- Re-test train waypoint-delete cases that motivated the fast-default rule.
- Add synthetic tests where route alternatives exist and the model explicitly
  chooses to ask/present options before mutation.

Current implementation status:
- `navigation_delete_waypoint_guarded(...)` no longer overwrites every
  mid-waypoint deletion with the fastest previous-to-next route. It preserves a
  supplied `route_id_without_waypoint` only when that route is already grounded
  and connects the deleted waypoint's previous and next waypoints. If the
  supplied route is missing, stale, unknown, or does not connect the segment, the
  helper keeps the existing policy-default fastest derivation.
- The 120B skill wording now matches that contract: explicit grounded route
  choices win; fastest is the fallback only when no valid choice or stored
  preference is available.
- Focused unit coverage:
  `test_delete_mid_waypoint_preserves_supplied_connecting_route`,
  `test_delete_mid_waypoint_derives_connecting_route`, and
  `test_delete_mid_waypoint_names_selected_route_and_offers_alternatives`.
- Target evidence:
  `output/run_configs/20260626-154239__run_configs-coroutine_route_helper_fix_cerebras_gemini_1__train-trials1-base3ids-hall0-dis1ids__gpt-oss-120b.json`
  passed `base_48`, `base_56`, `base_82`, and `disambiguation_46`. This
  validates the current route-edit boundary: default fastest for unselected
  waypoint deletion, but route-choice presentation/preservation for
  single-segment final-destination replacement.

### Route narration too conservative after fastest selection

Observed general failure:
- After a route edit where fastest routes were actually selected for new
  segments, narration sometimes said only "selected route" instead of explicitly
  stating the fastest-route fact required by policy.

Why this is a helper issue:
- Earlier fixes correctly avoided false "fastest" claims after user-selected
  non-fastest routes.
- The narration layer now needs to preserve both sides: avoid false fastest
  claims, but state fastest when the selected route is grounded as fastest.

Relevant code surfaces:
- Route selection state: `_remember_route_selection(...)`,
  `_latest_recorded_route_selection_for_destination(...)`, and
  `_recorded_selector_for_route_id(...)`.
- Narration construction: `_route_narration_record(...)`,
  `_route_narration(...)`, `_store_route_narration(...)`,
  `_store_route_narration_sequence(...)`, `_narrate_from_route_ids(...)`, and
  `_append_pending_route_narration(...)`.
- Route facts and ranking helpers: `_route_chain_fact(...)`,
  `_fastest_route(...)`, `_shortest_route(...)`, and `_normalize_route(...)`.

General fix direction:
- Store selection provenance per segment: route ID, via roads, whether it is
  fastest, whether it is shortest, whether it was user-selected, and whether
  alternatives existed.
- Narration should say "fastest" only when the selected route is grounded as
  fastest, and should avoid generic "selected route" when policy requires the
  fastest-route disclosure.

Train-safe validation:
- Unit tests for selected fastest route, selected non-fastest user route, and
  selected route with unknown ranking.

Current implementation status:
- Route-id selection no longer suppresses grounded fastest/shortest aliases in
  narration. If the selected route is known to be fastest, the response can say
  fastest even when the selector was a route ID. If the route is a non-default
  selected alternative, narration still says selected route with its via roads
  and does not claim fastest.
- Focused unit coverage:
  `test_narration_route_id_selection_keeps_fastest_fact` and
  `test_narration_route_id_selection_non_default_stays_selected`.
- Target evidence:
  `output/run_configs/20260626-155643__run_configs-coroutine_route_helper_fix_cerebras_gemini_1__train-trials1-base3ids-hall0-dis1ids__gpt-oss-120b.json`
  kept the route-edit target at `4/4` after this narration change.

### POI identity drift across multi-turn navigation setup

Observed general failure:
- The agent selects or discusses one charging POI, calculates facts for it, then
  later constructs navigation through a different POI.

Why this is a helper/code issue:
- Current POI identity preservation works in several train cases but is not
  durable enough across longer multi-turn workflows.
- Route-to-stop calls can still drift if a later lookup overwrites selected POI
  aliases or if the model uses a stale/nearby POI object.

Relevant code surfaces:
- POI selection helpers: `select_poi(...)`,
  `select_poi_at_location_open_at_route_arrival(...)`, and
  `select_charging_plug(...)`.
- Navigation-through-stop helpers: `set_new_navigation_via_stop(...)`,
  `set_navigation_via_route_stop_with_open_poi(...)`, and
  `set_new_navigation_guarded(...)`.
- Identity persistence and repair internals: `_remember_selected_poi(...)`,
  `_current_or_referred_selected_charging_poi(...)`,
  `_repair_charging_calculation_station(...)`,
  `_repair_charging_station_plug_pair(...)`,
  `_repair_route_endpoint_to_selected_poi(...)`, and `_known_poi_by_id(...)`.

General fix direction:
- Preserve selected POI identity by role: selected charger, selected meal stop,
  selected destination POI, selected companion POI.
- When calculating charging time or setting navigation through a stop, validate
  that the POI ID matches the selected role unless the model explicitly changes
  it.
- Keep repairs grounded to prior POI search results. Do not choose a new POI
  based on raw text matching or highest-power convenience alone.

Train-safe validation:
- Synthetic multi-turn POI flow: select POI A, calculate facts for A, then set
  navigation. Assert navigation uses A.

Current implementation status:
- `select_charging_plug(...)` now persists the selected station as the selected
  POI/selected charging POI, so downstream charging-time, provider-call, and
  navigation helpers have a stable station identity even when the model called
  the plug selector directly.
- `select_poi(..., role=...)` now stores explicit role-keyed aliases such as
  `selected_charging_stop_poi`, while preserving the generic `selected_poi`
  behavior when `record_selection=True`. The role must be supplied by the model
  or helper from the resolved plan; helpers do not infer it from raw user text.
- `_remember_selected_poi(..., role=..., set_current=False)` can store a
  role-only POI without overwriting the generic selected POI. The route-stop /
  open-companion helper uses this to keep the navigation stop and companion POI
  separate: the charging stop remains `selected_poi` and `selected_route_stop_poi`,
  while the non-waypoint companion is stored as `selected_companion_poi`.
- `_explicit_poi_from_current_request(...)` was removed. Runtime POI repair no
  longer selects a POI by matching the current user message. The model must call
  `select_poi(..., name=..., poi_id=..., role=...)` or use an already selected
  POI/helper role.
- Focused unit coverage:
  `test_select_poi_records_role_specific_alias` and
  `test_set_navigation_via_route_stop_with_open_poi_searches_window_and_sets_route`.

### Range/charging fact omission in active-route planning

Observed general failure:
- In long-trip or battery-concern flows, the agent sometimes uses route distance
  and SOC-distance facts but skips `get_charging_specs_and_status`, even when
  the expected reasoning requires current battery/range facts.

Why this is a helper/prompt issue:
- Existing charging helpers normalize range well once read, but the model may
  choose a lighter route that never reads charging specs.

Relevant code surfaces:
- Raw charging facts: `get_charging_specs_and_status(...)`.
- Active-route charging helpers:
  `find_charging_stop_on_active_route_by_soc(...)`,
  `search_charging_stations_on_active_route(...)`,
  `estimate_charging_stops_for_route_by_soc_window(...)`, and
  `plan_charging_for_next_meeting(...)`.
- Range normalization and unknown handling:
  `_record_unknown_charging_range(...)`,
  `_unknown_charging_range_response(...)`,
  `_abort_if_unknown_charging_range_blocks(...)`,
  `_charging_search_kilometer_from_state(...)`, and `_normalize_route(...)`.
- Email-specific EV-trip guards:
  `_long_route_email_needs_charging_facts_result(...)` and
  `_post_charge_email_needs_distance_by_soc_result(...)`.

General fix direction:
- Strengthen skill examples: when the user asks whether current battery is
  enough, read both current charging specs and route distance/SOC-distance facts
  before answering or searching chargers.
- Avoid forcing a specific charger strategy. The helper should only require the
  facts needed for the claim being made.

Train-safe validation:
- Use train EV/range tasks and unit tests where current SOC/range must appear
  before answer or charger search.

## Latest Implementation Evidence

Current implementation additions:
- `navigate_to_poi_by_arrival_weather(...)` /
  `navigate_to_poi_unless_arrival_weather(...)` now add the grounded weather
  branch result as a response obligation. If the helper has already found
  blocked arrival weather and set the fallback destination, the final answer
  must say that the weather blocked the primary POI branch and name the
  fallback branch. This uses only helper arguments, route/weather tool outputs,
  and selected route facts.
- `respond(...)` now repairs temperature-unit wording after a successful
  `set_climate_temperature(...)`: if the model says "degrees" without Celsius,
  the response is rewritten to "degrees Celsius". This is a response-format
  repair over the assistant's own text, not user-message parsing.
- The 120B skill and main prompt now clarify compound driver/passenger sync:
  multiple clauses naming the same target side should remain one
  `sync_climate_zone(...)` direction with the relevant include flags.

Target evidence:
- `output/run_configs/20260626-184959__run_configs-coroutine_base76_cerebras_gemini_3__train-trials3-base1ids-hall0-dis0__gpt-oss-120b.json`
  passed `base_76` `3/3`; all three traces copied passenger temperature and
  passenger seat-heating level onto the driver side.
- `output/run_configs/20260626-184644__run_configs-coroutine_disamb42_cerebras_gemini_3__train-trials3-base0-hall0-dis1ids__gpt-oss-120b.json`
  passed `disambiguation_42` `3/3` after confirmation success became terminal
  for the current Python execution.
- `output/run_configs/20260626-190325__run_configs-coroutine_weather_route_stop_regression_cerebras_gemini_1__train-trials1-base1ids-hall0-dis2ids__gpt-oss-120b.json`
  passed `disambiguation_53` after the arrival-weather branch obligation. The
  same run failed `base_96` only on the policy LLM: actions/tool/final passed
  with an explicit shortest-route request, but the judge said fastest was
  required.
- `output/run_configs/20260626-191521__run_configs-coroutine_seat_climate_regression_cerebras_gemini_1__train-trials1-base3ids-hall0-dis2ids__gpt-oss-120b.json`
  passed the seat/climate slice `5/5`, including `base_54`, `base_60`,
  `base_76`, `disambiguation_12`, and `disambiguation_38`.

Full helper-regression evidence:
- `output/run_configs/20260626-192315__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  scored `12/17` raw. Passed: `base_54`, `base_60`, `base_84`, `base_98`,
  both hallucination helper targets, and `disambiguation_12`, `26`, `38`, `42`,
  `48`, `53`.
- Remaining raw failures in that full run:
  `base_76` repeated the wrong-direction two-call sync pattern even after
  targeted `3/3`; this is a model argument-selection flake. Do not add a
  helper repair, because choosing which opposite-direction call is "intended"
  would require reading user wording or hidden task intent.
- `base_86` and `base_88` failed only policy LLM checks with all action/tool
  checks passing; these are the known route-options and unrelated-segment judge
  contradictions.
- `base_96` failed action matching in the full run despite the target run
  proving the explicit shortest-route branch can pass; do not force hidden
  shortest or fastest in the helper.
- `disambiguation_55` still fails raw reward only because the initial
  unsupported location lookup is counted as a tool-execution error; after the
  user correction, action/final/tool-subset/policy checks pass.

Latest full train evidence:
- `output/run_configs/20260626-225051__run_configs-coroutine_full_train_cerebras_gemini_1__train-trials1-baseall-hallall-disall__gpt-oss-120b.json`
  scored `122/129` raw, the best full train run so far on the current
  Cerebras/Gemini model combo.
- Base scored `46/50`. Two current raw misses were policy-only with all
  action/tool/final/user-end checks passing; one is the organizer-confirmed
  explicit-route-options judge contradiction. The remaining route-weather miss
  selected a valid fastest fallback route where action matching expected a
  different same-duration route; do not add hidden route-preference repair.
- Hallucination scored `47/48`. The only raw miss executed the expected safe
  AC/window/fan sequence and failed only because the user simulator ended with a
  hallucination marker. Treat as simulator/judge sensitivity unless repeated
  target trials show a wording issue.
- Disambiguation scored `29/31`. One raw miss is an active route-edit
  helper-selection/prompt issue: for unqualified final-destination replacement,
  the model asked for route choice instead of selecting the fastest route. The
  other raw miss remains the known initial unsupported-location lookup counted
  as a tool-execution error before a behaviorally correct corrected flow.
- No new helper was found overriding grounded valid model reasoning in this full
  run. The remaining safe work is helper selection/prompting and response
  wording, not runtime inference from raw user text.

Held-out full test evidence, sanitized for train iteration:
- `output/run_configs/20260626-234701__run_configs-coroutine_full_test_cerebras_gemini_1__test-trials1-baseall-hallall-disall__gpt-oss-120b.json`
  scored `105/125` raw. Use this only as general failure-class evidence; do not
  copy hidden task IDs, exact prompts, locations, contact names, route IDs, or
  answer strings into train-facing fixes.
- All failed held-out tasks had `r_tool_execution=1.0`, so there was no broad
  runtime crash or evaluator-tool bridge failure. The remaining problems are
  helper semantics, helper selection, action ordering, response wording, and
  user-simulator/judge sensitivity.
- General helper issues seen:
  reading-light occupancy final-state planning can emit contradictory calls;
  broad climate/airflow clarification can omit required current-state reads;
  contact-details email can resolve both contacts but fail to create/send the
  confirmation-backed email draft; seat/climate optimization needs broader
  handling of occupancy, unknown heating, unsupported rear seats, and
  temperature matching; route helpers need stronger toll disclosure and active
  route-stop mutation completion.
- Possible overfit signal:
  train fixes solved narrow versions of several policies, but held-out variants
  expose wider versions of the same helper contracts. Treat this as
  train-shape overfitting in helper coverage, not forbidden benchmark overfit.
  Fix by widening general helper contracts and synthetic tests, not by adding
  hidden-case branches.
- Forbidden fix boundary:
  do not add raw user-message regex/keyword inference, hidden route-preference
  repair, hidden contact/location/route IDs, or evaluator-specific behavior.

## Not Included Here

- Evaluator-only policy contradictions or user-simulator stopping behavior.
- Held-out task IDs, locations, route IDs, contact names, or exact prompts.
- Fixes that would require comparing the live tool list against a full external
  catalog.
- Fixes that infer hidden user preferences from raw text patterns.
