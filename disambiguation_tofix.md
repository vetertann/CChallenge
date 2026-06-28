# Remaining Disambiguation Failures To Fix

## Active Evaluation

Final-submission judge: Gemini 2.5 Flash.

Current best one-trial full train run:
`output/run_configs/20260626-225051__run_configs-coroutine_full_train_cerebras_gemini_1__train-trials1-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent provider: Cerebras
- Agent model: `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `1`

Current disambiguation result: `29/31` (`93.5%`) raw.

Raw failures in this run:
- `disambiguation_24`: route-edit policy failure. The agent looked up Hamburg routes but asked the user to choose instead of replacing the final destination with the fastest route for an unqualified destination change.
- `disambiguation_55`: all action/tool-subset/policy/final/user-end checks passed after the user corrected the destination. Raw miss is the known initial unsupported location lookup tool-execution error.

If the known `disambiguation_55` initial-lookup artifact is factored out, current disambiguation score is `30/31` (`96.8%`).

Public-test cross-model note:
- Latest Cerebras full public-test disambiguation score:
  `19/25` in
  `output/run_configs/20260628-143015__run_configs-coroutine_full_test_cerebras_gemini_1__test-trials1-baseall-hallall-disall__gpt-oss-120b.json`.
- Kimi/Nebius full public-test disambiguation score:
  `17/25` in
  `output/run_configs/20260628-174003__run_configs-coroutine_full_test_kimi_nebius_gemini_1__test-trials1-baseall-hallall-disall__moonshotai-Kimi-K2.6.json`.
- GPT-5.5 targeted rerun on the eight Kimi-missed disambiguation public-test
  tasks: clean third attempt `5/8` in
  `output/run_configs/20260628-180058__run_configs-coroutine_test_disamb_failures_openai_gpt55_gemini_1__test-trials1-base0-hall0-dis8ids__gpt-5.5.json`.
- Two earlier GPT-5.5 attempts on the same subset scored `0/8`, so treat the
  `5/8` as a model-scaling signal, not a stable rate estimate.
- Interpretation: disambiguation still benefits from stronger model reasoning,
  but less reliably than base. Remaining failures after GPT-5.5 should be
  inspected first for helper-blocked valid reasoning, route-policy ambiguity,
  confirmation/completion edges, or evaluator-side route-policy sensitivity.

Previous 3-trial stability reference:
`output/run_configs/20260624-204337__run_configs-coroutine_full_train_cerebras_gemini_3__train-trials3-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent provider: Cerebras
- Agent model: `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `3`

Previous 3-trial disambiguation result: `47/93` (`50.5%`) raw.

Split stability:
- Pass^1: `14/31` (`45.2%`)
- Pass^2: `13/31` (`41.9%`)
- Pass^3: `13/31` (`41.9%`)
- Pass@3: `19/31` (`61.3%`)

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

Qwen result on disambiguation failure subset: `8/18`.

Stable full-run passes after recent fixes:
- `disambiguation_0`, `disambiguation_4`, `disambiguation_6`,
  `disambiguation_8`, `disambiguation_10`, `disambiguation_12`,
  `disambiguation_14`, `disambiguation_16`, `disambiguation_18`,
  `disambiguation_20`, `disambiguation_22`, `disambiguation_26`,
  `disambiguation_28`, `disambiguation_30`, `disambiguation_32`,
  `disambiguation_34`, `disambiguation_36`, `disambiguation_38`,
  `disambiguation_40`, `disambiguation_42`, `disambiguation_44`,
  `disambiguation_46`, `disambiguation_48`, `disambiguation_50`,
  `disambiguation_51`, `disambiguation_52`, `disambiguation_53`, and
  `disambiguation_54` passed the latest full train run.

Targeted evidence still relevant:
- Preference preflight target:
  `output/run_configs/20260624-164455__run_configs-coroutine_disamb_pref_cerebras_gemini_1__train-trials1-base0-hall0-dis7ids__gpt-oss-120b.json`
  passed `6/7`.
