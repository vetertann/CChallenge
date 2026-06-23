# Remaining Base Failures To Fix

## Active Evaluation

Final-submission judge: Gemini 2.5 Flash.

Latest full run:
`output/run_configs/20260622-221000__run_configs-coroutine_base_gemini_1__train-trials1-baseall-hall0-dis0__openai-gpt-oss-120b-fast.json`

Configuration:
- Agent: `openai/gpt-oss-120b-fast`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `1`

Result: `43/50` (`86%`) raw. If the organizer-confirmed evaluator-side
`base_88` policy false negative is excluded, behavior-adjusted score is
`44/50`.

Raw failures in that full run (`7`):
- `base_28`: missed required `get_climate_settings` before relative fan change
  (targeted fix applied after this full run).
- `base_76`: read driver/passenger state but copied driver values onto
  passenger instead of passenger values onto driver.
- `base_82`: all actions passed, but first route presentation said `1.0 km`
  instead of about `1010 km`; policy LLM failed the wording.
- `base_86`: selected/called the wrong charging-station POI after computing the
  15% buffer point; policy LLM also repeated the known route-options false
  negative.
- `base_88`: all actions/final state passed; failed only the known
  evaluator-side policy interpretation about unrelated existing route segments.
- `base_96`: conditional branch chose Mannheim charging even though expected
  route was Cologne (targeted fix applied after this full run).
- `base_98`: calculated charging time for Fastned but later navigated to EnBW,
  so the post-navigation provider call was never triggered (targeted fix applied
  after this full run).

Targeted fixes applied after that full run:
- `base_28`: `increase_fan_speed` / `decrease_fan_speed` helpers read climate
  state before relative fan-speed changes.
- `base_76`: `sync_climate_zone(source_zone, target_zone)` plus source/target
  examples; latest targeted trials passed.
- `base_96`: arrival-time destination weather attention/helper; latest targeted
  trial passed.
- `base_98`: next-meeting charging plan helper, selected charger persistence,
  and selected-provider phone helper; isolated latest targeted trial passed.
- Combined targeted check
  `20260622-231433__run_configs-coroutine_base_nonregex_fix_round3_gemini_1__train-trials1-base3ids-hall0-dis0__openai-gpt-oss-120b-fast.json`
  passed all three patched cases: `base_76`, `base_96`, and `base_98`.

Prior reference run (3-trial, post facts-vs-intention refactor — see
`docs/facts-vs-intention-refactor-review.md`):
`output/run_configs/20260621-134625__run_configs-coroutine_base_gemini_3trial__train-trials3-baseall-hall0-dis0__openai-gpt-oss-120b-fast.json`

Configuration:
- Agent: `openai/gpt-oss-120b-fast`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `3`

Prior 3-trial result: Pass^1 `36/50` (`72%`), Pass^3 `32/50` (`64%`),
Pass@3 `41/50`.

CAVEAT: the prior `39/50` was a SINGLE trial and is not directly comparable to a
3-trial run (~27% per-task variance). No 3-trial run exists on the pre-refactor
code, so the refactor's net effect is not yet attributed.

Targeted one-trial mechanism checks after the reliability/ergonomics pass:
- `20260621-151010...followup_gemini_1`: `base_48` and `base_70` passed.
- `20260621-151530...followup2_gemini_1`: `base_84` passed.
- `20260622-212255...calendar_poi_ev...`: `base_20`, `base_68`, and
  `base_70` passed on current Cerebras/Gemini after calendar normalization,
  POI route-target persistence/base-route mapping, and EV charge-need prompt
  tightening.
- `20260622-214342...contact_routechain...`: `base_78` and `base_84` passed on
  current Cerebras/Gemini after contact-recipient grounding and route-chain
  validation/repair.
- The latest full run supersedes some earlier targeted uncertainty: `base_82`
  now has correct actions but bad distance wording, while `base_86` still has a
  real downstream charging-station/phone mismatch in addition to the known
  route-options policy false negative.

These targeted passes/failures are one-trial evidence only. They update the
known mechanism, not the 3-trial solid/flaky classification above.

Prior 3-trial hard-fail bucket (0/3) — `9`:
- `base_42`, `base_48`, `base_60`, `base_74`, `base_78`, `base_82`, `base_86`,
  `base_96`, `base_98`

Prior 3-trial flaky bucket (1–2/3) — `9`:
- `base_2`, `base_14`, `base_38`, `base_52`, `base_58`, `base_64`, `base_70`,
  `base_84`, `base_88`
- `base_84`/`base_88` improved hard-fail → flaky this run; `base_64`/`base_70`
  (paths touched by the refactor) regressed solid → flaky — variance vs
  helper-unlock cost not yet separated.

