# Review: Facts-vs-Intention Runtime Refactor (2026-06-21)

## Design principle

The runtime should **enforce facts and strict policy** but must not **infer the
user's intention** when several valid interpretations remain. Before this change
the coroutine runtime crossed that line in several places: it auto-redirected an
invalid navigation call to a guessed edit, declared a route "taken" after a mere
read, ended a compound turn after one helper succeeded, cleared one target's
failure when a different target succeeded, and reported a deletion for a waypoint
that was simply absent. This changeset makes the runtime stricter about facts and
less opinionated about intent.

The follow-up reliability pass keeps that principle. Helpers remain advisory
building blocks; the runtime only preserves grounded facts, mandatory policy
disclosures, exact action outcomes, and state revisions. It does not choose a
user interpretation or block alternative valid reasoning.

Scope: `src/track_1_agent_coroutine_under_test/` only. No edits to
`third_party/car-bench`. No hardcoded task IDs / wording / answers. No
catalog-diff capability inference (compliance boundary unchanged).

Files: `coroutine_repl.py`, `coroutine_prompts.py`, `car_domain_120b.md`,
`docs/coroutine-agent-architecture.md`, `base_tofix.md`,
`tests/test_coroutine_guards.py`, `tests/test_coroutine_batch_helpers.py`. New config
`run_configs/coroutine_base_gemini_3trial.toml`.

---

## The 7 changes

### 1. Active navigation → structured block (no redirect)
`set_new_navigation_guarded` (`coroutine_repl.py:~1793`).
**Before:** while a route was active it redirected the call to
`navigation_replace_final_destination` *and* substituted the fastest route — two
intention decisions. **After:** returns
`{"status": "NEEDS_ACTIVE_ROUTE_EDIT", "candidate_destination_id", "active_route",
"available_operations", "requested_route_ids", "reason"}` and the model chooses
the edit. New fact-only helpers `_requested_route_destination`,
`_active_route_summary`. **Reverses the 2026-06-20 redirect.** `_abort_with_response`
is no longer used here.

### 2. Mutation tracking by (tool, target args) + proved-by-read
`_record_mutation_outcomes`, `_reconcile_failures_with_reads`,
`_mutation_signature`, module-level `_MUTATION_STATE_PROOF` /
`_proof_open_close_window`.
**Before:** failures keyed by tool name only, so a passenger-window success
cleared a driver-window failure. **After:** keyed by tool plus semantic target
arguments; only the same target's success clears it. Failures survive every
model Python block in the user turn and clear on the next user turn. A later
state read can also clear a failure conservatively. Window proof uses exact
field mappings, so `DRIVER` cannot accidentally match `DRIVER_REAR`.

### 3. Helper response unlock (compose, don't lock on success)
`_helper_message` added; ordinary success-path `_respond_locked(...)` sites
converted to `_helper_message(...)` across the lighting/sunroof/defrost/AC/
climate/seat helpers.
**Before:** the first successful helper locked the final response and ended the
turn, dropping the rest of a compound request and any policy warning. **After:**
success helpers accumulate suggestions in `pending_helper_messages` and do
**not** set/lock the response; the model composes one final `respond(...)`
covering every subgoal. Mandatory policy disclosures are separate response
obligations, so a later helper cannot overwrite the policy-012 >3 °C warning and
`respond(...)` appends it only if the model omitted it. Locking remains for
terminal conditions and for successful execution of an explicitly confirmed
pending action. The unacknowledged-mutation-failure guard still blocks false
success. **Behavioral cost:** ordinary helper-only turns still require a model
composition step; confirmed pending actions no longer pay that cost.

### 4. Staged route narration
`_route_narration(routes, id, stage=...)` and `_store_route_narration(..., stage)`
(`:~2050`). Wording is now staged: `search` → "… Would you like details or to
navigate?" (offer, no action claimed); `select` → "I selected … for this
segment"; `navigate` → "This route segment is now using …". `get_routes_guarded`
passes `stage="search"`; edit guards keep the default `navigate`. No task content used —
only the evaluator's own `alias`/`includes_toll`/alternative count.