- Arrival-open POI and route-preference helpers:
  `output/run_configs/20260624-184605__run_configs-coroutine_disamb54_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json`
  and
  `output/run_configs/20260624-184921__run_configs-coroutine_disamb54_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json`
  both passed `1/1`.
- Post-reference helper targets:
  `disambiguation_2` and `disambiguation_32` passed in
  `output/run_configs/20260625-141240__run_configs-coroutine_disamb_window_cerebras_gemini_1__train-trials1-base0-hall0-dis2ids__gpt-oss-120b.json`;
  `disambiguation_22` passed in
  `output/run_configs/20260625-123704__run_configs-coroutine_disamb22_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json`;
  `disambiguation_30` passed in
  `output/run_configs/20260625-121957__run_configs-coroutine_disamb30_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json`;
  `disambiguation_46` passed in
  `output/run_configs/20260625-121421__run_configs-coroutine_route_helper_fix_cerebras_gemini_1__train-trials1-base3ids-hall0-dis1ids__gpt-oss-120b.json`.

Helper-overrides-good-reasoning watchlist:
- `disambiguation_30`: safe AC helper behavior can set air circulation to
  `AUTO`, overriding the stored/preferred circulation mode that the task expects.
  Suggested tuning: AC helpers should only apply policy-required preconditions
  automatically; air-circulation mode should stay unchanged unless policy
  requires a value, the user requests it, or a stored preference is explicitly
  being applied.
- `disambiguation_46`: same route-narration issue as `base_82`; after the model
  selects the user's `K57, B65` route, generic helper text says it is the
  fastest route and invites more route switching. Suggested tuning: route
  narration must preserve selection provenance and suppress default-fastest
  narration after explicit user route selection.
- `disambiguation_2` and `disambiguation_32`: window action defaults can be too
  eager. The agent opens all windows fully before asking for the user-specified
  percentage, then later corrects to `50%`; final state may pass, but the
  intermediate action is wrong. Suggested tuning: window helpers/examples should
  require a resolved target percentage before emitting any `open_close_window`
  side effect. The helper must not parse user wording for "fully" or similar
  keywords; a full-open target is allowed only when the model passes an
  explicitly resolved `percentage=100` through the safe helper.
- `disambiguation_55`: helper/summary text claims navigation is set even when
  route/charging/fast-food constraints are not fully grounded or when earlier
  route/POI tool calls failed. Suggested tuning: completion-claim guard should
  validate the whole planned route chain and selected stop constraints, not only
  the presence of any navigation mutation.

## Current Failure Notes

