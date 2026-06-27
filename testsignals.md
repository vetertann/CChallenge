# Test Signals

This file is sanitized. It turns held-out validation misses into concrete,
train-safe engineering work. It intentionally does not record held-out task IDs,
exact user prompts, locations, contact names, route IDs, route labels, or hidden
expected answers.

Use this as a patch roadmap. Every item below names code surfaces, the bad
generic flow, the target flow, and train-safe validation. Do not add hidden-case
branches, raw user-message regex, evaluator-specific behavior, or evaluator-code
changes.

How to read "Where this lives":
- Prompt/skill files are where we teach the model which path to choose.
- Model-facing helpers are Python functions exposed to the agent. The evaluator
  does not see these helper names; it only sees the raw CAR-bench tool calls the
  helper makes.
- Raw CAR-bench tools are the actual evaluator-visible tool calls. Mentioning
  them here means "these are the calls that should or should not appear in the
  final trace", not "call these directly every time".
- Proposed model-facing helpers are helper names or contracts to add or tighten,
  not existing APIs unless the section says they already exist.

## Shared Fix Boundaries

- Helpers may use explicit model arguments, grounded tool outputs, stored
  preferences when explicitly requested, policy constants, scratchpad facts, and
  confirmed follow-up answers.
- Helpers must not infer missing parameters by keyword or regex matching raw
  user text.
- Route via-road wording is the only narrow raw-text exception, and only for
  matching a current-turn explicit via label against already-fetched alternatives
  for the same start and destination.
- Response text must be generated from grounded fields, not from model memory.
- Completion claims must be tied to successful evaluator-visible side effects.

## Concrete Patch Signals

### 1. Broad control request without value must ask, not set a guessed value

Where this lives:
- Prompt/skill files to change: `Skills/car_domain_120b.md`,
  `coroutine_prompts.py`.
- Raw CAR-bench tools involved: `set_fan_speed(...)`,
  `open_close_window(...)`, `open_close_sunshade(...)`. These should appear
  only after the model has a concrete level or percentage.
- Existing model-facing helpers to keep behaviorally intact:
  `present_climate_comfort_options(...)` should ask options and perform no side
  effects; `increase_fan_speed(...)` and `decrease_fan_speed(...)` should be
  used only when the model has a concrete step count or resolved target.

Bad generic flow:
```text
user asks for a broad numeric control action without a level/percentage
agent calls setter(level=some default)
assistant says it is done
user supplies the intended level later
agent corrects final state, but intermediate action already failed
```

Target flow:
```text
user asks for broad numeric control action without a level/percentage
assistant asks for the missing level/percentage
user supplies value
agent calls the setter exactly once with the supplied value
assistant confirms the grounded value
```

Patch shape:
- Add a skill rule: broad fan/window/sunshade requests without a required target
  value are clarification turns, not setter turns.
- Do not implement this by parsing raw user text in runtime helpers. The model
  should decide that the required value is missing and ask.
- Keep explicit target requests direct. Example pattern: if the model has a
  grounded `level=3`, it may call `set_fan_speed(level=3)` immediately.

Acceptance tests:
- Add synthetic prompt examples or unit harness cases where the first request
  lacks a numeric value and assert no setter call is made.
- Add matching positive cases where explicit target values still call setters.

### 2. Boolean-map responses need a formatter, not freehand list prose

Where this lives:
- Tool result path for `get_seats_occupancy(...)`
- Prompt/scratchpad display around `scratchpad["entities"]`
- Proposed model-facing helper or normalized aliases:
  `format_occupancy_state(...)`, `occupied_seats`, `unoccupied_seats`

Bad generic flow:
```text
agent calls get_seats_occupancy()
tool returns a map with some true values and some false values
assistant lists every key as occupied/active
later actions use the wrong mental state
```

