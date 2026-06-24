# Remaining Disambiguation Failures To Fix

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

Result for disambiguation split: `47/93` (`50.5%`) raw.

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
  `disambiguation_14`, `disambiguation_16`, `disambiguation_18`,
  `disambiguation_24`, `disambiguation_34`, `disambiguation_36`,
  `disambiguation_40`, `disambiguation_51`, `disambiguation_52`, and
  `disambiguation_54` all passed `3/3`.

Targeted evidence still relevant:
- Preference preflight target:
  `output/run_configs/20260624-164455__run_configs-coroutine_disamb_pref_cerebras_gemini_1__train-trials1-base0-hall0-dis7ids__gpt-oss-120b.json`
  passed `6/7`.
- Arrival-open POI and route-preference helpers:
  `output/run_configs/20260624-184605__run_configs-coroutine_disamb54_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json`
  and
  `output/run_configs/20260624-184921__run_configs-coroutine_disamb54_cerebras_gemini_1__train-trials1-base0-hall0-dis1ids__gpt-oss-120b.json`
  both passed `1/1`.

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
  side effect, unless the user's wording explicitly says fully open/close.
- `disambiguation_55`: helper/summary text claims navigation is set even when
  route/charging/fast-food constraints are not fully grounded or when earlier
  route/POI tool calls failed. Suggested tuning: completion-claim guard should
  validate the whole planned route chain and selected stop constraints, not only
  the presence of any navigation mutation.

## Current Failure Notes

| Task | Cerebras full run | Qwen subset | Current reading |
| --- | --- | --- | --- |
| `disambiguation_2` | `0/3` | fail | User wanted all windows opened to the same level and would answer `50%` if asked. The agent opened all windows fully first, then later corrected to `50%`. This is an over-eager side effect before resolving the missing percentage. |
| `disambiguation_8` | `0/3` | fail | The agent asked a broad lights question and stopped. Expected flow needs exterior-light state, weather, then fog-light action if context resolves the ambiguity. |
| `disambiguation_10` | `0/3` | pass | Cerebras failed by acting on fog/low-beam lights without the required weather check and by drifting into high-beam confirmation text. Qwen passing means the policy path is representable; this is prompt/skill clarity, not missing wrapper capability. |
| `disambiguation_12` | `0/3` | fail | "Too warm" was narrowed to a cabin-temperature question. Expected behavior is to present/resolve cooling options and handle the user's chosen seat-heating reduction, including `set_seat_heating`. |
| `disambiguation_20` | `0/3` | pass | Cerebras turned on low beams only. Expected state-aware path: read exterior lights, notice low beams are already on, ask/confirm high beams, then call `set_head_lights_high_beams`. Qwen passing again suggests this is an instruction/example issue. |
| `disambiguation_22` | `0/3` | fail | Defrost/window flow executes but mismatches the expected action sequence. Need plan all window-close and defrost side effects together before the first mutation. |
| `disambiguation_26` | `1/3` | pass | Some trials skipped the charging-station/status/time bundle and jumped to route distance. Passing Qwen and one Cerebras trial show the helper surface is sufficient; failure is incomplete charging-information planning. |
| `disambiguation_28` | `0/3` | pass | The agent made an initial fan-speed side effect before the user clarified the desired `+2` change. It should avoid exploratory climate mutations when the user says other climate/AC settings must remain unchanged. |
| `disambiguation_30` | `0/3` | pass | The agent turned AC on and set circulation to `AUTO`; expected is AC on plus preferred circulation mode. This is the clearest disambiguation helper override: safe AC behavior must not erase an explicit/stored circulation preference. |
| `disambiguation_32` | `2/3` | fail | Same window-percentage pattern as `disambiguation_2`. The failed trial opened windows fully, then later corrected to `50%` before AC/fresh-air work. |
| `disambiguation_38` | `1/3` | pass | The agent applied driver temperature and driver heating, but also set passenger seat heating even though the user asked to warm the driving area. Need zone-specific heating examples to prevent over-broad occupied-seat mutation. |
| `disambiguation_42` | `1/3` | fail | Route/contact/email flow can pass, but failed trials omitted `get_charging_specs_and_status` before sending the email. Needs complete route + charging facts before confirmation-required email content. |
| `disambiguation_44` | `0/3` | pass | Calendar and weather reads happen, but attendee/contact resolution is weak. Some trials ask which Tina; others send to Tina with incomplete or downgraded weather wording. Qwen passing suggests better examples may be enough. |
| `disambiguation_46` | `0/3` | fail | Same route path as `base_82`: Berlin route via `K57, B65` is selected in the end, but there is premature fastest-route commit and generic "fastest route" narration after the user-selected route. |
| `disambiguation_48` | `2/3` | pass | One trial missed route-based charging search after waypoint replacement, using nearby/location-style charging discovery instead. The route-edit part is stable enough; charging-on-route follow-up is flaky. |
| `disambiguation_50` | `0/3` | fail | The agent stopped when outside temperature was unavailable and never checked sunroof/sunshade state or asked the remaining percentage/confirmation needed to continue. |
| `disambiguation_53` | `1/3` | fail | Same family as `base_96`: conditional weather branch sometimes selects Cologne correctly but uses fastest route wording/action where the task expects shortest. |
| `disambiguation_55` | `0/3` | fail | Correction and route/POI composition remain unstable. The agent retries invalid Ordino, sometimes searches POIs at charging-station IDs, misses `search_poi_along_the_route`, and can claim navigation is set despite incomplete charging/fast-food constraints. |

