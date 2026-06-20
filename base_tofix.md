# Remaining Base Failures To Fix

## Active Evaluation

Final-submission judge: Gemini 2.5 Flash.

Run:
`output/run_configs/20260620-232527__run_configs-coroutine_base_gemini_1__train-trials1-baseall-hall0-dis0__openai-gpt-oss-120b-fast.json`

Configuration:
- Agent: `openai/gpt-oss-120b-fast`
- Skill: `car_domain_120b.md`
- User simulator: `gemini/gemini-2.5-flash`
- Policy evaluator: `gemini/gemini-2.5-flash`
- Trials: `1`

Result: `39/50` (`78.0%`) — up from `35/50`.

Active failures (`11`):
- `base_42`
- `base_48`
- `base_60`
- `base_74`
- `base_78`
- `base_82`
- `base_84`
- `base_86`
- `base_88`
- `base_96`
- `base_98`

Fixed since the 35/50 run:
- `base_64` (confirmed): the `navigation_add_one_waypoint_guarded` completeness
  bug + always-narrate the selected route (was `NavigationAddOneWaypoint_008` +
  missing policy-022 narration).
- `base_70` (confirmed): route-presentation narration now fires on a plain
  `get_routes` presentation, not only on edits.
- `base_14`, `base_58` flipped to pass but were not direct targets — treat as
  unconfirmed (single-trial; could be noise) until a multi-trial run.

Runtime changes landed for this run (see `docs/coroutine-agent-architecture.md`):
dynamic-key normalization (`get_distance_by_soc` → `distance_km`,
`calculate_charging_time_by_soc` → `minutes`); always-narrate on add; active-nav
guard converted from refuse to **redirect** (`set_new_navigation` while active →
`navigation_replace_final_destination`); idempotent no-op when deleting an
already-removed waypoint; the `navigation_add_one_waypoint_guarded` completeness
fix.

## Active Root Causes

### `base_64`: waypoint wrapper emits an incomplete protocol — FIXED

`navigation_add_one_waypoint_guarded` treated `waypoint_id_after_new_waypoint`
as sufficient and forwarded the raw call even when the route-away dependency was
missing → `NavigationAddOneWaypoint_008`. Now it resolves both the to-route and
(for mid-route inserts) both after-args via `_resolve_route_arg`, and narrates
the selected route. Confirmed passing. Unit tests added
(`test_insert_with_after_waypoint_but_missing_away_route_derives_it`).

### `base_78`: nested contact names fail required-field validation

All Scott contact IDs were grounded, but `get_contact_details` validation did
not accept nested fields such as `name.first_name` and `name.last_name`.

Fix direction:
- Normalize nested and flat contact-detail schemas before validation.
- Keep IDs and display names stable across lookup and detail calls.

### `base_58`: phone normalization — passed this run (UNCONFIRMED)

Previously the POI phone was called with leading whitespace, breaking the
action match. It passed this run without a direct fix, so treat as single-trial
noise until a multi-trial confirms. The fix below is still worth doing
defensively.

Fix direction:
- Strip surrounding whitespace and normalize phone formatting at the wrapper
  boundary.
- Preserve the evaluator-provided canonical value where possible.

### `base_48` and `base_82`: selected-route state is not preserved

Destination replacement succeeded, but later alternative-route selection used
delete-and-recreate behavior or failed to complete against the selected route.

Fix direction:
- Persist route-option IDs and the active route revision.
- Apply a selected alternative to the current navigation state rather than
  reconstructing the route unless policy requires reconstruction.
- Invalidate stale route options after any route mutation.

### `base_88`: completed deletion is retried — partially fixed

The delete-loop is fixed: `navigation_delete_waypoint_guarded` now returns an
idempotent no-op success when the target waypoint is already absent, so the
repeated delete no longer errors and loops. **Still failing** on two residuals:
(a) policy wants every segment of the resulting multi-stop route to be the
fastest (the Berlin→Leipzig leg is not re-validated), and (b) the charging
sub-task was weak — it could not read the route distance (now mitigated by the
`distance_km` alias, re-check next run).

Fix direction (remaining):
- After a waypoint mutation, re-validate that all adjacent segments are the
  fastest route.
- Confirm the `get_distance_by_soc` → `distance_km` alias resolves the distance
  read here.

