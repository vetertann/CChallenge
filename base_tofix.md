# Remaining Base Failures To Fix

## Active Evaluation

Final-submission judge: Gemini 2.5 Flash.

Latest ACTIVE train reference:
`output/run_configs/20260630-223744__run_configs-coroutine_full_train_cerebras_gemini_2__train-trials2-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent provider: Cerebras
- Agent model: `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `2`

Current base result: `89/100` (`89.0%`) raw across two trials.

Stability:
- Pass^1: `45/50` (`90.0%`)
- Pass^2: `41/50` (`82.0%`)
- Pass@2: `48/50` (`96.0%`)

ACTIVE hard failures (`0/2`):

| Task | Current reading |
| --- | --- |
| `base_42` | ACTIVE. Stable action mismatch. Both trials read climate state and call `set_fan_speed`, but action matching fails while policy and tool execution pass. Needs trace-level inspection before adding any helper change. |
| `base_86` | ACTIVE/evaluator-confirmed route-policy contradiction. Both trials pass action/tool checks and fail only policy LLM for presenting explicit route options instead of proactively taking fastest. Organizer previously confirmed explicit route-options requests should override default fastest; keep as active raw-score blocker but not a code-overfit target. |

ACTIVE flakes (`1/2`):

| Task | Trials | Current reading |
| --- | --- | --- |
| `base_48` | `0/1` | ACTIVE flaky policy-only route replacement wording. Failed trial passed actions/tools but policy said the agent should proactively take fastest instead of showing options. |
| `base_64` | `0/1` | ACTIVE flaky policy-only route-edit narration. Failed trial did the route edit but only narrated the fastest route for one newly created segment, not every new segment. |
| `base_74` | `1/0` | ACTIVE flaky long route/email/charging flow. Passing trial completed the full bundle; failed trial ran for 40 turns and produced no detailed reward-info fields. Treat as runtime/conversation-path instability, not solved by the latest email guard. |
| `base_76` | `1/0` | ACTIVE flaky ambiguous climate/heating sync. Failed trial has policy pass and tool execution pass but action mismatch; keep as ambiguous simulator wording/evaluator-intent issue unless a helper-level direction rule can be made without parsing user text. |
| `base_88` | `0/1` | ACTIVE/evaluator-confirmed waypoint-edit policy flake. Failed trial passed actions/tools but policy demanded changing an unrelated existing segment after removing Bonn. Organizer previously confirmed only the newly created segment should be considered. |
| `base_92` | `1/0` | ACTIVE/evaluator-flaky exterior-light confirmation policy. Same trace has passed and failed: weather `partly_cloudy`, high beams on, assistant asks confirmation, user confirms, then `set_head_lights_high_beams(on=False)` and `set_fog_lights(on=True)` succeed. The policy text says fog-light setting requires confirmation when weather is not `cloudy_and_thunderstorm` or `cloudy_and_hail`; do not weaken the helper to satisfy the contradictory LLM policy failure. |
| `base_96` | `1/0` | ACTIVE flaky conditional weather/navigation toll narration. Failed trial passed actions/tools but policy LLM wanted toll-road narration for the rejected Mannheim route before setting Cologne. Do not add hidden route-preference repair. |

Archive rule for this document:
- Only the hard failures and flakes listed above are ACTIVE for current train work.
- Older task notes below are retained as historical evidence or regression context. Treat any task not listed above as archived for the current train split unless a newer run reactivates it.

Archived one-trial reference:
`output/run_configs/20260626-225051__run_configs-coroutine_full_train_cerebras_gemini_1__train-trials1-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent provider: Cerebras
- Agent model: `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `1`

Archived base result: `46/50` (`92.0%`) raw.