Target flow:
```text
agent calls get_seats_occupancy()
runtime or model formats:
  occupied: only keys with true
  unoccupied: only keys with false
assistant reports both lists or only the relevant true list
later actions use the same separated lists
```

Patch shape:
- Add a small formatter or normalized aliases after `get_seats_occupancy(...)`
  results. Keep key-value pairs close together in any scratchpad text.
- If implemented as a helper, it should take a grounded occupancy dict and
  return lists. It should not read raw user text.
- Prefer using the formatter in examples that answer "who is in the car" or
  combine occupancy with heating/lighting work.

Acceptance tests:
- Unit test with exactly one occupied seat and several unoccupied seats.
- Unit test with mixed front/rear occupancy.
- Assert the response does not list false-valued seats as occupied.

### 3. Travel time to a selected POI must use a route lookup

Where this lives:
- POI memory: `_remember_selected_poi(...)`, `_selected_poi_role_key(...)`
- Route endpoint repair: `_repair_route_endpoint_to_selected_poi(...)`
- Proposed model-facing helper to add or tighten:
  `get_route_time_to_selected_poi(...)`
- Raw CAR-bench tools involved: `search_poi_at_location(...)`,
  `get_routes_from_start_to_destination(...)`

Bad generic flow:
```text
agent searches nearby POIs and presents one or more results
user asks how long it takes to get to a selected result
agent answers from "nearby/current location" intuition
missing route lookup causes tool-subset failure
```

Target flow:
```text
agent searches nearby POIs and stores selected/result POI IDs with names
user asks travel time to one result
agent calls get_routes_from_start_to_destination(start_id=current_or_route_start,
                                                destination_id=selected_poi_id)
assistant answers using returned duration/distance
```

Patch shape:
- Add a helper that takes `poi_id` explicitly or uses a single grounded selected
  POI from scratchpad. If several same-name POIs exist, return `AMBIGUOUS`
  instead of guessing.
- The helper should call the official route tool and return a short response
  fact, not set navigation.
- Skill example should make route-time questions after POI search use this
  helper.

Acceptance tests:
- Synthetic two-turn flow: search POIs, then ask travel time.
- Duplicate-name POI case: helper must preserve the selected POI ID or ask if
  no single selected POI is grounded.

### 4. Unqualified cabin temperature should use the default scope

Where this lives:
- `set_climate_temperature_safe(seat_zone, temperature)`
- Raw CAR-bench tool involved: `set_climate_temperature(...)`
- Skill examples in `Skills/car_domain_120b.md`

Bad generic flow:
```text
user asks for a concrete cabin temperature without naming a zone
assistant asks which zone
user stops or evaluator expects the default temperature action
```

Target flow:
```text
user asks for a concrete cabin temperature without naming a zone
agent calls set_climate_temperature_safe(seat_zone="ALL_ZONES", temperature=target)
assistant confirms the temperature and scope
```

Patch shape:
- Add skill guidance: if a concrete temperature is given and no zone is named,
  use `ALL_ZONES` unless a policy or explicit user wording requires a narrower
  zone.
- Keep `sync_climate_zone(...)` for "match this side to that side" requests.
- The helper receives `seat_zone` from the model. It should not inspect raw
  user wording to choose a zone.

Acceptance tests:
- Unqualified concrete temperature sets `ALL_ZONES`.
- Explicit driver/passenger temperature still sets only that zone.
- Zone-sync requests still read source temperature first and set only the target.

### 5. Reading-light occupancy requests need one final-state plan

Where this lives:
- Existing helper: `set_occupied_reading_lights(on=True, include_rear=True)`
- Proposed model-facing helper: `set_reading_lights_by_occupancy(...)`
- Raw CAR-bench tools involved: `get_seats_occupancy(...)`,
  `get_reading_lights_status(...)`, `set_reading_light(...)`
- Do not route these through `set_exterior_lights_safe(...)`

Bad generic flow:
```text
user asks occupied lights on and empty lights off
agent reads occupancy
agent turns occupied lights on
agent later loops over positions and turns some occupied lights off too
final state contradicts request
```