Previously patched since the 35/50 run:
- `base_64`: the `navigation_add_one_waypoint_guarded` completeness
  bug + always-narrate the selected route (was `NavigationAddOneWaypoint_008` +
  missing policy-022 narration). It was flaky in the three-trial run; duplicate
  insertion is now also guarded as a truthful no-op.
- `base_70`: route-presentation narration now fires on a plain
  `get_routes` presentation, not only on edits.
- `base_14`, `base_58` flipped to pass but were not direct targets — treat as
  unconfirmed (single-trial; could be noise) until a multi-trial run.

Runtime changes landed for this run (see `docs/coroutine-agent-architecture.md`):
dynamic-key normalization (`get_distance_by_soc` → `distance_km`,
`calculate_charging_time_by_soc` → `minutes`); always-narrate on add; active-nav
guard returns a structured `NEEDS_ACTIVE_ROUTE_EDIT` fact block without choosing
the edit; idempotent no-op when deleting an already-removed waypoint; the
`navigation_add_one_waypoint_guarded` completeness fix.

Implemented after this run and exercised by targeted Gemini checks:
- Durable policy-response obligations and accumulated helper messages
  (`base_52`, `base_60`).
- Mutation failures persist across model Python blocks; repeated reads are
  same-turn cached and marked `no_progress`.
- Exact window proof, nested contact-name normalization, charging-plug
  normalization, and phone/email whitespace normalization (`base_58`, `_78`,
  `_84`, `_86`).
- Navigation revisions, returned-state persistence, stale route-option
  invalidation, selection provenance, and duplicate-add no-op (`base_48`, `_64`,
  `_82`).
- Calendar entries from `get_entries_from_calendar(...)` now expose
  `start_time`/`start_time_24h`, numeric start fields, and display strings
  (`base_20`).
- POI summaries keep POI name, `navigation_id`, `poi_id`, host location ID/name,
  and display text close together. Final-destination replacement maps a
  POI-route `base_route_id` to the actual POI route ID before emitting the
  evaluator call (`base_68`).
- The skill/prompt now explicitly require `get_charging_specs_and_status()`
  before answering whether a trip needs charging (`base_70`).
- Confirmation-required `send_email(...)` now repairs a single-recipient email
  when a unique contact-set intersection is known and the selected email belongs
  to a different grounded contact (`base_78`).
- `set_new_navigation(...)` now validates known multi-leg route chains and can
  replace a stale/base route ID with the unique known route that starts where
  the previous leg ends (`base_84`).

## Active Root Causes

### `base_28`: relative fan-speed change skips state read — TARGETED FIX

The user asked to move airflow to feet and increase fan speed "one level". The
agent emitted `set_fan_airflow_direction(direction="FEET")` and
`set_fan_speed(level=1.0)`, which matched the final state, but it skipped the
required `get_climate_settings()` read for the relative fan-speed request.
Evaluator scoring: all action/final checks passed, but `r_tool_subset=0` for
missing `get_climate_settings`.

Implemented:
- Added `increase_fan_speed(steps=...)` / `decrease_fan_speed(steps=...)`.
  These helpers read `get_climate_settings()` first, then set the calculated
  level.
- Added prompt/skill guidance to use these helpers for explicit relative
  fan-speed deltas.
- Targeted run `20260622-224610...base_nonregex_fix...` passed `base_28`.

### `base_76`: driver/passenger sync direction reversed — TARGETED FIX

The agent correctly read `get_temperature_inside_car()` and
`get_seat_heating_level()`. The read values were driver `27°C` / seat heat `3`
and passenger `16°C` / seat heat `1`. The user asked to sync driver settings to
match passenger, but the agent called:
`set_climate_temperature(seat_zone="PASSENGER", temperature=27.0)` and
`set_seat_heating(seat_zone="PASSENGER", level=3.0)`.

This was a real action failure: it copied driver values onto passenger instead
of passenger values onto driver.

Implemented:
- Added `sync_climate_zone(source_zone, target_zone, ...)`.
- Added explicit source/target wording: "set driver to match passenger" means
  `source_zone="PASSENGER", target_zone="DRIVER"`.
- Targeted runs:
  `20260622-225815...round3...` and `20260622-230404...round4...` both passed
  `base_76`; combined targeted run `20260622-231433...final3...` also passed.

### `base_64`: waypoint flow — PREFLIGHT FIX, TARGETED 3/3