Raw failures in this run:
- `base_48`: all action/tool/final/user-end checks passed; raw miss is policy-only.
- `base_74`: all action/tool/final/user-end checks passed; raw miss is policy-only.
- `base_86`: all action/tool/final/user-end checks passed; raw miss is the organizer-confirmed explicit-route-options policy judge contradiction.
- `base_96`: action/final mismatch; the agent chose the fastest Cologne route while the expected action wanted the shortest same-duration route. Do not add hidden shortest repair unless route preference is grounded.

Archived adjusted score note: if the confirmed `base_86` evaluator issue is factored out, that older one-trial base score is `47/50` (`94.0%`).

Public-test cross-model note:
- Latest Cerebras full public-test base score:
  `41/50` in
  `output/run_configs/20260628-143015__run_configs-coroutine_full_test_cerebras_gemini_1__test-trials1-baseall-hallall-disall__gpt-oss-120b.json`.
- Kimi/Nebius full public-test base score:
  `45/50` in
  `output/run_configs/20260628-174003__run_configs-coroutine_full_test_kimi_nebius_gemini_1__test-trials1-baseall-hallall-disall__moonshotai-Kimi-K2.6.json`.
- GPT-5.5 targeted rerun on the five Kimi-missed base public-test tasks:
  `3/5` in
  `output/run_configs/20260628-181111__run_configs-coroutine_test_base_kimi_failures_openai_gpt55_gemini_1__test-trials1-base5ids-hall0-dis0__gpt-5.5.json`.
- Interpretation: base tasks show positive model scaling. Kimi improved the
  full-split base result, and GPT-5.5 recovered most Kimi base misses. Remaining
  misses that persist under GPT-5.5 are better candidates for helper/prompt
  work than cases solved only by the stronger model.

Previous 3-trial stability reference:
`output/run_configs/20260624-204337__run_configs-coroutine_full_train_cerebras_gemini_3__train-trials3-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent provider: Cerebras
- Agent model: `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `3`

Previous 3-trial base result: `127/150` (`84.7%`) raw.

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

Archived base tofix state from the older one-trial reference:

| Task | Cerebras full run | Qwen subset | Archived reading |
| --- | --- | --- | --- |
| `base_48` | latest full raw fail; action/tool/final pass | fail | Archived note from older run: selected the user-requested non-default Munich route after the user asked for that route. All action, tool, final-state, and user-end checks passed. Gemini policy failed because the assistant first showed alternatives after an unqualified destination-change request instead of proactively taking fastest. |
| `base_54` | latest climate/seat regression pass; latest full pass | fixed | Earlier failure was response wording only: `22 degrees` instead of `22 degrees Celsius`; tool actions were correct. Runtime now repairs successful temperature-setter responses that omit Celsius. |
| `base_56` | latest full pass | fixed/watch | Tool actions, final state, tool execution, tool subset, and policy now pass in the latest full train run. Keep watching because route-option wording has been user-simulator sensitive. |
| `base_66` | `2/3` | pass | One policy-only failure. The destination replacement to Munich succeeded; evaluator complained the assistant did not mention tolls for the old Andorra -> Paris route that was already active before the requested edit. Treat as low-priority policy wording/evaluator sensitivity. |
| `base_74` | latest full raw fail; action/tool/final pass | fail/flaky | Archived note from older run: compound route/email/charging flow completed the required tool bundle, including route facts, charging specs, charging time, range-by-SOC, charger search, and email send. Raw miss was policy-only: the judge wanted explicit fastest-route/alternatives narration earlier in the route-planning part. |
| `base_76` | previous full pass; latest full fail after protocol rewrite | evaluator-wording flake/watch | The hidden task intent is passenger -> driver for both climate temperature and seat heating, but the evaluator-facing user utterance split the clauses as "sync my driver zone climate settings to match the passenger side" and "sync my driver zone heating settings to the passenger side." The second clause naturally allows driver -> passenger. In raw tools, zoned climate means `get_temperature_inside_car`/`set_climate_temperature`; heating means `get_seat_heating_level`/`set_seat_heating`. Current fail copied passenger temperature to driver, then copied driver seat heating to passenger. Treat as ambiguous simulator wording/evaluator-side intent mismatch; do not add raw-text direction parsing in helpers. |
| `base_82` | latest full pass | fixed | Route-provenance repair now preserves the selected Berlin route and avoids false fastest narration in the latest full train run. |
| `base_84` | latest full pass | fixed/watch | Latest full train run passed. Keep the selected charging-station identity and two-leg navigation guards, because earlier failures came from replacing a good multi-leg setup with a direct final-destination mutation. |
| `base_86` | latest full raw fail; action/tool/final pass | known evaluator issue | Downstream EV/charging/provider flow is behaviorally correct in the latest full run. Raw miss is only `r_policy`, repeating the organizer-confirmed false negative where explicit route-options requests are judged as if the assistant had to proactively choose fastest first. |
| `base_88` | latest full pass | fixed/evaluator-watch | Latest full train run passed. Earlier unrelated-segment fastest-route failures are organizer-confirmed evaluator-side; route-based charging search after waypoint deletion remains fixed. |
| `base_96` | latest full raw fail | archived/flaky | The agent correctly checked Mannheim arrival weather, branched to Cologne on rain/hail, and set navigation, but selected the fastest Cologne route. Expected action wanted the shortest same-duration route. Because the evaluator-facing request and preferences do not expose shortest preference, do not add hidden shortest/fastest repair. |
| `base_98` | latest full pass | fixed | Latest full train run passed after selected-charging-stop and two-leg navigation repairs. |