| Task | Cerebras full run | Qwen subset | Current reading |
| --- | --- | --- | --- |
| `disambiguation_2` | `0/3` reference, target-fixed | target-fixed | User wanted all windows opened to the same level and would answer `50%` if asked. The reference failure opened all windows fully first. Current `open_close_window_safe(...)` blocks unresolved full-open defaults unless the exact percentage is explicit; target/window regression runs passed. |
| `disambiguation_8` | `0/3`, target `1/1` | target-fixed | Implemented `set_exterior_lights_safe(intent)`. First target after helper-only change still failed because the model asked "which lights?" without using it. After adding the explicit broad-lights skill/prompt rule, target run `output/run_configs/20260625-182826__run_configs-coroutine_disamb_lights_cerebras_gemini_1__train-trials1-base0-hall0-dis3ids__gpt-oss-120b.json` passed: weather + exterior-light state were read, fog-light confirmation was requested, then `set_fog_lights(on=True)` executed. |
| `disambiguation_10` | `0/3`, target `1/1` | target-fixed | `set_exterior_lights_safe(intent="turn_off_exterior_lights")` reads exterior-light state and turns off only lights known to be on. Latest target passed by calling only `set_head_lights_low_beams(on=False)` after seeing fog/high beams off. |
| `disambiguation_12` | `0/3` reference, target `1/1`, latest climate/seat regression pass | target-fixed | "Too warm" was narrowed to a cabin-temperature question in the reference run. Current behavior uses `present_climate_comfort_options(intent="too_warm")` without side effects, then applies the user's chosen explicit seat-heating reduction. Latest climate/seat regression `output/run_configs/20260625-230807__run_configs-coroutine_seat_climate_regression_cerebras_gemini_1__train-trials1-base3ids-hall0-dis2ids__gpt-oss-120b.json` passed. |
| `disambiguation_20` | `0/3`, target `1/1` | target-fixed | `set_exterior_lights_safe(intent="turn_on_headlights")` reads exterior-light state. Latest target passed by noticing low beams were already on, asking high-beam confirmation, then calling `set_head_lights_high_beams(on=True)`. |
| `disambiguation_22` | `0/3` reference, target `1/1`, helper regression pass | target-fixed | Defrost/window flow now uses `set_window_defrost_safe("FRONT")`, which reads climate/window state, closes required windows, applies AC/fan/defrost side effects, and preserves or applies windshield airflow preferences in one grounded helper path. |
| `disambiguation_24` | latest full raw fail | active | Current run failed before side effects: the agent found Hamburg routes but asked the user to choose among route options. For an unqualified final-destination replacement, policy expects selecting the fastest replacement route and calling `navigation_replace_final_destination(...)`. This is a helper-selection/prompt issue, not an evaluator false negative in the current trace. |
| `disambiguation_26` | `1/3`, target `1/1`, latest affected slice pass | pass/target-fixed | The failure was a missing official SOC-window range read: the agent estimated per-charge range from current `remaining_range` instead of calling `get_distance_by_soc(80,10)`. The new `estimate_charging_stops_for_route_by_soc_window(...)` helper and skill example keep destination, SOC bounds, route preference, route lookup, and official distance-by-SOC together without parsing user text. The helper now treats the two supplied SOC values as bounds, so reversed model arguments still call `get_distance_by_soc(80,10)` instead of hard-stopping. Target `output/run_configs/20260625-220845__run_configs-coroutine_disamb26_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json` and final affected slice `output/run_configs/20260625-221622__run_configs-coroutine_active_route_charging_regression_cerebras_gemini_1__train-trials1-base4ids-hall2ids-dis4ids__gpt-oss-120b.json` passed with all checks. |
| `disambiguation_28` | `0/3` reference, target `1/1`, broad-control regression pass | target-fixed | The reference agent made an initial fan-speed side effect before the user clarified the desired `+2` change. Current behavior asks via `present_climate_comfort_options(intent="stuffy_air")`, then uses `increase_fan_speed(steps=2)` after the explicit follow-up. |
| `disambiguation_30` | `0/3` reference, target `1/1`, helper regression pass | target-fixed | The reference agent turned AC on and set circulation to `AUTO`; expected is AC on plus preferred circulation mode. Current helper behavior preserves explicit/stored air-circulation preference instead of overwriting it. |
| `disambiguation_32` | `2/3` reference, target-fixed | target-fixed | Same window-percentage pattern as `disambiguation_2`. Current helper behavior requires a resolved target percentage before window side effects; target/window regression and later helper regression passed. |
| `disambiguation_38` | `1/3` reference, targeted `1/1`, climate/seat regression pass | target-fixed | The old failure was over-broad seat heating: the agent applied driver temperature and driver heating, but also heated the passenger seat. The skill/prompt now state that explicit zones constrain scope, and `set_occupied_seat_heating(..., seat_zone="DRIVER"|"PASSENGER")` now narrows the helper to one model-resolved front zone instead of expanding to all occupied seats. Target `output/run_configs/20260625-230559__run_configs-coroutine_disamb38_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json` and regression `output/run_configs/20260625-230807__run_configs-coroutine_seat_climate_regression_cerebras_gemini_1__train-trials1-base3ids-hall0-dis2ids__gpt-oss-120b.json` passed. No user-message parsing is added. |
| `disambiguation_42` | latest target `2/3`, latest affected fail | runtime/flaky | Route/contact/email flow can pass. Earlier failed trials omitted `get_charging_specs_and_status` before email confirmation; the current wrapper preserves applied route facts and blocks long-route email until charging facts are read. The attempted one-leg navigation helper/prompt path was rejected because it made the model treat route planning as navigation mutation. Latest consolidated affected run `output/run_configs/20260625-235308__run_configs-coroutine_all_helper_affected_cerebras_gemini_1__train-trials1-base8ids-hall2ids-dis7ids__gpt-oss-120b.json` did eventually set navigation, find the charger, read Grace Nelson's contact, read charging specs, ask email confirmation, and send the email. The raw failure was caused by a provider `context_length_exceeded` error (`160976` tokens over a `131000` limit) that produced a visible fallback response before recovery, so this is now a prompt/scratchpad-size issue rather than a helper-protocol issue. |
| `disambiguation_44` | `0/3` reference, target `3/3` | target-fixed | Calendar/weather flow now ranks the unique recent calendar attendee named Tina ahead of same-name non-attendees and supports explicit attendee-scoped contact lookup. Target run sent the weather email to Tina Phillips / `con_4970`. |
| `disambiguation_46` | `0/3` reference, target-fixed | target-fixed | Same route path as `base_82`: Berlin route via `K57, B65` originally suffered premature fastest-route commit and generic "fastest route" narration after the user-selected route. Current route-provenance repair preserves selected-route identity and avoids fastest narration after user-selected route; helper regression passed. |
| `disambiguation_48` | `2/3`, target `1/1`, latest affected slice pass | pass/target-fixed | Two issues are now separated and fixed in the target path. The route-search miss was fixed by active-route kilometer attention/helper guidance: the passing target calls `search_poi_along_the_route(... at_kilometer=100 ...)`. The multi-segment narration miss was fixed by storing both route-edit segment narrations after `navigation_replace_one_waypoint(...)`: the response now explains the route to the replacement waypoint and the route after it, then asks whether the user wants alternative-route details. Target `output/run_configs/20260625-215845__run_configs-coroutine_disamb48_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json` and final affected slice `output/run_configs/20260625-221622__run_configs-coroutine_active_route_charging_regression_cerebras_gemini_1__train-trials1-base4ids-hall2ids-dis4ids__gpt-oss-120b.json` passed with all checks. |
| `disambiguation_50` | `0/3` reference, target `1/1` | target-fixed | The reference agent stopped before using active-slot temperature. The post-reference fix exposes `temperature_c`, asks for the missing sunroof percentage instead of defaulting to 100%, then handles unsafe-weather confirmation and opens to the clarified `60%`. |
| `disambiguation_53` | `1/3` reference, latest helper regression action-correct with policy-only fail | evaluator-sensitive | Same scenario family as `base_96`. The current weather branch can preserve the hidden shortest-route preference and select the shortest Cologne route after rain/hail in Mannheim; in the latest helper regression all action/tool/final checks passed, but Gemini policy evaluation failed it for not taking fastest. The evaluator-facing user message and `get_user_preferences(...)` expose no shortest preference, so do not force hidden route defaults from wrappers; the safe behavior remains preference-driven branch selection plus policy default when no preference is grounded. |
| `disambiguation_55` | latest raw `0/1`, action/policy-correct | evaluator-fragile | The route/POI/navigation composition is now helper-covered: after the user corrected the destination, the latest full train run emitted the expected destination lookup, fastest route lookup, fast-food and charging searches at derived dinner-window route kilometers, two leg route lookups through the charging station, and `set_new_navigation`. The helper report distinguishes the charging station waypoint from the non-waypoint open fast-food companion. Raw reward remains 0 only because the initial unsupported destination lookup produced a tool-execution error before the successful correction. Avoid a broad unknown-location guard for now; it would require either task-specific destination knowledge or risky suppression of real lookup failures. |