`navigation_add_one_waypoint_guarded` treated `waypoint_id_after_new_waypoint`
as sufficient and forwarded the raw call even when the route-away dependency was
missing → `NavigationAddOneWaypoint_008`. Now it resolves both the to-route and
(for mid-route inserts) both after-args via `_resolve_route_arg`, and narrates
the selected route. A fresh-state duplicate add now returns
`already_present: True` / `waypoint_added: False` without emitting the invalid
mutation. Unit tests cover both paths.

The two-trial full-base run `20260621-223750...` failed 0/2 for a different
reason. In both trials every expected action and final state passed, but the
navigation observation boundary stopped Python before a successful mixed
batch's Stuttgart and Cologne lookup results could be assigned. The next model
decision emitted two route reads with `loc_stg_???`, causing
`r_tool_execution=0`.

The boundary is now removed. Current navigation is read before the first model
decision and stored as neutral scratchpad facts, so model-written batches return
normally. ID arguments containing question-mark placeholders are also blocked
before the evaluator.

Targeted Gemini evidence after the preflight replacement:
- `20260622-095519...preflight_target...`: passed.
- `20260622-095907...preflight_target...`: passed.
- `20260622-100158...preflight_target...`: passed.

This confirms the specific boundary regression is gone. Full-split validation
is still needed before treating the task as stable.

### `base_78`: recipient identity from contact-set intersection — PASSED TARGETED CHECK

All Scott contact IDs were grounded, but `get_contact_details` validation did
not accept nested fields such as `name.first_name` and `name.last_name`.

Nested contact normalization is fixed and all Scott details were grounded. The
remaining failure is recipient selection: a first-name-only Nathan lookup
returned four contacts and the model chose the first (Nathan Carter) instead of
intersecting it with the already-grounded Scott IDs to select Nathan Scott.

Implemented after the failure: `id_value()` recognizes exactly one normalized
contact ID and rejects ambiguous lists; prompt/skill include the general
set-intersection algorithm and prohibit first-result selection. The confirmation
gate now also repairs the final grounded email action if there is a unique
contact intersection and the model selected a different known contact's email.

Targeted Gemini/Cerebras evidence:
- `20260622-214342...contact_routechain...`: passed. The agent sent the email to
  Nathan Scott (`con_1139`) after confirmation and included the other Scott
  contacts.

### `base_58`: phone normalization — PATCHED, RE-EVAL NEEDED

Previously the POI phone was called with leading whitespace, breaking the
action match. Phone arguments now strip surrounding whitespace at the wrapper
boundary while preserving the evaluator-provided value otherwise.

### `base_48`: route choice/persistence — FIXED IN TARGETED POLICY-TAIL RUN

Destination replacement succeeded, but later alternative-route selection used
delete-and-recreate behavior or failed to complete against the selected route.

The deterministic route-selection checkpoint was removed because it depended on
regex interpretation of user language. The replacement wrapper remains
fact-only. Current navigation is preflighted before the first model decision.
The static single-segment versus multi-stop reminder now follows the serialized
scratchpad, next to the current route shape. Actual prior user and assistant
messages are retained so follow-ups can refer to a route the assistant just
presented.

Evidence:
- Prompt-only route-state injection: `20260621-212157...`, `base_48` 0/3.
- Observation boundary plus dialogue continuity:
  `20260621-213348...route_facts...`, `base_48` 3/3.
- The same run passed 5/6 across `base_48` and `base_82`.
- Initial preflight probes `20260622-095519...` and `20260622-095907...` failed:
  the model still selected fastest while the reminder was distant in the system
  prompt.
- After moving the same static reminder next to the scratchpad facts,
  `20260622-100158...preflight_target...` passed `base_48`, `base_64`, and
  `base_82` 3/3 overall. This is one targeted trial per task.

### `base_82`: route actions correct, first presentation corrupted distance

The latest run got the full action flow right: it read active navigation,
resolved Berlin, presented route options, waited for the user to choose K57/B65,
and called `navigation_replace_final_destination(...)` with
`rll_rig_ber_558409`.

The remaining failure was verbal: the first route presentation said
`A74, 1.0 km, 32 minutes` even though the tool returned `1010.08 km` and
`12h 32m`. The later expanded list was correct. The evaluator failed only
`r_policy` for inaccurate distance formatting.

The replacement wrapper deliberately does not parse phrases such as `K57 and
B65`; the model interprets them. Navigation preflight ensures the model sees the
two-waypoint state before its first decision, without interrupting Python or
injecting a dynamic user message.

Fix direction:
- Route summaries should expose a compact `display` string with route id,
  `name_via`, full `distance_km`, and `duration`, so the model can copy the
  representation instead of reconstructing units from separate fields.
- Preserve `duration_total_minutes`, but keep human display next to it to avoid
  "12h 32m" becoming "32 minutes".