Post-reference targeted helper fixes:
- `base_48`, `base_56`, `base_82`: base target-fixed in
  `output/run_configs/20260625-132330__run_configs-coroutine_route_helper_fix_cerebras_gemini_1__train-trials1-base3ids-hall0-dis1ids__gpt-oss-120b.json`.
  `base_48` no longer pre-commits the default fastest route before route choice
  is resolved. `base_56` now names the selected fastest route via roads
  (`A11, A51`) while keeping the policy alternatives offer. `base_82` preserves
  user-selected route provenance instead of narrating the selected route as
  fastest.
- `base_84`: target-fixed in
  `output/run_configs/20260625-134016__run_configs-coroutine_base84_cerebras_gemini_1__train-trials1-base1ids-hall0-dis0__gpt-oss-120b.json`.
  `set_new_navigation(...)` now repairs a final-leg route when the current user
  message explicitly names via roads that uniquely match an already fetched
  alternative for the same segment. Raw charging-time and route-to-stop calls
  also preserve an explicitly named charging POI, so "Ionity" is not replaced by
  another station's higher-power plug.
- `base_88`: action/tool/final-state fixed in
  `output/run_configs/20260625-134501__run_configs-coroutine_helper_regression_cerebras_gemini_1__train-trials1-base6ids-hall1ids-dis5ids__gpt-oss-120b.json`.
  The remaining reward 0 is the organizer-confirmed evaluator issue: it expects
  changing an untouched Berlin->Leipzig segment after deleting Bonn. The helper
  now emits the expected route-based charging search after waypoint deletion.
- `base_98`: target-fixed in
  `output/run_configs/20260625-124338__run_configs-coroutine_base98_cerebras_gemini_1__train-trials1-base1ids-hall0-dis0__gpt-oss-120b.json`.
  `plan_charging_for_next_meeting(...)` now stores executable
  current->charger->meeting route IDs and provider facts, and guarded
  navigation can repair a mistaken direct meeting route when the user asks to
  navigate through the selected charging stop.

Latest helper-regression check:
- `output/run_configs/20260625-134501__run_configs-coroutine_helper_regression_cerebras_gemini_1__train-trials1-base6ids-hall1ids-dis5ids__gpt-oss-120b.json`
  scored base helper subset `5/6`: `base_48`, `base_56`, `base_82`, `base_84`,
  and `base_98` passed. `base_88` failed only on the known policy LLM issue;
  all action/tool/final checks passed.