### 5. Additive contact / POI normalization
New `get_contact_id_by_contact_name_guarded` wraps the raw lookup
(`{"matches": {id: name}}`) into additive `contact_ids` / `contacts` / `by_id`
with the original under `raw_result` — fixes models reading wrapper keys
(`matches`, `status`) as IDs. Registered in `WORKSPACE_HELPER_NAMES` and both
delegation paths. Nested contact names expose flat
`first_name`/`last_name`/`display_name`. `_summarize_pois` gains
`poi_id`/`plug_ids`/`available_plug_ids`, preserves normalized
`charging_plugs`, raises the cap 6→12, and marks truncation instead of silently
dropping. Normalization is additive only; nothing is invented.

### 6. Uniform batch results (+ envelope-name fix)
`call_batch_sync`. Non-reserved helper keys are hoisted onto the batch
envelope so a batched helper reads like a direct call; reserved keys
(`_RESERVED_BATCH_ENVELOPE_KEYS = status/tool_name/tool_call_id/result`) stay
runtime-owned. **Bonus correctness fix found here:** the envelope is now tagged
with the name the model actually called (`_requested_name`), not the internal
`*_guarded` delegation target — previously `result_by_tool(results, "<raw name>")`
failed for *every* delegated tool inside a batch. Mixed-batch return values now
also preserve the original input order.

### 7. Absent-waypoint delete relabel
`_already_removed_result` (`:~1995`). Returns `already_absent: True` /
`waypoint_deleted: False` with a note, instead of claiming `waypoint_deleted:
True`. Still a non-error (avoids the `NavigationDeleteOneWaypoint_005` loop), but
no longer asserts a deletion happened — the model decides whether it targeted the
right stop.

### Prompt updates (`coroutine_prompts.py`)
Helpers don't end the turn (compose one final message incl. warnings); act on
`NEEDS_ACTIVE_ROUTE_EDIT`; use `set_seat_heating(seat_zone=…)` for an explicit
single seat vs `set_occupied_seat_heating` for "all occupied"; read
`contact_ids`/`by_id` from the contact lookup; `already_absent` ≠ deleted.

## Follow-up safeguards

- Same-turn repeated successful reads are cached by tool, normalized arguments,
  and state revision. Repeats return `cached: True` / `no_progress: True` but do
  not throw or constrain other reasoning. Successful mutations invalidate the
  cache.
- Navigation mutations persist returned waypoint/route IDs, advance a
  `navigation_revision`, and invalidate stale route options. `select_route`
  records revision-bound selection provenance.
- Adding a waypoint already present in the fresh route is a truthful no-op
  (`already_present: True`, `waypoint_added: False`).
- Phone and email wrapper arguments strip surrounding whitespace.

---

## Tests
The two focused suites now total **86 passing tests**. They cover helper
obligations, cross-block mutation
state, exact window proof, read caching/invalidation, navigation revisions,
duplicate insertion, nested contacts, charging plugs, mixed-batch order,
workspace aliases, context-callable resolution, safe scratchpad serialization,
route-selection aliases, POI navigation/host IDs, numeric range aliases, and
single-argument wrapper ergonomics.
Pre-existing,
unrelated failures on a clean tree: `test_a2a_response_contract.py` (imports a
track-2 module that doesn't exist) and `test_scenario_contract.py` (run-config
naming matrix).

---

## Evaluation (3-trial base, Gemini 2.5 Flash judge)
Run: `output/run_configs/20260621-134625__…coroutine_base_gemini_3trial…json`.

- Pass^1 = **36/50 (72%)** per-trial avg; Pass^3 = **32/50 (64%)**; Pass@3 = 41/50.
- 32 solid (3/3), 9 flaky (1–2/3), 9 hard-fail (0/3).
- Flaky: base_2, 14, 38, 52, 58, 64, 70, 84, 88. Hard-fail: base_42, 48, 60, 74,
  78, 82, 86, 96, 98.

**Honest read:** the only prior baseline (39/50) was a *single* trial, so it
cannot be cleanly diffed against a 3-trial run (≈27% per-task run-to-run variance
from the stochastic Gemini sim+evaluator). Per individual trial, new = 36 vs old
single = 39 — inside the noise band. Mild positive signal: base_84/base_88 moved
hard-fail → flaky. Primary targets base_60/base_96 are still 0/3. Several
previously-passing tasks (base_64/70/2/38/52) are now flaky, which may be the
helper-unlock compose-step cost or variance — **not yet separated**. There is no
3-trial run on the pre-refactor code to attribute the delta.

## Remaining evaluation questions
1. Whether response obligations recover `base_52`/`base_60` without reducing
   compound-task flexibility requires a new Gemini run.
2. Proved-by-read still covers only `open_close_window`; extending it should
   require an exact stable response contract, not heuristic matching.
