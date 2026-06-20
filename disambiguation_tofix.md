# Remaining Disambiguation Failures To Fix

## Active Evaluation

Final-submission judge: Gemini 2.5 Flash.

Run:
`output/run_configs/20260620-180604__run_configs-coroutine_disambiguation_gemini_1__train-trials1-base0-hall0-disall__openai-gpt-oss-120b-fast.json`

Configuration:
- Agent: `openai/gpt-oss-120b-fast`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `1`

Result: `9/31` (`29.0%`).

Note: this run predates the 2026-06-20 runtime changes (dynamic-key distance/
charging normalization, route narration on presentations/new-route sets,
active-nav refuse→redirect, idempotent nav delete). Those are global, so re-run
the disambiguation split to refresh these numbers before acting.

Active failures:
- `disambiguation_0`
- `disambiguation_2`
- `disambiguation_8`
- `disambiguation_10`
- `disambiguation_12`
- `disambiguation_18`
- `disambiguation_22`
- `disambiguation_26`
- `disambiguation_30`
- `disambiguation_32`
- `disambiguation_34`
- `disambiguation_38`
- `disambiguation_40`
- `disambiguation_42`
- `disambiguation_44`
- `disambiguation_46`
- `disambiguation_48`
- `disambiguation_50`
- `disambiguation_52`
- `disambiguation_53`
- `disambiguation_54`
- `disambiguation_55`

## Active Root Causes

### Stored preferences are ignored or incompletely applied

Affected tasks:
- `disambiguation_0`: stored sunroof preference was 50% and never fully open;
  the model opened it to 100%.
- `disambiguation_18`: asked for airflow direction instead of retrieving the
  stored FEET preference.
- `disambiguation_22`: defrost default ignored the compatible FEET airflow
  preference.
- `disambiguation_26`: asked for target SOC and charger instead of using the
  stored 80% preference and searching candidates.
- `disambiguation_38`: changed temperature before resolving the linked preferred
  heating level.
- `disambiguation_52`: started navigation without applying route preferences or
  handling toll requirements.

Fix direction:
- Resolve relevant preferences before asking a question or mutating state.
- Apply all compatible preferences in one plan.
- Ask only for unresolved choices that materially affect execution.

### Ambiguous requests trigger premature mutations

Affected tasks:
- `disambiguation_2`: opened windows to 100% before clarifying percentage.
- `disambiguation_12`: narrowed "too warm" to target temperature before
  determining which climate control the user meant.
- `disambiguation_34`: activated occupied-seat heating before clarification.
- `disambiguation_46`: selected Berlin as destination before route choice.
- `disambiguation_50`: invented a 100% sunroof setting instead of asking for the
  percentage after the condition was satisfied.
- `disambiguation_53`: guessed a conditional branch and omitted toll handling.

Fix direction:
- Separate read-only grounding from mutation.
- Do not execute irreversible or user-visible changes while a required
  disambiguation remains unresolved.
- Store the pending intent and consume the clarification once.

### Compound actions are split, contradicted, or partially completed

Affected tasks:
- `disambiguation_30`: used an AC helper instead of circulation control and later
  claimed AUTO mode; expected result was fresh air.
- `disambiguation_32`: eventually set fresh air but first reported
  recirculation, so the compound request was not atomic.
- `disambiguation_34`: produced an incomplete temperature/heating plan.
- `disambiguation_38`: split temperature and preferred heating actions.
- `disambiguation_40`: window/defrost synchronization was incomplete; the
  required all-window target was 5%.
- `disambiguation_42`: route, charging, contact, and email facts were not planned
  together; the email also reported an incorrect four-minute duration.

Fix direction:
- Build and validate a complete structured plan before executing compound
  mutations.
- Preserve grounded values across helper calls.
- Generate the final response from actual executed results, not an intermediate
  plan.

### Clarifications and corrections are not consumed reliably

Affected tasks:
- `disambiguation_48`: a resolved waypoint was not consumed once; stale
  clarification caused repeated replacement and loss of plug/route data.
- `disambiguation_55`: corrected Ordino reappeared, irrelevant preferences were
  requested, direct navigation started before the charging stop, and tool errors
  did not produce a stable corrected plan.

Fix direction:
- Add a pending-slot state machine with `unresolved`, `resolved`, `executed`, and
  `superseded` states.
- Remove resolved alternatives from subsequent prompts.
- Invalidate stale route and charging options after corrections.

### Tool selection and grounded-state reuse are weak

Affected tasks:
- `disambiguation_10`: selected high beams and manual confirmation for
  "exterior lights" instead of resolving the current lighting state.
- `disambiguation_44`: failed to reuse calendar facts, contradicted the meeting,
  used the wrong weather parameter, and asked for Tina's raw ID.
- `disambiguation_54`: claimed a preferred Belgrade plan but failed to find the
  required open supermarket for the second segment.

Fix direction:
- Retrieve current state before resolving broad control nouns.
- Keep calendar, weather, contact, and route facts in normalized structured
  state.
- Never ask the user for an internal identifier when a lookup tool exists.

### `disambiguation_8`: apparently correct clarification still failed

The agent asked the expected lighting clarification, but the Gemini simulator
stopped and assigned `DISAMBIGUATION_ERROR`.

Fix direction:
- Keep this in the active Gemini backlog because it is a judged failure.
- Reinspect the exact wording for avoidable ambiguity.
- Do not add task-specific behavior solely to satisfy a simulator stop.

## Recommended Order

1. Implement consume-once pending-intent state and correction invalidation
   (`disambiguation_48`, `_55`, plus several premature-mutation cases).
2. Resolve stored preferences before mutation (`_0`, `_18`, `_22`, `_26`,
   `_38`, `_52`).
3. Add complete compound-plan validation (`_30`, `_32`, `_34`, `_40`, `_42`).
4. Tighten broad-noun state resolution and grounded-value reuse (`_10`, `_44`,
   `_54`).
5. Re-run Gemini after the general fixes before changing behavior for
   `disambiguation_8`.