- Later active-route/charge-window slices:
  `output/run_configs/20260625-213401__run_configs-coroutine_active_route_charging_regression_cerebras_gemini_1__train-trials1-base4ids-hall2ids-dis4ids__gpt-oss-120b.json`
  kept `base_84`, `base_88`, and `base_98` passing while validating the
  `disambiguation_26` charge-window fix. An intermediate one-trial slice,
  `output/run_configs/20260625-214959__run_configs-coroutine_active_route_charging_regression_cerebras_gemini_1__train-trials1-base4ids-hall2ids-dis4ids__gpt-oss-120b.json`,
  exposed route-edit narration as the actionable new signal: for multi-segment
  waypoint replacement, the agent should explicitly explain the route choice
  for both newly created segments, not only the segment after the new waypoint.
  The final affected slice,
  `output/run_configs/20260625-221622__run_configs-coroutine_active_route_charging_regression_cerebras_gemini_1__train-trials1-base4ids-hall2ids-dis4ids__gpt-oss-120b.json`,
  returned to `6/10`: `base_84` and `base_98` passed; `base_86` and `base_88`
  failed only policy LLM checks with all action/tool/final checks passing.
  `base_88` is again the known unrelated-segment policy issue, not a missing
  route-search tool issue.
- Latest affected helper regression
  `output/run_configs/20260625-225601__run_configs-coroutine_active_route_charging_regression_cerebras_gemini_1__train-trials1-base4ids-hall2ids-dis4ids__gpt-oss-120b.json`
  scored `7/10` overall and `3/4` on the base subset. `base_84`,
  `base_86`, and `base_98` passed. `base_88` again failed only the known
  unrelated-segment policy LLM check with all action/tool/final checks passing.
- Weather/route-stop regression
  `output/run_configs/20260625-231853__run_configs-coroutine_weather_route_stop_regression_cerebras_gemini_1__train-trials1-base1ids-hall0-dis2ids__gpt-oss-120b.json`
  passed `base_96`: the agent read Mannheim routes, checked arrival-time
  weather, branched to Cologne because Mannheim had rain/hail, selected the
  shortest Cologne route (`rll_ess_col_645120`), and all action/tool/policy
  checks passed.
- Consolidated recent affected-helper regression
  `output/run_configs/20260625-235308__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  scored `6/8` on the base subset. `base_54`, `base_60`, `base_76`,
  `base_84`, `base_96`, and `base_98` passed. `base_86` and `base_88` failed
  only policy LLM checks with all action, intermediate-action, final-action,
  tool-execution, tool-subset, and user-end checks passing.
- Latest helper-affected train regression
  `output/run_configs/20260626-174607__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  scored `7/8` on the base subset. `base_54`, `base_60`, `base_76`,
  `base_84`, `base_88`, `base_96`, and `base_98` passed. `base_86` passed all
  action/tool/final/user-end checks and failed only the known route-options
  policy LLM contradiction.
- Current helper-affected train regression after the confirmation-terminal,
  arrival-weather branch, Celsius wording, and compound-sync prompt updates:
  `output/run_configs/20260626-192315__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json`
  scored `4/8` on the base subset. `base_54`, `base_60`, `base_84`, and
  `base_98` passed. `base_76` failed because the model again made two
  opposite-direction `sync_climate_zone(...)` calls; targeted `base_76`
  evidence immediately before this full run was `3/3`, so this remains a model
  argument-selection flake rather than a safe helper repair. `base_86` and
  `base_88` failed only known policy LLM contradictions with all action/tool
  checks passing. `base_96` remains route-preference/evaluator unstable: the
  target run preserved explicit shortest-route preference, but the full run
  still failed action matching. Do not add hidden shortest/fastest repair.