Evidence:
- Before the batch boundary, `20260621-213348...route_facts...` passed 2/3.
  The one failure put the first navigation read inside `batch(...)`, bypassed
  the model-decision boundary, and selected fastest prematurely.
- After the batch boundary, `20260621-213609...base_82_route_boundary...`
  scored 1/3, but agent behavior was policy-correct in all three trials: it read
  state and presented alternatives without a premature mutation. In two trials
  Gemini stopped after the options instead of selecting its instructed K57/B65
  route, so the evaluator reported the final replacement tool missing.
- The three preflight target probes on `20260622` all passed `base_82`; the last
  also passed `base_48` and `base_64`.
- Full run `20260622-221000...base_gemini_1`: actions passed, policy failed
  only on the first distance string.

No task-specific route-choice wrapper logic is proposed.

### `base_88`: completed deletion is retried — policy failure evaluator-side

The delete-loop is fixed: `navigation_delete_waypoint_guarded` now returns an
idempotent no-op success when the target waypoint is already absent, so the
repeated delete no longer errors and loops.

Organizer Q&A on 2026-06-22 confirmed the remaining policy failure was
evaluator-side: after a waypoint deletion, LLM-POL:022 applies to the newly
created segment only. The agent does not need to rewrite unrelated existing
segments such as Berlin→Leipzig.

Latest run `20260622-221000...base_gemini_1`: all action and final checks
passed again. The only failure was the same evaluator-side policy error claiming
the unrelated Berlin→Leipzig segment should also have been changed to fastest.
The response wording was already segment-scoped.

Fix direction (remaining):
- Treat current train-split `base_88` failures as evaluator-side for score
  analysis.
- Keep route-edit narration segment-scoped; do not rewrite unrelated existing
  route segments.

### `base_42`: relative adjustments are guessed or replayed

`base_42` ("more air circulation") jumped the fan two levels (2→4) and the
request is tool-ambiguous (fan speed vs air-circulation mode). (`base_14`,
previously grouped here, passed this run but was not a direct fix target —
unconfirmed.)

Fix direction:
- For relative words with no explicit target, read current state and apply one
  defined step or clarify; do not guess the magnitude.
- Disambiguate "air circulation" between the candidate tools.

### `base_60`: compound climate warning can be lost — PATCHED, RE-EVAL NEEDED

Implemented: the prompt/skill reserve `set_occupied_seat_heating` for requests
covering all occupied seats; explicit zones use `set_seat_heating`. Ordinary
helpers accumulate messages without locking compound turns. The policy-012
warning is now a durable response obligation that `respond(...)` appends only
when the model omitted it. Explicitly confirmed pending actions still complete
with a grounded locked response.

### `base_70`: route/email/charging flow — PASSED TARGETED CHECK

The latest targeted Cerebras/Gemini run passed. Route narration fired, the agent
read `get_charging_specs_and_status()` before deciding whether the trip needed
charging, `send_email(...)` stored the grounded recipient/content confirmation,
and `handle_pending_confirmation()` sent it after yes. The earlier 3-trial
result remains flaky, so this is not yet a solid classification.

Fix direction (carried forward for other charging tasks):
- Re-check in a full base split. Keep the generic EV-route planning checklist
  focused on official charging reads and grounded route facts.

### `base_74`: premature email confirmation and duration corruption

The model asked for email confirmation before grounding the complete plan and
reported `38 min` instead of `14 h 38 min`, while losing route and plug details.

Fix direction:
- Preserve typed duration values rather than reconstructing them from prose.
- Delay confirmation until all required message facts are grounded.
- Build the final email body from structured route and charging data.

### `base_84`: charging POI and two-leg navigation — PASSED TARGETED CHECK

The POI result already contained the plug identifier, but the model asked the
user to provide it.