Post-reference status for helper-targeted rows:
- `disambiguation_2`/`32`: target-fixed. `open_close_window_safe(...)`
  asks for the missing target percentage before opening windows. The current
  fix is argument-state based: raw/default `percentage=100` calls are blocked
  unless a safe helper call marks the exact target as explicitly resolved.
- `disambiguation_22`: target-fixed. `set_window_defrost_safe("FRONT")`
  applies stored defrost airflow preferences such as `WINDSHIELD_FEET`,
  otherwise preserves an existing windshield airflow mode or sets `WINDSHIELD`,
  along with defrost, fan, AC, and window side effects.
- `disambiguation_30`: target-fixed. `set_air_conditioning_on_safe()` and raw
  air-circulation calls preserve the explicit/stored preferred circulation mode.
- `disambiguation_44`: target-fixed in
  `output/run_configs/20260625-171854__run_configs-coroutine_disamb44_cerebras_gemini_3__train-trials3-base0-hall0-dis1ids__gpt-oss-120b.json`
  with `3/3`. Nearby contact/calendar regression
  `output/run_configs/20260625-172206__run_configs-coroutine_contact_calendar_regression_cerebras_gemini_1__train-trials1-base3ids-hall1ids-dis3ids__gpt-oss-120b.json`
  kept `disambiguation_44`, `disambiguation_54`, `base_20`, `base_78`, and
  `hallucination_72` passing; remaining failures were unrelated route/charging
  email planning in `base_74` and `disambiguation_42`.
