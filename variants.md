# Prompt / Policy Variants

Working doc for experiments on what the agent sees in its system prompt. Tracks
the policy-injection path, the rules for trimming it safely, and each variant we
try with its measured result.

Authoritative measurement method: **two independent full 3-trial runs, compared
by intersection** (see `output/consistency_two_run_intersection.md` and
`scripts/rank_consistency.py`). ~27% of tasks flip between near-identical runs
because the GPT-4.1 user-simulator + policy-evaluator are stochastic (agent is
temperature 0), so a single 3-trial run cannot resolve a small prompt change.

---

## 1. How the policy is currently injected

Received verbatim from the evaluator → stored on the workspace → rendered once
into the system prompt → repeated on every turn. Per-task, constant except the
`## System State Awareness` block (CURRENT_LOCATION + DATETIME).

1. **Evaluator → A2A text part.** Each task's first inbound text part is
   `"System: <full policy>\n\nUser: <request>"`.
2. **Split out the policy** — `coroutine_agent.py:542-545`
   (`_parse_inbound`): splits on `"\n\nUser:"`, strips the leading `"System:"`;
   the remainder is `policy_text` (the full ~200 lines).
3. **Store on the workspace, once** — `coroutine_agent.py:196-197`:
   `self.workspace.policy = inbound.policy_text` (only set when non-empty, i.e.
   the first turn).
4. **Build the system prompt, once per task** — `coroutine_agent.py:258-263`,
   guarded by `if not self.initialized`:
   `self.system_prompt = build_system_prompt(car_policy=ws.policy, ...)`.
5. **Render verbatim** — `coroutine_prompts.py:235-243` (`build_system_prompt`):
   `prompt += "\n\n## CAR-bench Policy From Evaluator\n" + car_policy.strip()`.
   No transformation; policy sits between the skill and the tool listing.
6. **Shown every turn** — `coroutine_agent.py:292-293`: `system_prompt` is the
   system message prepended on every `_rebuild_messages`.

### Insertion point for any policy variant

Apply a `distill_policy(text) -> text` transform to the **rendered copy only**,
inside `build_system_prompt`, immediately before `prompt += car_policy.strip()`.

Hard constraints:
- **Do not mutate `ws.policy`.** `_current_policy_context()`
  (`coroutine_repl.py:2031`) regex-parses `CURRENT_LOCATION` and `DATETIME` out
  of `self.policy`; that feeds `policy_now()` and the weather/sunroof helpers.
  The transform must operate on the `car_policy` argument (prompt copy), leaving
  the stored policy full and verbatim.
- **Always preserve the `## System State Awareness` block** — it is both the only
  per-task-varying part and the source for datetime/location.
- The trace (`inbound_a2a`, `coroutine_agent.py:203`) keeps logging the verbatim
  `policy_text`, so the record shows exactly what the evaluator sent; only the
  model's view changes.
- Grading is unaffected: the policy evaluator judges against its **own** copy of
  the policy, not the agent prompt. Worst case of a bad trim is agent behavior,
  never a grading penalty for "not showing the rule".

---

## 2. Why the numbered protocols are trim candidates

A helper only "reliably implements" a rule if it is enforced no matter how the
model calls the tool. Raw-path guarding today (`_delegate_policy_sensitive_call`
+ `_make_tool_wrapper`) covers ten raw tools — `set_fog_lights`,
`set_head_lights_high_beams`, `set_new_navigation`,
`get_routes_from_start_to_destination`, `get_weather`,
`search_poi_along_the_route`, `navigation_add_one_waypoint`,
`navigation_delete_waypoint`, `navigation_replace_one_waypoint`, and
`navigation_replace_final_destination` (the non-lighting ones added for the
degenerate-call, active-route-edit, weather-day-clamp, and route-narration
guards) — and the confirmation state machine covers any `REQUIRES_CONFIRMATION`
tool.

### Tier A — safe to remove now (enforced on all paths, incl. raw setters)
- `004` confirmation requirement → confirmation state machine.
- `013` fog lights → low/high beam prerequisites → `set_fog_lights` raw delegates
  to `set_fog_lights_on_safe`.
- `014` high-beam-blocked-by-fog → `set_head_lights_high_beams` raw delegates to
  `set_high_beams_on_safe`.

### Tier B — remove only AFTER adding raw→safe delegation (helper exists, raw path currently unguarded)
- `005` + `008/009` (sunroof) → `open_sunroof_safe`; needs
  `open_close_sunroof` → `open_sunroof_safe` delegation.
- `010` defrost protocol → `defrost_front_window`; needs
  `set_window_defrost` → defrost helper delegation. NOTE: helper is front-only;
  all-window defrost is partial — verify before removing `010`.
- `011` AC protocol → `set_air_conditioning_on_safe`; needs
  `set_air_conditioning` → safe-helper delegation.
- `012` single-zone temp-diff warning → `set_climate_temperature_safe`; needs
  `set_climate_temperature` → safe-helper delegation.
- `008/009` (fog) is already covered by the `013` delegation.

### Tier C — keep (no reliable helper; response-content or not-yet-built)
- `002` units / 24h / TTS formatting (pure response constraint).
- `007` window > 25% + AC on → confirm + energy warning (no helper at all).
- `016`–`019` navigation construction rules (only partially guarded).
- `021` / `022` toll + route-presentation disclosure (response content; already a
  known failure class).
- `023` / `024` calendar / weather current-day (`policy_now` helps but does not
  enforce).
- **Disambiguation Protocol** (policy lines ~54-72): keep. It is the largest
  un-enforced area (disambiguation gate not built; 17 stable fails in that
  split). Removing it strips the model's only guidance there.

### Meta-policy-question risk (user asks ABOUT a rule, not to do something)
Low. When a helper blocks, it already emits the explanatory rule (e.g.
`open_sunroof_safe` states the sunshade rule). Only the pure "tell me your
policy" question is lost; traces suggest 1-2 tasks, partial credit, rare.

### Expectation setting
Trimming will NOT reduce the 27% run-to-run variance (that is the
simulator/evaluator, external to the agent). Goals are: fewer prompt tokens,
lower latency, and a possible small accuracy gain from less distraction on a
small model. Treat as an accuracy/cost play, not a noise play.

---

## 3. Variant log

| id | change | raw-path hardening | runs | Pass^3 (base/dis/hall) | verdict |
|----|--------|--------------------|------|------------------------|---------|
| baseline-guards | 4 reliability guards, full policy | fog/highbeam/new_nav | guards_train_3, _3b | 23/9/38, 23/8/35 | shipped; deterministic backlog = base 16, dis 17, hall 1 (h78) |
| prompt-trim-1 | collapsed 4 advisory bullets → 2 | same | guards_train_3b | (within noise of _3) | kept (leaner); effect unmeasurable at this power |

Planned:
- `policy-trim-A`: remove Tier A policy lines from the prompt copy only.
- `policy-trim-AB`: add Tier B raw→safe delegations, then remove Tier A+B lines.