### `base_42`: relative adjustments are guessed or replayed

`base_42` ("more air circulation") jumped the fan two levels (2→4) and the
request is tool-ambiguous (fan speed vs air-circulation mode). (`base_14`,
previously grouped here, passed this run but was not a direct fix target —
unconfirmed.)

Fix direction:
- For relative words with no explicit target, read current state and apply one
  defined step or clarify; do not guess the magnitude.
- Disambiguate "air circulation" between the candidate tools.

### `base_60`: compound climate request is collapsed into one helper

The driver-only request used `set_occupied_seat_heating` (heated the passenger
too) and the helper's `_respond_locked` clobbered the policy-012 temperature
warning and the rest of the compound task. This is the **helper response-lock**
over-narrowing flagged in the guard audit: a helper that completes one subgoal
locks the final response and the turn ends, dropping the other subgoals/warnings.

Fix direction:
- Do not use `set_occupied_seat_heating` for explicitly single-zone requests;
  use `set_seat_heating` for an explicit zone.
- Have helpers report their outcome **without** `_respond_locked` in a compound
  turn, so the model composes the final message and keeps required warnings.
  (Architectural trade-off — locking exists to prevent false-success claims.)

### `base_70`: route alternatives not offered — FIXED

The model failed to offer the required route alternatives after presenting a
route. The narration mechanism now fires on a plain `get_routes` presentation
(not only on edits), so the fastest/alternatives sentence is appended.
Confirmed passing. (Kept here for the EV-route-planning checklist idea, still
useful for `base_74`/`base_84`/`base_98`.)

Fix direction (carried forward for other charging tasks):
- Add a generic EV-route planning checklist to the skill.
- Require vehicle charging state/specifications before selecting charging
  options when policy depends on them.

### `base_74`: premature email confirmation and duration corruption

The model asked for email confirmation before grounding the complete plan and
reported `38 min` instead of `14 h 38 min`, while losing route and plug details.

Fix direction:
- Preserve typed duration values rather than reconstructing them from prose.
- Delay confirmation until all required message facts are grounded.
- Build the final email body from structured route and charging data.

### `base_84`: asks for a plug ID already present

The POI result already contained the plug identifier, but the model asked the
user to provide it.

Fix direction:
- Normalize charging POI fields and expose plug IDs explicitly.
- Before asking the user, search current REPL state for the required grounded
  value.

### `base_86`: loses a grounded POI phone number

The model had the phone number, then asked for confirmation instead of attempting
the call wrapper.

Fix direction:
- Preserve selected POI data in structured state.
- Treat an explicit call request as authorization to attempt the wrapper.

### `base_96`: conditional weather-nav — still failing (mechanism changed)

Two of the original causes were addressed: the wrong-day weather error
(`AUT-POL:024`) is fixed by `get_weather_guarded` clamping month/day to
`policy_now`, and toll disclosure now fires via the route narration. The
active-nav guard was converted from refuse to redirect. It still fails on the
conditional branch / route-selection logic (the agent didn't complete the
correct navigation). Re-check next run; remaining work is the conditional
plan-and-execute, not the date/toll/guard pieces.

Fix direction (remaining):
- Bind the conditional weather check to `policy_now` and execute exactly one
  branch deterministically.

### `base_98`: asks for facts available through tools

The model asked for meeting time and target state of charge instead of calling
calendar and charging tools.

Fix direction:
- Prefer available read tools over user questions.
- Ask only after the relevant tool has been attempted and the fact remains
  unavailable.

## Recommended Order

Done: `base_64` (waypoint protocol), `base_70` (route narration), nav-delete
idempotency (`base_88` loop), weather day-clamp + toll (`base_96` partial),
distance dynamic-key alias.

Remaining, in priority order:
1. Normalize contact nested name (`base_78`: `name.first_name`) + no-progress
   loop guard; phone/plug/POI normalization (`_84`, `_86`).
2. Multi-stop "all segments fastest" re-validation after a mutation (`base_88`).
3. Helper response-lock: don't lock in compound turns (`base_60`).
4. Stabilize route-option state across follow-ups (`base_48`, `_82`).
5. EV/charging planning: prefer tools over asking, duration normalization,
   conditional plan-and-execute (`base_74`, `_84`, `_96`, `_98`).
6. Relative-adjustment intent (`base_42`).