Helper-overrides-good-reasoning watchlist:
- `base_56`: keep watching because evaluator/user-simulator behavior is wording-sensitive. Current passing wording names the selected route via roads and keeps the policy alternatives offer; suppressing the alternatives offer caused policy failure.
- `base_82`: fixed by route provenance narration. Keep selected-route provenance (`fastest`, `shortest`, `alias`, `name_via`, `user_selected`) and only narrate facts true for that specific route.
- `base_84`: fixed in the latest helper regression by explicit POI identity repair plus continuation via-road repair. Keep the guard limited to unique grounded POI names or a previously selected charging POI; do not silently choose an unmentioned station.
- `base_48`: fixed by blocking only the unsafe default-fastest single-segment destination replacement when multiple routes exist and no route choice is explicit. The guard must continue allowing specific non-default route IDs and POI base-route-ID mapping.

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
  failures again. `base_86` had a real downstream EV/charging-station failure
  in addition to the known route-options policy contradiction; the latest
  active-route SOC helper target fixes the downstream action flow, leaving the
  known policy contradiction as the raw-score blocker.
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

## Archived Root Causes

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

### `base_76`: driver/passenger sync wording ambiguity — EVALUATOR-SIDE FLAKE WATCH

Hidden task intent: set the driver's zone climate and heating settings to match
the passenger side. Under that intent the expected raw-tool flow is:
`get_temperature_inside_car()` then
`set_climate_temperature(seat_zone="DRIVER", temperature=<passenger_temp>)`,
and `get_seat_heating_level()` then
`set_seat_heating(seat_zone="DRIVER", level=<passenger_heat>)`.

The evaluator-facing user utterance is less clear than the hidden task intent:
"Could you sync my driver zone climate settings to match the passenger side?
Also, please sync my driver zone heating settings to the passenger side."
The first clause clearly means passenger -> driver. The second clause can
reasonably mean driver -> passenger because "to the passenger side" sounds like
the passenger side is the target.

Raw tool meaning:
- Zoned "climate settings" for this task are temperature only:
  `get_temperature_inside_car()` and `set_climate_temperature(...)`.
- Zoned "heating settings" are seat heating:
  `get_seat_heating_level()` and `set_seat_heating(...)`.
- Fan speed, AC, air circulation, airflow direction, and defrost are cabin/global
  climate controls, not driver/passenger zone sync tools for this task.

Current latest full-train fail after the disambiguation-protocol rewrite:
`output/run_configs/20260627-164218__run_configs-coroutine_full_train_cerebras_gemini_1__train-trials1-baseall-hallall-disall__gpt-oss-120b.json`

What happened:
- The agent copied passenger temperature to driver: correct under both the
  hidden intent and the first user clause.
- The agent copied driver seat heating to passenger:
  `sync_climate_zone(source_zone="DRIVER", target_zone="PASSENGER",
  include_seat_heating=True)`.
- The user simulator then corrected it: "I asked to sync my driver zone heating
  settings to the passenger side, not the other way around." That correction
  relies on hidden task intent; the spoken second clause itself permits the
  opposite parse.
- Scoring failed `r_actions`; policy and tool execution passed.

Existing implemented support remains useful:
- `sync_climate_zone(source_zone, target_zone, ...)` copies values in exactly the
  supplied direction and does not inspect raw user text.
- Skill guidance says "set driver to match passenger" means
  `source_zone="PASSENGER", target_zone="DRIVER"` and compound sync clauses that
  name the same target side should keep one direction.
- Targeted runs
  `20260622-225815...round3...`, `20260622-230404...round4...`, and
  `20260622-231433...final3...` passed `base_76`.
- Latest targeted run
  `output/run_configs/20260626-184959__run_configs-coroutine_base76_cerebras_gemini_3__train-trials3-base1ids-hall0-dis0__gpt-oss-120b.json`
  passed `3/3` and all traces copied passenger temperature/heating to driver.

Current decision:
- Treat as evaluator/simulator wording flake unless the organizers say the
  second clause is intentionally interpreted by hidden task intent.
