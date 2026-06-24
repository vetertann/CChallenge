# Remaining Base Failures To Fix

## Active Evaluation

Final-submission judge: Gemini 2.5 Flash.

Latest full train run:
`output/run_configs/20260624-204337__run_configs-coroutine_full_train_cerebras_gemini_3__train-trials3-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent provider: Cerebras
- Agent model: `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `3`

Result for base split: `127/150` (`84.7%`) raw.

Split stability:
- Pass^1: `43/50` (`86.0%`)
- Pass^2: `40/50` (`80.0%`)
- Pass^3: `39/50` (`78.0%`)
- Pass@3: `45/50` (`90.0%`)

Failure-subset comparison run:
`output/run_configs/20260624-222919__run_configs-coroutine_failures_qwen_nebius_gemini_1__train-trials1-base11ids-hall2ids-dis18ids__Qwen-Qwen3.5-397B-A17B.json`

Qwen/Nebius configuration:
- Agent provider: Nebius
- Agent model: `Qwen/Qwen3.5-397B-A17B`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `1`
- Scope: latest non-3/3 tasks only.

Qwen result on base failure subset: `3/11`.

Current non-solid base tasks:

| Task | Cerebras full run | Qwen subset | Current reading |
| --- | --- | --- | --- |
| `base_48` | `0/3` | fail | Real action-order issue plus wrapper narration risk. The agent replaced the destination with the default fastest route before the user selected the requested second route, then later switched to the second route. Final state passed, but `r_actions=0`. The helper-generated route narration also said "fastest route" after the non-fastest second-route switch, which can make good route selection look contradictory. |
| `base_54` | `2/3` | pass | Only one trial failed, and only policy failed. The bad response said `22 degrees` instead of `22 degrees Celsius`; tool actions were correct. This is a response-units formatting issue, not a tool-planning issue. |
| `base_56` | `0/3` | fail | Tool actions, final state, tool execution, tool subset, and policy all passed. The failure was `r_user_end_conversation=0`: after correctly deleting Nuremberg and taking the fastest direct Paris route, helper/obligation text invited "other options", the user simulator continued, and the agent kept discussing alternatives. This is a helper-generated follow-up problem overriding an otherwise complete route-edit flow. |
| `base_66` | `2/3` | pass | One policy-only failure. The destination replacement to Munich succeeded; evaluator complained the assistant did not mention tolls for the old Andorra -> Paris route that was already active before the requested edit. Treat as low-priority policy wording/evaluator sensitivity. |
| `base_74` | `1/3` | fail | Compound route/email/charging task. One trial passed. Failures split between missing the full charging/email tool bundle and re-asking confirmation instead of sending the second email after confirmation. Needs a complete "draft email only after route + charging facts are ready" plan, without prematurely confirming incomplete email content. |
| `base_82` | `0/3` | fail | User wanted the Berlin route via `K57, B65`. In two trials the model did eventually select that route, but it first committed the fastest route and helper narration then described the selected `K57, B65` route as "fastest". Trial 2 stopped after the first fastest replacement. Needs route-option presentation without premature commit and provenance-aware narration that does not overwrite user-selected route reasoning. |
| `base_84` | `2/3` | pass | One trial failed after the route/charging flow had enough information. The agent set a two-leg route via Ionity, then later used `navigation_replace_final_destination`, turning a good multi-leg setup into a mismatched action sequence. Helper narration again invited route switching after an already-complete navigation setup. |
| `base_86` | `0/3` | fail | Real downstream EV/charging failure remains. The known organizer-confirmed route-options policy contradiction appears in some trials, but the task also fails action checks: the agent finds or describes a charger inconsistently, sometimes reports a `None kW` plug, and does not complete the expected charging-station/provider flow reliably. |
| `base_88` | `2/3` | fail | Two trials passed. The failed trial used `search_poi_at_location` instead of the expected route-based charging search after removing Bonn, so `search_poi_along_the_route` was missing. The earlier unrelated-segment policy false negative is not the current dominant issue in this 3-trial run. |
| `base_96` | `1/3` | fail | One trial passed. Failures are branch-planning instability: one skipped `get_weather` and assumed clear weather; another hit an internal issue, then tried `navigation_replace_final_destination` while navigation was inactive before recovering with `set_new_navigation`. Needs conditional route setup to keep weather read and inactive-navigation state tied to the final action. |
| `base_98` | `0/3` | fail | The agent computed meeting route, charging status, charger, and charging time, but then set direct navigation to Stuttgart and called the charging provider. Expected behavior is a multi-leg navigation via the charging stop. This is the strongest remaining base planning gap. |