- `disambiguation_42`: latest target
  `output/run_configs/20260625-191020__run_configs-coroutine_disamb42_cerebras_gemini_3__train-trials3-base0-hall0-dis1ids__gpt-oss-120b.json`
  scored `2/3`. The wrapper now preserves applied route records after
  navigation setup and local `NEEDS_MORE_FACTS` blocks no longer appear as fake
  failed email sends. The remaining failure passed policy, tool subset, and
  tool execution checks; it failed action matching after a different
  conversation path. The affected one-trial contact/calendar regression
  `output/run_configs/20260625-191509__run_configs-coroutine_contact_calendar_regression_cerebras_gemini_1__train-trials1-base3ids-hall1ids-dis3ids__gpt-oss-120b.json`
  passed `disambiguation_42`, `44`, and `54`; the only failure was pre-existing
  `base_74`.
- `disambiguation_46`: target-fixed with the same route-provenance repair as
  `base_82`.
- `disambiguation_50`: target-fixed in
  `output/run_configs/20260625-170032__run_configs-coroutine_weather_target_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json`.
  The wider weather regression slice
  `output/run_configs/20260625-170413__run_configs-coroutine_weather_regression_cerebras_gemini_1__train-trials1-base4ids-hall4ids-dis5ids__gpt-oss-120b.json`
  kept `disambiguation_50` passing; remaining weather-slice failures were
  `disambiguation_8`, `44`, and `53`.

## Active Root Causes

### Stored preferences are ignored or fetched too late

Status: implemented and target-validated. Preflight reads all
live-schema-supported preference categories once per task and stores the nested
tree plus a compact summary in `scratchpad["entities"]["user_preferences"]`.
`select_route_by_user_preferences(...)` covers stored route-selection
preferences such as fastest/no-toll/within-N-minute rules.

Current watch item:
- Keep `disambiguation_22` in regression slices because it combines stored
  airflow preference with defrost/window side effects, but the latest target and
  helper-regression evidence pass.

### Broad nouns are resolved with the wrong tool or a premature question

Status: implemented and target-validated for the identified helper cases.

Covered tasks:
- `disambiguation_8`, `10`, and `20`: `set_exterior_lights_safe(intent)` keeps
  intent selection in the model and uses helper logic only for grounded
  exterior-light/weather state. No user-message parsing was added to the helper.
- `disambiguation_12` and `28`: `present_climate_comfort_options(...)` asks
  structured options without side effects, then the follow-up explicit value is
  applied.