3. The historical score delta remains unattributed because there is no
   three-trial pre-refactor baseline. The next run should evaluate the current
   safeguards directly rather than claim causality from the old comparison.

## Targeted follow-up evaluation

Runs:
- `output/run_configs/20260621-151010__...followup_gemini_1...json`
- `output/run_configs/20260621-151530__...followup2_gemini_1...json`

Same 120B agent/skill and Gemini 2.5 Flash simulator/evaluator, one trial each.
These are mechanism checks, not variance-resistant score comparisons.

- `base_48` passed: no premature navigation mutation; the selected second route
  was applied with `navigation_replace_final_destination`.
- `base_70` passed: `send_email(...)` created a runtime confirmation gate and
  the user's yes resumed the exact stored call.
- `base_84` passed after resolving context callables before persistence: the
  agent routed to the POI ID, then used the requested second route to Hamburg.
- `base_78` still failed because the model chose the first of several Nathan
  contacts instead of intersecting first-name candidates with grounded Scott
  IDs. Wrapper normalization was correct.
- `base_82` still failed because the model proactively selected fastest on a
  two-point destination replacement, then later reused the pre-edit Stuttgart
  destination. This is model policy/state use, not missing runtime facts.
- `base_86` completed destination replacement, charging search, and a grounded
  phone call, but searched the wrong point on the Barcelona segment because it
  did not subtract range consumed before Frankfurt.

One transient `base_84` run was invalidated by a bound `policy_location_id`
method being persisted into scratchpad. The runtime now resolves known pure
context callables before helper execution/persistence and serializes model-owned
scratchpad values safely; unit and live follow-up validation passed.

## Route-state follow-up and preflight attention messages

The regex-based final-destination checkpoint remains removed. Prompt-only
route-state injection scored 0/6 on three trials each of `base_48` and
`base_82`, because the model could read navigation state and continue to mutate
inside the same Python execution before another LLM call saw the new facts.

The boundary experiment improved the targeted route tasks, but a later
two-trial full-base run exposed a concrete regression in `base_64`: a batch
successfully read navigation plus two location IDs, then the boundary stopped
Python before the batch return value could be assigned. The next model decision
invented temporary `loc_stg_???` IDs and caused two evaluator tool errors.

The boundary and per-call synthetic route instruction have therefore been
replaced by a preflight read. Before the first model decision, the worker reads
current navigation when available and stores neutral route-shape facts in the
scratchpad. Model-written Python then runs normally, including complete batch
return values. Each new user request marks the prior navigation snapshot stale,
forcing one refreshed preflight while allowing same-turn reuse. The policy
reminder is static and follows the serialized scratchpad, placing it next to the
route facts without parsing user wording or constraining wrapper execution. We
call this a **preflight attention message**: grounded state first, then concise
general guidance adjacent to it, with the model still responsible for
interpreting intent and selecting an action.
Stable model history still retains actual user and assistant dialogue instead
of only the assistant's prior plan.

This is intended as a reusable mechanism for other frequently important system
state and disambiguation facts. A future attention message may be selected
because a grounded state family is present or because several candidates remain,
but it must not contain task-specific wording, derive the benchmark answer,
choose a candidate, mutate state, or block an otherwise valid reasoning path.
Examples include drawing attention to occupied-seat scope, AC/window
prerequisites, unresolved contact intersections, or current-day calendar facts.
The set should remain small so attention messages do not become another long,
low-salience policy dump.

Targeted evidence:
- `20260621-213348...route_facts...`: 5/6 overall; `base_48` 3/3 and
  `base_82` 2/3.
- `20260621-213609...base_82_route_boundary...`: all three agent traces
  presented alternatives without premature mutation. Reward was 1/3 because
  Gemini stopped after the options in two trials instead of making its
  instructed route selection.
- `20260621-223750...base_gemini_2trial...`: `base_64` completed every
  required action in both trials but failed only tool execution because the
  boundary lost successful batch return values. This motivated the preflight
  replacement.
- `20260622-095519...preflight_target...` and
  `20260622-095907...preflight_target...`: `base_64` and `base_82` passed, but
  `base_48` still selected fastest prematurely while the reminder was distant
  in the system prompt.
- `20260622-100158...preflight_target...`: after moving the same static reminder
  next to the scratchpad facts, `base_48`, `base_64`, and `base_82` all passed
  one targeted trial. This is targeted single-trial evidence, not a full-split
  stability claim.