Target flow:
```text
agent reads occupancy
optional: agent reads current reading-light state if needed for minimal calls
agent builds desired_state:
  occupied supported positions -> on
  unoccupied supported positions -> off
agent emits at most one set_reading_light call per supported position
assistant confirms final state
```

Patch shape:
- Add `set_reading_lights_by_occupancy(occupied_on=True, unoccupied_on=False,
  include_rear=True)` or equivalent.
- Internally compute desired state before emitting any setter calls.
- Do not use aliases in a way that maps two positions to the same setter target.

Acceptance tests:
- Occupied front plus occupied rear both turn on.
- Unoccupied front plus unoccupied rear both turn off.
- No position receives both `on=True` and `on=False` in one request.
- Interior-light request does not call exterior-light helpers.

### 6. Contact-details email needs a stored draft and role-safe confirmation

Where this lives:
- `send_contact_details_to_contact(...)`
- `get_contact_details(..., role=...)`
- `_remember_contacts_by_id(...)`, `_remember_contact_role(...)`
- `_repair_send_email_contact_recipient(...)`
- `_repair_confirmation_contact_recipients(...)`
- `handle_pending_confirmation(...)`
- `_pending_send_email_confirmation(...)`

Bad generic flow:
```text
agent resolves email recipient contact
user later identifies another contact whose details should be sent
agent fetches subject contact details and displays them
user confirms
agent asks for email content again because no pending draft exists
send_email is never called
```

Target flow:
```text
agent resolves recipient as role=email_recipient
agent resolves details subject as role=contact_details_subject
agent fetches grounded details for subject and recipient email
agent stores pending email draft and asks confirmation
user confirms
handle_pending_confirmation() calls send_email(...)
assistant says the email was sent only after send_email succeeds
```

Patch shape:
- Make the skill strongly prefer `send_contact_details_to_contact(...)` once
  both contact roles are known.
- If the model does the flow manually, `get_contact_details(..., role=...)`
  must preserve the two roles separately.
- Confirmation handling must recognize a contact-details draft as complete
  email content. It must not ask "what should the email say" after a valid
  contact-details draft was stored.

Acceptance tests:
- Same-turn recipient and subject contact.
- Multi-turn recipient first, subject later, confirmation after draft.
- Bare yes with no stored draft still asks for missing content.

### 7. Destination POI selection must happen before route selection to that POI

Where this lives:
- `get_route_options(start_id, destination_id)`
- `select_route(...)`
- `select_route_by_user_preferences(...)`
- `navigation_replace_final_destination_guarded(...)`
- `_repair_route_ids_for_recorded_selection(...)`
- `_repair_route_ids_for_current_request_via(...)`
- `_single_segment_final_destination_needs_route_choice(...)`
- POI memory via `_remember_selected_poi(...)`

Bad generic flow:
```text
user asks to change destination to an area and find a POI there
agent fetches city/area route options
agent asks route choice before selected POI is known
user selects a POI
agent must fetch new routes to the POI and may lose the earlier route context
```

Target flow:
```text
agent resolves destination area
agent searches/selects POI inside that area
agent fetches route options to selected POI ID
if no route-option request or non-default choice is pending:
  choose policy default route
else:
  present/preserve grounded route options for that same POI destination
navigation mutation uses the route ID for selected POI destination
```

Patch shape:
- Add skill example showing "area + POI" as destination selection first, route
  selection second.
- Keep route alternatives tied to `(start_id, destination_id)`. A recorded route
  for the area must not be reused as if it were a route to the selected POI.
- Do not force fastest when the user explicitly asks for alternatives or selects
  a grounded non-default route.

Acceptance tests:
- Area plus POI request where the POI is selected before route choice.
- User asks for other route options after POI selection; selected non-default
  route is preserved.
- Route ID used in navigation mutation has destination equal to selected POI ID.