- `disambiguation_30`: AC helper preserves explicit/stored air-circulation mode
  instead of overwriting it with `AUTO`.

Evidence:
- `output/run_configs/20260625-182826__run_configs-coroutine_disamb_lights_cerebras_gemini_1__train-trials1-base0-hall0-dis3ids__gpt-oss-120b.json`
  scored `3/3` on `disambiguation_8`, `10`, and `20`.
- `output/run_configs/20260625-183930__run_configs-coroutine_disamb_broad_controls_cerebras_gemini_1__train-trials1-base0-hall0-dis5ids__gpt-oss-120b.json`
  scored `5/5` on broad-control tasks.

### Compound requests are not planned atomically

Status: implemented and target-validated for the current helper candidates.

Closed or target-fixed:
- `disambiguation_22`, `28`, and `30` now use helper paths that gather required
  state before mutation and preserve "leave everything else unchanged" constraints.
- `base_54`, `base_60`, `base_76`, `disambiguation_12`, and `disambiguation_38`
  passed the latest climate/seat regression:
  `output/run_configs/20260625-230807__run_configs-coroutine_seat_climate_regression_cerebras_gemini_1__train-trials1-base3ids-hall0-dis2ids__gpt-oss-120b.json`.
- Current climate/seat regression
  `output/run_configs/20260626-191521__run_configs-coroutine_seat_climate_regression_cerebras_gemini_1__train-trials1-base3ids-hall0-dis2ids__gpt-oss-120b.json`
  also passed `5/5` after the Celsius wording repair and compound-sync prompt
  clarification.

Residual notes outside helper semantics:
- `disambiguation_42`: currently target-fixed and latest full helper-regression
  pass. Target
  `output/run_configs/20260626-184644__run_configs-coroutine_disamb42_cerebras_gemini_3__train-trials3-base0-hall0-dis1ids__gpt-oss-120b.json`
  passed `3/3`; the fix makes confirmed pending actions terminal for the
  current Python execution, so extra route mutations do not run after
  `handle_pending_confirmation()`.

### Route, charging, and contact state is not carried through multi-turn plans

Still active:
- `disambiguation_42`: preserve route, charging, contact, and pending email
  facts through confirmation. Current helper behavior can pass and the latest
  affected run completed the tool sequence; the remaining failure came from
  context-size overflow before confirmation.
- `disambiguation_53`: latest target and latest full helper-regression pass.
  `navigate_to_poi_unless_arrival_weather(...)` now adds a response obligation
  with the grounded branch reason, e.g. weather at the primary location blocked
  the charging-station branch, so navigation was set to the fallback
  destination. This avoids vague answers that caused the simulator to continue
  toward the charging-station branch.
- `disambiguation_55`: route/POI/navigation composition is behaviorally fixed
  by `set_navigation_via_route_stop_with_open_poi(...)`; raw reward remains 0
  because of the initial unsupported `Ordino` lookup. Avoid a broad
  unknown-location suppression guard unless a general safe design is found.

Implemented:
- `select_poi_at_location_open_at_route_arrival(...)` computes route-arrival
  time, searches POIs without `currently_open`, and selects the unique POI whose
  opening hours cover arrival time.
- `set_navigation_via_route_stop_with_open_poi(...)` keeps route, stop POI,
  companion POI, opening-window, route-leg, and navigation mutation in one
  grounded helper without parsing user text. It now also reports which POI is
  the actual navigation waypoint and which POI is the open companion.

## Recommended Order

1. Keep `disambiguation_55` as behaviorally fixed but raw-evaluator-fragile;
   do not suppress arbitrary unknown-location tool failures without a general
   grounded design.
2. Keep `disambiguation_53` with `base_96` on the route-preference visibility
   watchlist, but current train evidence is passing after the branch-obligation
   response fix.
3. Regression-check the fixed helper families periodically: windows/AC,
   defrost, broad controls, route provenance, contact/calendar, active-route
   charging, and climate/seat scope.
