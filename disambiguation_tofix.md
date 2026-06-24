# Remaining Disambiguation Failures To Fix

## Active Evaluation

Final-submission judge: Gemini 2.5 Flash.

Latest full train run:
`output/run_configs/20260624-121323__run_configs-coroutine_full_train_cerebras_gemini_1__train-trials1-baseall-hallall-disall__gpt-oss-120b.json`

Configuration:
- Agent provider: Cerebras
- Agent model: `gpt-oss-120b`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `1`

Result for disambiguation split: `10/31` (`32.3%`) raw.

One raw failure is not behaviorally analyzable:
- `disambiguation_2`: no trajectory. Gemini policy evaluation returned HTTP
  503 before any assistant action was recorded. Treat as infrastructure/rerun,
  not as an agent bug.

Behavior-measured result excluding that no-trace 503: `10/30` (`33.3%`).

Latest passes:
- `disambiguation_6`
- `disambiguation_14`
- `disambiguation_16`
- `disambiguation_24`
- `disambiguation_32`
- `disambiguation_36`
- `disambiguation_40`
- `disambiguation_46`
- `disambiguation_48`
- `disambiguation_51`

Active behavior failures:
- `disambiguation_0`
- `disambiguation_4`
- `disambiguation_8`
- `disambiguation_10`
- `disambiguation_12`
- `disambiguation_18`
- `disambiguation_20`
- `disambiguation_22`
- `disambiguation_26`
- `disambiguation_28`
- `disambiguation_30`
- `disambiguation_34`
- `disambiguation_38`
- `disambiguation_42`
- `disambiguation_44`
- `disambiguation_50`
- `disambiguation_52`
- `disambiguation_53`
- `disambiguation_54`
- `disambiguation_55`

## Current Failure Notes

- `disambiguation_0`: stored sunroof preference was not applied. The agent
  asked confirmation for opening sunroof to `100%` and then did it; expected was
  preference-driven percentage.
- `disambiguation_4`: ambient-light color should have been resolved from stored
  preferences. The agent asked "Which ambient light color would you like?" and
  never called `set_ambient_lights`.
- `disambiguation_8`: the agent asked a broad lights clarification and stopped.
  Expected flow still required exterior-light/weather grounding and fog-light
  action after resolving the ambiguity.
- `disambiguation_10`: "headlights" was resolved to high beams off; expected
  low-beam handling. Missing expected `set_head_lights_low_beams`.
- `disambiguation_12`: "too warm" was narrowed to a temperature question, but
  expected climate/seat-heating disambiguation and `set_seat_heating`.
- `disambiguation_18`: the agent increased fan speed, then asked for airflow
  direction instead of using the stored FEET airflow preference.
- `disambiguation_20`: "headlights for better visibility" resulted in low beams
  only. Expected state-aware high-beam path after recognizing low beams were
  already on.
- `disambiguation_22`: defrost flow executed, but the earlier window-close turn
  and compatible preference handling made the final action sequence mismatch.
- `disambiguation_26`: charging-time request should have used stored target SOC
  and searched candidates. The agent asked the user what charge target they
  wanted.
- `disambiguation_28`: user wanted fan speed increased by two while leaving
  other climate/AC settings unchanged. The agent turned AC on first, then later
  changed fan speed.
- `disambiguation_30`: user wanted fresh-air circulation. The agent used AC
  helper behavior and set circulation to AUTO.
- `disambiguation_34`: tool actions passed, but policy failed because final
  response said `22 degrees` instead of `22 degrees Celsius`.
- `disambiguation_38`: heating preference was not resolved internally. The agent
  asked what heating level to set and never applied temperature, seat heating,
  or steering-wheel heating.
- `disambiguation_42`: route, charging, contact, and email flow executed
  partially, but final actions mismatched; the plan was not validated as one
  complete route/charging/email bundle before sending.
- `disambiguation_44`: calendar/weather facts were read, but contact
  disambiguation fell back to asking for Tina instead of resolving attendee
  contact details and sending the weather email.
- `disambiguation_50`: the agent stopped after saying outside temperature was
  unavailable and never checked sunroof/sunshade state or asked for the
  remaining disambiguation.
- `disambiguation_52`: route preferences were fetched only after presenting
  route options; navigation was never set.
- `disambiguation_53`: conditional Mannheim/Cologne navigation selected Cologne
  and set fastest route, but final expected action still failed; this mirrors
  the base route-preference issue where explicit shortest/default rules are not
  carried through the selected branch.
- `disambiguation_54`: route preferences were fetched and Belgrade routes were
  presented, but the required supermarket search and `set_new_navigation` never
  happened.
- `disambiguation_55`: correction handling remains unstable. The agent retried
  invalid Ordino lookup, then after correction to Andorra la Vella repeatedly
  asked for kilometer information instead of using route/POI tools; it also
  omitted toll information when presenting routes.

## Active Root Causes

### Stored preferences are ignored or fetched too late

Affected tasks:
- `disambiguation_0`
- `disambiguation_4`
- `disambiguation_18`
- `disambiguation_22`
- `disambiguation_26`
- `disambiguation_38`
- `disambiguation_52`
- `disambiguation_54`

Fix direction:
- Before asking the user for a missing vehicle setting, route-selection choice,
  charging target, or ambient-light color, retrieve the relevant preference
  category if the policy says preferences can resolve the ambiguity.
- Apply compatible preferences before mutation, not after presenting options.
- Keep this as a general preference-before-question habit, not task-specific
  preference values.

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
- `disambiguation_54`
- `disambiguation_55`

Fix direction:
- Keep pending route, charging, POI, calendar, and contact choices as structured
  facts until they are executed or superseded.
- When the user corrects a location or route, invalidate stale alternatives and
  consume the corrected value once.
- Do not ask the user for internal IDs or route kilometer marks when a lookup or
  search tool can ground the missing value.

### Infrastructure/no-trace failures

Affected tasks:
- `disambiguation_2`

Fix direction:
- Rerun this task or the whole split before drawing conclusions. The latest
  artifact contains no assistant trajectory because Gemini returned 503 during
  policy evaluation.

## Recommended Order

1. Add preference-before-question guidance/examples for common vehicle,
   lighting, charging, and route-selection ambiguities.
2. Add broad-control examples for "lights/headlights", "too warm", and
   "air circulation/airflow" that show state/preference grounding before
   mutation or clarification.
3. Tighten compound-plan handling so helpers do not violate "leave other
   settings unchanged" and final responses include required units.
4. Strengthen multi-turn structured pending state for route/charging/contact
   plans and correction consumption.
5. Rerun `disambiguation_2` or the full split to replace the no-trace 503.