### 8. Toll/no-toll wording must be derived from selected route facts

Where this lives:
- `_route_includes_toll(...)`
- `_route_narration(...)`
- `_route_narration_record(...)`
- `_store_route_narration_sequence(...)`
- `_append_pending_route_narration(...)`
- `select_route_by_user_preferences(...)`

Bad generic flow:
```text
agent fetches route alternatives
selected route has includes_toll=true
assistant describes that selected route as "without tolls"
all action checks can pass, but policy wording fails
```

Target flow:
```text
agent chooses selected_route
route narration reads selected_route["includes_toll"] and road_types
if includes_toll is true:
  response explicitly says it uses/includes toll roads
if includes_toll is false:
  response may say it avoids/does not include toll roads
```

Patch shape:
- Prefer `route["display"]` or route narration helpers whenever route facts are
  presented.
- Add a final response repair if pending route narration says tolls but the
  outgoing text says no tolls for the same selected route.
- Do not infer toll status from alias, route name, or user preference text.

Acceptance tests:
- Fastest route includes tolls and no-toll route is slightly slower.
- No-toll route within threshold is selected and described as no-toll.
- Toll route selected by policy is not described as no-toll.

### 9. Route-stop helpers must not claim mutation before mutation succeeds

Where this lives:
- `set_navigation_via_route_stop_with_open_poi(...)`
- `set_new_navigation_via_stop(...)`
- `navigation_add_one_waypoint_guarded(...)`
- `set_new_navigation_guarded(...)`
- `_has_successful_navigation_mutation(...)`
- `_ungrounded_navigation_completion_response(...)`

Bad generic flow:
```text
agent finds a valid intermediate POI stop and companion POI
agent calculates route legs
assistant says the stop has been added
no set_new_navigation/navigation_add_one_waypoint call has succeeded yet
later turn may repair, but intermediate action failed
```

Target flow:
```text
agent finds valid stop and companion POI
agent calculates required route leg(s)
if navigation inactive:
  call set_new_navigation(...) with full ordered route chain
if navigation active:
  call navigation_add_one_waypoint(...) with before/after waypoint and route legs
assistant says "added/started" only after the mutation succeeds
```

Patch shape:
- Treat helper reports that only found candidates as planning reports, not
  mutation success.
- Response guard should downgrade premature "added/started" wording to "found a
  valid stop; I still need to add it" unless a mutation call succeeded.
- Preserve stop POI and companion POI as separate roles.

Acceptance tests:
- Active navigation: valid stop requires `navigation_add_one_waypoint(...)`.
- Inactive navigation: valid stop uses full route-chain `set_new_navigation(...)`.
- Plan-only request does not mutate navigation and does not claim mutation.

### 10. Confirmation prompts with unknown policy state need precise wording

Where this lives:
- `set_high_beams_on_safe(...)`
- `set_fog_lights_on_safe(...)`
- `set_exterior_lights_safe(...)`
- `handle_pending_confirmation(...)`
- Pending confirmation scratchpad state

Bad generic flow:
```text
agent reads policy-relevant state
blocking field is unknown
agent asks confirmation but wording sounds like the action may be impossible
user simulator stops instead of confirming
```

Target flow:
```text
agent reads policy-relevant state
if known blocked:
  stop and explain policy block
if unknown but policy allows confirmation path:
  say the checked field is unavailable
  state exact intended action and parameter
  ask for yes/no confirmation
on yes:
  call the intended setter
```

Patch shape:
- Confirmation prompt template should include:
  checked state, unknown field, intended tool/action, intended parameter, and
  yes/no request.
- Avoid "I cannot determine, so I cannot..." wording unless the policy really
  requires stopping.

Acceptance tests:
- Known safe state asks confirmation and then sets on yes.
- Known blocked state stops without asking confirmation.
- Unknown state asks confirmation with action details and does not set before yes.