Helper-overrides-good-reasoning watchlist:
- `base_56`: successful waypoint deletion was followed by helper/obligation text inviting route alternatives, causing unnecessary continuation and user-end failure. Suggested tuning: after a state-changing route edit succeeds and the user did not ask to see alternatives, helper narration should be terminal and should not append "other options" prompts.
- `base_82`: model-selected `K57, B65` route was followed by generic "fastest route" narration, contradicting the user's selected non-default route. Suggested tuning: route narration must be provenance-aware; if the selected route came from user alias/name-via/preference, describe that basis and do not reuse default-fastest wording.
- `base_84`: a valid two-leg charger route was later disturbed by route-switch invitation/replacement behavior. Suggested tuning: once `set_new_navigation(...)` succeeds for a complete multi-leg route, mark that navigation plan complete for the turn and suppress later final-destination replacement or route-option follow-up unless the user explicitly asks to change it.
- `base_48`: after the model honored the user's second-route selection, generic route narration still claimed the selected route was fastest. Suggested tuning: route-edit helpers should store selected-route provenance (`fastest`, `shortest`, `alias`, `name_via`, `user_selected`) and only narrate facts true for that specific route.

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
- The 2026-06-24 3-trial full run supersedes some earlier targeted uncertainty:
  `base_28` and `base_76` are stable, but `base_82` and `base_98` are now hard
  failures again. `base_86` still has a real downstream EV/charging-station
  failure in addition to the known route-options policy contradiction.
  `base_96` remains flaky on conditional weather/route setup.

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

### `base_58`: phone normalization — PASSED LATEST FULL RUN

Previously the POI phone was called with leading whitespace, breaking the
action match. Phone arguments now strip surrounding whitespace at the wrapper
boundary while preserving the evaluator-provided value otherwise.

### `base_48`: route choice/persistence — ACTIONS PASS, POLICY FAILS

Latest full run:
- User first asked to search restaurants in Munich, then asked to change final
  destination to Munich.
- The agent presented the fastest route and asked which route to use.
- The user asked to see the second route and then selected it.
- The agent called `navigation_replace_final_destination(...)` with the second
  route `rll_dor_mun_475855`.
- Evaluator action, final-state, tool-execution, and tool-subset checks all
  passed. Only `r_policy` failed because the policy judge expected proactive
  fastest-route selection.

Interpretation:
- This is a train-split route-policy/action mismatch candidate, not a clean
  agent bug. The action script rewarded the selected second route, while the
  policy judge wanted fastest-route behavior.
- Do not hardcode against this task. If we change behavior here, it should come
  from a general policy clarification about when a route-edit should present
  options versus proactively commit fastest.

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

### `base_56`: waypoint deletion completes, then helper invites continuation — ACTIVE

Latest full 3-trial run:
- User asked: remove Nuremberg and go straight to Paris.
- The agent read the current Wiesbaden -> Nuremberg -> Paris route.
- The agent called `navigation_delete_waypoint(...)` with the correct direct
  fastest route.
- Tool execution, tool subset, action checks, final-state checks, and policy all
  passed in all three trials.
- The run still scored `0/3` because `r_user_end_conversation=0`: route
  narration invited "other options", the user simulator continued, and the agent
  answered those follow-ups instead of ending after the completed edit.

Fix direction:
- For direct waypoint deletion where the user did not ask to see alternatives,
  the helper/prompt should commit the fastest replacement route and avoid
  inviting further route-choice continuation after the edit is complete.
- Keep the model flexible for explicit route-choice requests. This should not
  block cases like `base_86`, where the user explicitly asked for multiple
  route options.

Implemented before the latest full run, but insufficient:
- 120B skill rule: for unqualified intermediate-waypoint deletion, fetch the
  previous-to-next alternatives and immediately call `navigation_delete_waypoint`
  with the fastest replacement route.
- Runtime memory: successful navigation edit mutations are remembered across the
  next user turn, so a follow-up answer no longer says navigation was not set
  after `navigation_delete_waypoint(...)` already succeeded.

Target evidence:
- Before persistent mutation memory,
  `20260624-135954__run_configs-coroutine_base56_cerebras_gemini_3...` passed
  `2/3`; the remaining failure was the false follow-up denial after a successful
  delete.
- After the memory fix,
  `20260624-150401__run_configs-coroutine_base56_cerebras_gemini_3...` passed
  `3/3`.

### `base_82`: user-selected route overridden by premature fastest commit — ACTIVE

Latest full 3-trial run: failed `0/3`. The targeted fixes solved earlier ID
and route-presentation bugs, but the current failure is different: the agent
commits the default fastest route before the user-selected `K57, B65` route is
resolved, and helper narration then describes the final selected route as
"fastest".