The clean targeted rerun passed: the model used the available DC plug, routed to
the POI ID (not Warsaw's host location), selected fastest for that leg, selected
the requested second Hamburg route, and set both route IDs together.

After the earlier stale-route failure, route facts are now also persisted
cumulatively in `routes_by_id`, including `base_route_id`. Before
`set_new_navigation(...)` emits a known multi-leg chain, the wrapper checks that
each next route starts where the previous route ends. If the model supplies an
old city-to-destination route but a unique known POI-to-destination route maps
to it through `base_route_id`, the wrapper rewrites the second leg. If all route
facts are known and no unique repair exists, it returns `ROUTE_CHAIN_MISMATCH`
instead of sending an impossible route chain to the evaluator.

Targeted Gemini/Cerebras evidence:
- `20260622-214342...contact_routechain...`: passed. In this live trial the
  model selected the correct chain itself
  (`rlp_war_cha_224861`, `rpl_cha_ham_429250`), so the repair path was not
  needed; unit coverage exercises the stale/base-route repair path directly.

### `base_86`: charging point is computed from the wrong segment origin — PARTIAL

Phone persistence/calling is fixed: the model called a grounded number. The
remaining action mismatch comes earlier. In the latest run it first searched at
`at_kilometer=600` and found Fastned, then after the user requested the 15%
buffer it calculated `get_distance_by_soc(98, 15) -> 394 km` and searched the
Frankfurt→Barcelona route at `394 km`. The expected 15% buffer point is
`50 km` into the Frankfurt→Barcelona segment because the car must first spend
range on the Leipzig→Frankfurt leg before reaching that segment.

The agent then called the phone number for the Ionity found at the wrong
kilometer. The route-option policy failure in this task is still the
organizer-confirmed evaluator-side issue, but the charging-station/phone action
failure is real.

Implemented after the failure: numeric `remaining_range_km`, POI detour aliases,
and explicit multi-stop EV guidance to derive range/SOC at the later segment's
start. In `20260621-203852...final_destination_regression_gemini_1`, the
multi-stop route was not blocked and the Barcelona destination replacement
executed. Organizer Q&A on 2026-06-22 confirmed the policy-evaluator
contradiction was evaluator-side: the user's explicit request to see multiple
route options overrides the default fastest-route rule. Remaining agent work, if
any, is downstream charging/phone correctness, not suppressing route options.

### `base_96`: weather-conditioned navigation uses wrong time — TARGETED FIX

The expected branch was Cologne. The agent read Mannheim weather at `16:00`
(`time_hour_24hformat=16`) and saw `cloudy`, then routed to a Mannheim charging
station. The expected weather call is at policy time `19:00`; at that time the
scenario evidently requires the rain branch and direct Cologne navigation.

Implemented:
- Added `get_weather_at_route_arrival(...)` for navigation decisions conditioned
  on destination weather.
- Added preflight attention reminding the model that weather-dependent
  navigation should use arrival-time destination weather, not current remote
  weather.
- Targeted run `20260622-230404...round4...` passed `base_96`: the agent checked
  Mannheim weather at arrival time (`19:23`), observed rain, presented Cologne
  route options, then set the user-selected shortest Cologne route. Combined
  targeted run `20260622-231433...final3...` also passed.

### `base_98`: EV charging plan loses max-window/provider-call semantics — TARGETED FIX

The agent now uses calendar, charging, route, POI search, and charging-time
tools. The failure changed: it calculated time using Fastned
`poi_cha_363177` / plug `plg_cha_564167`, but later routed to EnBW
`poi_cha_425198`. Because it navigated to the wrong charging station, it never
called the Fastned provider number after navigation.

Later targeted runs exposed two additional general issues:
- The model sometimes computed "maximum charging time" as time to 80%/100%
  charge instead of the schedule window before the meeting.
- On the provider follow-up, the model could refuse because there is no
  reservation API, even though the supported task is to call the provider.

Implemented:
- `select_charging_plug(pois)` keeps station id, plug id, power, availability,
  navigation id, and phone number together.
- `set_new_navigation(...)` validates known charging-plan route chains so the
  route target stays aligned with the station used for charging-time
  calculation.
- `call_selected_charging_provider()` resolves the selected station phone number
  from grounded charger/navigation state and calls `call_phone_by_number(...)`.
- `plan_charging_for_next_meeting(range_buffer_km=40,
  arrival_buffer_minutes=5)` computes min charging time and max schedule window
  for next-meeting charging requests. It stores the selected fastest charger and
  plan facts for follow-up navigation and phone calls.
- Targeted run `20260622-231154...base98_plan_helper...` passed `base_98`: the
  agent reported min `3` / max `40`, set navigation through Fastned, then called
  `+49 110 1244459`. Combined targeted run `20260622-231433...final3...` also
  passed.

## Recommended Order

Implemented: `base_64` waypoint protocol/duplicate guard, `base_70` route
narration, nav-delete idempotency (`base_88` loop), weather day-clamp + toll
(`base_96` partial), distance dynamic-key alias, contact-recipient grounding
(`base_78`), and route-chain validation/repair (`base_84`). These remain
subject to the latest flaky/failing evaluation evidence above.

Remaining, in priority order:
1. Later-segment EV range calculation for `base_86`.
2. Re-run a broader base split to check whether the new charging helper causes
   any collateral behavior changes.
3. Treat `base_88` as evaluator-side in train-split score analysis; do not
   rewrite unrelated existing route segments.