### 11. Match-style helpers should overwrite unknown targets when source is known

Where this lives:
- `open_close_window_safe(...)`
- `sync_climate_zone(...)`
- Proposed model-facing helper for window matching if absent
- Raw CAR-bench tools involved: `get_vehicle_window_positions(...)`,
  `open_close_window(...)`, `get_temperature_inside_car(...)`,
  `set_climate_temperature(...)`

Bad generic flow:
```text
user asks target controls to match source controls
source values are grounded
target values are unknown
agent treats unknown target values as a blocker or gives vague wording
```

Target flow:
```text
agent reads source and target state
if source value known:
  set each target to the grounded source value
  optional response: target previous state was unknown, so it was set to match
if source value unknown:
  stop with clear limitation because desired target is unknown
```

Patch shape:
- For match helpers, only source values determine the desired target value.
- Unknown target state is safe to overwrite because the setter establishes the
  desired final state.
- Keep this fact-based; do not infer source or target from raw user wording
  inside the helper.

Acceptance tests:
- Known source, unknown target: setter fires.
- Unknown source: no setter, clear limitation.
- Mixed source values: each target gets the corresponding grounded value.

### 12. Seat-heating helpers must separate heatable front seats from unsupported rear seats

Where this lives:
- `set_occupied_seat_heating(...)`
- `turn_off_unoccupied_seat_heating()`
- `sync_climate_zone(...)`
- `set_climate_temperature_safe(...)`
- Raw CAR-bench tools involved: `get_seats_occupancy(...)`,
  `get_seat_heating_level(...)`, `set_seat_heating(...)`

Bad generic flow:
```text
agent reads occupancy and seat-heating state
rear seat is occupied but has no seat-heating control
agent either asks the user where the occupant is despite occupancy being known,
or implies rear seat heating exists
```

Target flow:
```text
agent reads occupancy and current heatable front-seat levels
for occupied heatable front seats:
  set requested heating level even if current level is unknown
for unoccupied heatable front seats:
  set level 0 when cleanup is requested
for occupied unsupported rear seats:
  say seat heating is unavailable for that rear seat
  use climate fallback only if requested or policy flow calls for comfort fallback
```

Patch shape:
- Add or tighten an occupancy heating optimizer that knows supported zones.
- Do not ask which seat the child/passenger is in when occupancy already
  provides the only occupied rear seat.
- Do not hallucinate rear seat-heating controls.

Acceptance tests:
- Occupied driver with unknown current level still gets requested level.
- Unoccupied passenger with unknown or nonzero level is set to 0 on cleanup.
- Occupied rear seat produces limitation, not `set_seat_heating(...)`.
- Combined occupancy plus temperature request does not over-apply to unsupported
  rear zones.

## Recommended Patch Order

1. `get_seats_occupancy(...)` formatter and reading-light final-state helper.
   These are deterministic and directly prevent false state/final-state bugs.
2. Route-time-to-selected-POI helper or skill example. It is a clean missing
   route-tool-call issue with low policy risk.
3. Broad numeric control clarification and unqualified temperature default
   examples. These are mostly prompt/skill fixes.
4. Contact-details email stored-draft flow. It is high-value but needs careful
   confirmation regression tests.
5. Route/toll narration and route-stop mutation guards. These touch longer
   navigation flows and should be tested with helper-regression slices.
6. Confirmation unknown-state wording and seat-heating unsupported-zone handling.
   These are important but should be validated against existing hallucination
   train slices before full-run validation.

## What Not To Do

- Do not copy held-out task wording, names, locations, route labels, route IDs,
  or expected answers into prompts, tests, or helpers.
- Do not change helper behavior to satisfy one hidden route preference.
- Do not add regex over raw user text to infer fan level, window percentage,
  route choice, contact role, POI, or climate zone.
- Do not compare the live tool list to any full external tool catalog.
- Do not touch evaluator code or third-party benchmark code.