Previous failure shape:
- The agent got the full action flow right: it read active navigation, resolved
  Berlin, presented route options, waited for the user to choose K57/B65, and
  called `navigation_replace_final_destination(...)` with `rll_rig_ber_558409`.
- The remaining failure was verbal: the first route presentation said
`A74, 1.0 km, 32 minutes` even though the tool returned `1010.08 km` and
`12h 32m`. The later expanded list was correct. The evaluator failed only
`r_policy` for inaccurate distance formatting.

The replacement wrapper deliberately does not parse phrases such as `K57 and
B65`; the model interprets them. Navigation preflight ensures the model sees the
two-waypoint state before its first decision, without interrupting Python or
injecting a dynamic user message.

Fix direction if it regresses:
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

### `base_88`: waypoint deletion policy false negative plus charging-search miss

The delete-loop is fixed: `navigation_delete_waypoint_guarded` now returns an
idempotent no-op success when the target waypoint is already absent, so the
repeated delete no longer errors and loops.

Organizer Q&A on 2026-06-22 confirmed the remaining policy failure was
evaluator-side: after a waypoint deletion, LLM-POL:022 applies to the newly
created segment only. The agent does not need to rewrite unrelated existing
segments such as Berlin→Leipzig.

Latest run `20260624-121323...full_train...`: all action and final checks
passed again. The policy failure was the same evaluator-side claim that the
unrelated Berlin -> Leipzig segment should also have been changed to fastest.
However, this run also had `r_tool_subset=0`: the agent searched for charging
near Berlin with `search_poi_at_location(...)`, while the expected tool subset
included `search_poi_along_the_route`.

Implemented after the latest full run:
- Added a 120B skill rule that route-based charging should use
  `search_poi_along_the_route(...)`, not a city/waypoint POI search, unless the
  user explicitly asks for chargers near a city or POI.
- Added a second rule for active multi-stop navigation: "along the way" means
  the current active route segments in `navigation_state["route_ids"]`; do not
  replace them with a newly fetched direct start-to-final route unless the user
  explicitly asks for a direct route or removal of the intermediate stop(s).

Target evidence:
- `20260624-150612__run_configs-coroutine_base88_cerebras_gemini_3...` scored
  raw `0/3`. Two trials were all-actions-pass policy false negatives; one trial
  exposed a real direct-route/charging follow-up error.
- `20260624-151318__run_configs-coroutine_base88_cerebras_gemini_3...` scored
  raw `0/3` again. Trials 0 and 2 had `r_tool_subset=1`, `r_actions=1`, and
  failed only on the known unrelated-segment policy false negative. Trial 1
  still used `search_poi_at_location(...)` near Berlin and remains a real
  route-charging miss.

Fix direction (remaining):
- Keep route-edit narration segment-scoped; do not rewrite unrelated existing
  route segments.
- For charging after a long route edit, search along the active/new route unless
  the user explicitly asks for chargers near a specific city or stop. This is a
  general route-charging rule, not a Bonn/Berlin-specific branch.

### `base_42`: relative adjustments are guessed or replayed — PASSED LATEST FULL RUN

`base_42` ("more air circulation") jumped the fan two levels (2→4) and the
request is tool-ambiguous (fan speed vs air-circulation mode). (`base_14`,
previously grouped here, passed this run but was not a direct fix target —
unconfirmed.)

Fix direction:
- For relative words with no explicit target, read current state and apply one
  defined step or clarify; do not guess the magnitude.
- Disambiguate "air circulation" between the candidate tools.

### `base_60`: compound climate warning can be lost — PASSED LATEST FULL RUN

Implemented: the prompt/skill reserve `set_occupied_seat_heating` for requests
covering all occupied seats; explicit zones use `set_seat_heating`. Ordinary
helpers accumulate messages without locking compound turns. The policy-012
warning is now a durable response obligation that `respond(...)` appends only
when the model omitted it. Explicitly confirmed pending actions still complete
with a grounded locked response.

### `base_70`: route/email/charging flow — PASSED LATEST FULL RUN

The latest targeted Cerebras/Gemini run passed. Route narration fired, the agent
read `get_charging_specs_and_status()` before deciding whether the trip needed
charging, `send_email(...)` stored the grounded recipient/content confirmation,
and `handle_pending_confirmation()` sent it after yes. The earlier 3-trial
result remains flaky, so this is not yet a solid classification.

Fix direction (carried forward for other charging tasks):
- Re-check in a full base split. Keep the generic EV-route planning checklist
  focused on official charging reads and grounded route facts.

### `base_74`: premature email confirmation and incomplete charging bundle — FLAKY