## Active Root Causes

### Stored preferences are ignored or fetched too late

Status: preference preflight is now implemented and passed six of seven targeted
preference-heavy cases in one trial. Keep these on the watchlist until a full
disambiguation split confirms no collateral damage.

Affected tasks:
- `disambiguation_22`

Fix direction:
- Implemented: preflight reads all live-schema-supported preference categories
  once per task and stores the nested tree plus a compact summary in
  `scratchpad["entities"]["user_preferences"]`.
- Remaining watch item: validate on the full disambiguation split, especially
  `disambiguation_22`, where preference handling interacts with a defrost/window
  action sequence rather than a simple single setter.
- Implemented after the preference-preflight target: `select_route_by_user_preferences(...)`
  for stored route-selection preferences such as fastest/no-toll/within-N-minute
  rules.

### Broad nouns are resolved with the wrong tool or a premature question

Affected tasks:
- `disambiguation_8`
- `disambiguation_10`
- `disambiguation_12`
- `disambiguation_20`
- `disambiguation_28`
- `disambiguation_30`

Fix direction:
- For broad words like "lights", "headlights", "too warm", "air circulation",
  and "airflow", first gather the relevant current state and preference facts.
- Choose a single tool only when policy, explicit wording, preference, or state
  makes it unique.
- Ask the user only after those facts still leave multiple valid options.

### Compound requests are not planned atomically

Affected tasks:
- `disambiguation_22`
- `disambiguation_28`
- `disambiguation_30`
- `disambiguation_34`
- `disambiguation_42`

Fix direction:
- Build a complete intended action set before the first side-effect call.
- Preserve "leave everything else unchanged" constraints across helper calls.
- Generate the final response from executed tool results and required units,
  not from an intermediate plan. Temperature confirmations must say
  `degrees Celsius`.

### Route, charging, and contact state is not carried through multi-turn plans

Affected tasks:
- `disambiguation_42`
- `disambiguation_44`
- `disambiguation_50`
- `disambiguation_52`
- `disambiguation_53`
- `disambiguation_55`

Fix direction:
- Keep pending route, charging, POI, calendar, and contact choices as structured
  facts until they are executed or superseded.
- When the user corrects a location or route, invalidate stale alternatives and
  consume the corrected value once.
- Do not ask the user for internal IDs or route kilometer marks when a lookup or
  search tool can ground the missing value.
- Implemented for arrival-open POIs: `select_poi_at_location_open_at_route_arrival(...)`
  computes route-arrival time, searches POIs without `currently_open`, and
  selects the unique POI whose opening hours cover arrival time.

## Recommended Order

1. Fix helper/narration side effects that mutate too early or override selected
   preferences: window percentages, AC circulation mode, and route-choice
   narration.
2. Add broad-control examples for "lights/headlights", "too warm", and
   "air circulation/airflow" that show state/preference grounding before
   mutation or clarification.
3. Tighten compound-plan handling so helpers do not violate "leave other
   settings unchanged" and final responses include required units.
4. Strengthen multi-turn structured pending state for route/charging/contact
   plans and correction consumption.