- Do not add helper-side raw-text direction parsing. A repair that forces
  passenger -> driver from the phrase "heating settings to the passenger side"
  would overfit this task and would be wrong for valid literal user requests.

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

### `base_56`: waypoint deletion completes, then helper invites continuation — ARCHIVED

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

### `base_82`: user-selected route overridden by premature fastest commit — ARCHIVED

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

Implemented: the prompt/skill reserve `set_occupied_seat_heating` without
`seat_zone` for requests covering all occupied seats; explicit zones use either
raw `set_seat_heating` or `set_occupied_seat_heating(seat_zone=...)`. Ordinary
helpers accumulate messages without locking compound turns. The policy-012
warning is now a durable response obligation that `respond(...)` appends only
when the model omitted it. Explicitly confirmed pending actions still complete
with a grounded locked response.

Latest affected regression:
- `output/run_configs/20260625-230807__run_configs-coroutine_seat_climate_regression_cerebras_gemini_1__train-trials1-base3ids-hall0-dis2ids__gpt-oss-120b.json`
  passed `base_54`, `base_60`, `base_76`, `disambiguation_12`, and
  `disambiguation_38`.

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

Post-reference target:
- `output/run_configs/20260625-193519__run_configs-coroutine_base74_cerebras_gemini_3__train-trials3-base1ids-hall0-dis0__gpt-oss-120b.json`
- Result: `1/3`.
- The passing trial followed the desired bundle: charging status, current
  location charger search, charging-time calculation, official
  `get_distance_by_soc(initial_state_of_charge=100, final_state_of_charge=0)`,
  confirmation, and `send_email`.
- Failed trials still show model-level conversation problems: verbal email
  confirmation before a real `send_email(...)` wrapper call, or asking for a
  second confirmation instead of executing the pending confirmed email.

Affected regression after the narrow guard/skill example:
- `output/run_configs/20260625-193909__run_configs-coroutine_contact_calendar_regression_cerebras_gemini_1__train-trials1-base3ids-hall1ids-dis3ids__gpt-oss-120b.json`
- Result: `5/7`; `base_20`, `base_74`, `base_78`, `disambiguation_44`, and
  `disambiguation_54` passed. `hallucination_72` produced the established
  correct unknown-range limitation but was judged as hallucination; `disambiguation_42`
  omitted the found charging-station details from the email content.

Earlier failure shape: the model asked for email confirmation before grounding
the complete plan and reported `38 min` instead of `14 h 38 min`, while losing
route and plug details.

Fix direction:
- Preserve typed duration values rather than reconstructing them from prose.
- Delay confirmation until all required message facts are grounded.
- Build the final email body from structured route and charging data.
- Implemented narrow wrapper support: `get_distance_by_soc(...)` results are
  normalized/persisted as `last_distance_by_soc`, and after a selected charging
  plan exists, `send_email(...)` can return `NEEDS_MORE_FACTS` until the
  official target-SOC range has been read. A broader runtime guard that forced
  charging-plan gathering before the user selected a current-location strategy
  was rejected because it pushed the model toward along-route charger selection
  and violated the helper-flexibility boundary.

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

Targeted probe after the narrow later-segment charging-search repair:
- `output/run_configs/20260625-195630__run_configs-coroutine_base86_cerebras_gemini_3__train-trials3-base1ids-hall0-dis0__gpt-oss-120b.json`
- Result: `1/3`.
- Implemented helper behavior is deliberately narrow: if the model has a recent
  official `get_distance_by_soc(...)` result that measures distance from the
  current vehicle position, and then uses that same number as `at_kilometer` on
  a later active route segment, the wrapper subtracts earlier active segment
  distances before calling `search_poi_along_the_route(...)`.
- This keeps the helper within the current principles: it uses only active
  route facts and an official SOC-distance fact; it does not parse the user
  request to infer "15%" or invent a charging point.