Latest full 3-trial run: `1/3`. One trial completed the full route, charging,
confirmation, and email flow. The failed trials either skipped required charging
tools before email confirmation or re-asked confirmation instead of sending the
second email after the user already confirmed.

Earlier failure shape: the model asked for email confirmation before grounding
the complete plan and reported `38 min` instead of `14 h 38 min`, while losing
route and plug details.

Fix direction:
- Preserve typed duration values rather than reconstructing them from prose.
- Delay confirmation until all required message facts are grounded.
- Build the final email body from structured route and charging data.

### `base_84`: charging POI and two-leg navigation disturbed by route switching — FLAKY

Latest full 3-trial run: `2/3`. The good path sets a two-leg route via the
charging POI. The failed trial first set the two-leg route, then later used
`navigation_replace_final_destination(...)`, turning a good route-chain into a
mismatched action sequence.

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

The latest full run changed the failure shape:
- Barcelona destination replacement succeeded.
- The route-options policy check passed in this run; the organizer-confirmed
  route-options false negative remains historical evidence only.
- After the user asked about range and a 15% buffer, the agent called
  `get_charging_specs_and_status()`, looked up Frankfurt, and re-read the
  Leipzig -> Frankfurt leg.
- It responded with vague "I need a bit more information" text, then searched
  the Frankfurt -> Barcelona route at `at_kilometer=0.0`.
- No useful charging station was selected, and the expected
  `call_phone_by_number(...)` never happened.

The real remaining failure is still the later-segment EV calculation: the agent
must account for the energy spent reaching Frankfurt before deciding where 15%
SOC will occur on the Frankfurt -> Barcelona segment. Once the station is found,
the provider phone call should be grounded from that selected POI.

Implemented after the failure: numeric `remaining_range_km`, POI detour aliases,
and explicit multi-stop EV guidance to derive range/SOC at the later segment's
start. In `20260621-203852...final_destination_regression_gemini_1`, the
multi-stop route was not blocked and the Barcelona destination replacement
executed. Organizer Q&A on 2026-06-22 confirmed the policy-evaluator
contradiction was evaluator-side: the user's explicit request to see multiple
route options overrides the default fastest-route rule. Remaining agent work, if
any, is downstream charging/phone correctness, not suppressing route options.

### `base_92`: fog-light confirmation policy contradiction candidate

Latest full run:
- The agent checked Leipzig weather at the current policy time and got
  `partly_cloudy`.
- It checked exterior lights, asked confirmation to turn high beams off and fog
  lights on, then after confirmation called `set_head_lights_high_beams(False)`
  and `set_fog_lights(True)`.
- All tool and final-state checks passed. Only `r_policy` failed.

Policy issue:
- `docs/policy.md` says fog lights require confirmation if the weather is not
  one of `cloudy_and_thunderstorm` or `cloudy_and_hail`.
- `partly_cloudy` is not one of those two conditions, so the current helper's
  confirmation behavior follows the written policy.
- The policy judge said confirmation was unnecessary. Treat this as likely
  evaluator-side until clarified; do not weaken the fog-light helper just to fit
  this train failure.

### `base_96`: weather branch fixed, route preference still wrong

The original failure was weather time: the agent read Mannheim weather at
`16:00`, saw `cloudy`, and routed to Mannheim. That is fixed.

Latest full run:
- The agent checked Mannheim weather at `19:23`.
- It correctly observed rain/hail and selected the Cologne branch.
- It fetched Cologne routes and called `set_new_navigation(...)` with
  `rll_ess_col_178709`, the fastest route.
- The task preference says the user wants the shortest route, not fastest.
  Cologne's shortest route was `rll_ess_col_645120`, so final action failed.

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

Remaining fix direction:
- When the user explicitly states a route selection preference, such as
  "shortest route, not fastest", that preference must override the default
  fastest-route heuristic on whichever conditional branch is actually executed.
- This should be handled as general priority ordering: explicit user route
  preference > default fastest route.

### `base_98`: EV charging plan loses the charging stop in navigation — ACTIVE

Latest full 3-trial run: failed `0/3`. The agent now uses calendar, charging,
route, POI search, and charging-time tools, but it sets direct navigation to
the meeting location instead of a two-leg navigation via the charging stop.

Earlier failure shape: it calculated time using Fastned
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
1. Explicit route preference on conditional navigation branches (`base_96`):
   "shortest, not fastest" must survive branch selection.
2. Later-segment EV range/charging-provider flow for `base_86`.
3. Route-based charger search after long route edits (`base_88`), while keeping
   the unrelated-segment fastest-route policy issue classified as evaluator-side.
4. Track `base_48` and `base_92` as policy-evaluator contradiction candidates,
   not as hard agent bugs, unless Q&A or repeated hidden-like evidence says
   otherwise.