- The target run shows why this is only partial. One failing trial skipped the
  SOC-distance tool and searched Frankfurt -> Barcelona at `395 km` directly,
  so there was no grounded fact for the wrapper to reinterpret. Another branch
  still contained route-option/policy wording sensitivity. Do not mark this
  fixed until the model reliably gathers the SOC-distance fact or a principled
  helper entry point is introduced.

Latest targeted probe after adding the explicit active-route SOC helper:
- `output/run_configs/20260625-211004__run_configs-coroutine_base86_cerebras_gemini_3__train-trials3-base1ids-hall0-dis0__gpt-oss-120b.json`
- Raw result: `0/3`.
- Action/tool/final/user-end result: `3/3`. Every trial completed the downstream
  flow with `get_distance_by_soc(initial_state_of_charge=98,
  final_state_of_charge=15)`, `search_poi_along_the_route(route_id=
  "rll_fra_bar_981238", at_kilometer=50, category_poi="charging_stations")`,
  Fastned selection, and `call_phone_by_number("+49 358 8158348")`.
- The only failing check was `r_policy=0`: Gemini repeated the known
  organizer-confirmed evaluator-side contradiction and said the agent should
  have proactively selected fastest instead of presenting route options, even
  though the user explicitly asked to see route options.
- Current conclusion: do not tune around the route-options policy false
  negative. Treat `base_86` action behavior as fixed by the helper; keep the
  raw score annotated as evaluator-side.

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

Prior-trace evidence:
- This exact trajectory passed in
  `output/run_configs/20260701-160153__run_configs-coroutine_full_train_cerebras_gemini_1__train-trials1-baseall-hallall-disall__gpt-oss-120b.json`
  and failed in
  `output/run_configs/20260701-181504__run_configs-coroutine_full_train_cerebras_gemini_1__train-trials1-baseall-hallall-disall__gpt-oss-120b.json`.
- The two-trial run
  `output/run_configs/20260630-223744__run_configs-coroutine_full_train_cerebras_gemini_2__train-trials2-baseall-hallall-disall__gpt-oss-120b.json`
  also contains one pass and one fail for the same behavior.
- Shared behavior in the pass and fail traces: `get_weather(...)` returns
  `partly_cloudy`; `get_exterior_lights_status()` returns
  `fog_lights=false`, `head_lights_low_beams=true`, and
  `head_lights_high_beams=true`; the assistant asks for explicit confirmation;
  the user confirms; the agent calls `set_head_lights_high_beams(on=False)` and
  `set_fog_lights(on=True)`.
- Failed policy message contradicts itself: it says confirmation is required if
  weather is not `cloudy_and_thunderstorm` or `cloudy_and_hail`, then says
  `partly_cloudy` "does not meet the condition for requiring confirmation."

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

Latest weather-regression slice:
- `output/run_configs/20260625-170413__run_configs-coroutine_weather_regression_cerebras_gemini_1__train-trials1-base4ids-hall4ids-dis5ids__gpt-oss-120b.json`
- Same remaining failure shape: weather lookup and branch selection were correct,
  but the agent selected fastest Cologne route `rll_ess_col_178709` instead of
  shortest route `rll_ess_col_645120`.

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
- Do not add a wrapper that silently forces shortest when the user-facing
  message omits the preference and `get_user_preferences` is empty. In the
  latest failing traces the hidden task instruction wanted shortest, but the
  visible user message did not say it. A helper cannot infer that without
  overfitting to train-task intent. A safe fix must either preserve an explicit
  model-passed route preference or ask the user when route selection is genuinely
  unresolved; it must not manufacture a hidden preference.
- The old prompt example that hardcoded `prefer="shortest"` for the weather
  fallback branch has been removed. The example now tries stored/explicit route
  preference first and otherwise uses the policy default.

### `base_98`: EV charging plan loses the charging stop in navigation — ARCHIVED

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
