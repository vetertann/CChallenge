# Development Discipline

The rules that decide *whether a change ships*, distinct from the architecture
(`coroutine-agent-architecture.md`) and the protocol reference
(`development-guide.md`). These are the practices that produced the results;
they exist because prompt-driven agents overfit silently and the only defense
is a discipline that refuses changes which improve a weak model by exploiting
its weaknesses rather than by improving structure.

## 1. The base prompt is stable; skills are what you train

Treat `BASE_SYSTEM_PROMPT` (runtime mechanics: the REPL contract, output
discipline, tool/scratchpad rules) as a fixed substrate. It changes only when
the *runtime* changes. Domain behavior is iterated in the `Skills/` layer
(`car_domain*.md`, `email.md`) and in runtime helpers — never by growing the
base prompt with task-specific rules.

Why: mixing domain rules into the base prompt makes every domain tweak a
whole-agent change, destroys the ability to A/B a skill against the bare
substrate, and is the main driver of the attention dilution that causes
rare rule-compliance misses. One home per rule.

How to apply: before editing the base prompt, ask "is this a runtime mechanic
or a domain behavior?" If domain, it goes in a skill or a helper. If you cannot
express it without base-prompt text, that is a signal the runtime is missing a
guarantee (see rule 3).

## 2. The Bitter Lesson as a regression test

A change ships only if a **stronger model already did better than a weaker
model on the bare prompt** — measured *before* any domain skill is layered on.
The bare-prompt ordering (stronger > weaker) is the ground-truth signal that
the task rewards reasoning/structure, not memorized scaffolding.

The failure signature to reject: a smaller model **gained** while a bigger
model **regressed** on the same change. That means the change fit the small
model's specific weaknesses, not the task structure — it is scaffolding that a
better model has to route around, and it will not generalize to the hidden set.

Operational triage when 120b fails or flakes:

1. Re-run the failing task on the stronger model (GPT-5.5) first.
2. If the stronger model **passes** but 120b fails: the task is solvable within
   the current architecture — 5.5 proves the policy and tools are right — but we
   have **not yet built a rigid enough failure pipeline** in tools and prompts to
   carry the weaker model to that reachable ceiling. This is the signal to **add
   harness**: convert the missing determinism into wrapper/helper/guard code so
   the weaker model succeeds by construction. It is *not* "log as variance and
   move on." Only the genuinely irreducible residual — sim/judge noise with no
   encodable structure — is logged as variance.
3. If the stronger model **also fails**: the defect is in the harness we
   **already** built — a wrong policy interpretation, a wrong/missing tool
   implementation, or a normalization bug. **Audit and fix the existing
   harness; do not dump new harness on top of a broken one.** Piling new
   structure over a wrong foundation hides the defect instead of removing it.

Any harness added in step 2 is still bound by the ship gate: it must be
deterministic structure (rule 3) that does not regress the stronger model on
the bare prompt. That is what keeps "add harness for 120b" from decaying into
120b-specific scaffolding a stronger model would route around.

## 3. Policy IS tool

Many CAR-bench policies are deterministic given grounded facts, which means
they can be encoded **directly as deterministic logic in tool CODE** —
`*_guarded` wrappers, policy helpers, the confirmation state machine,
live-surface membership, result normalization — rather than as prose the model
is asked to remember. This is one of the best fits for the CodeAct approach:
because the model's action surface *is* executable code, the tools that code
calls can embed the policy as ordinary deterministic logic. The policy runs in
the code layer, enforced whether the model calls the raw name or the helper.

This is the enforcement corollary of the facts-vs-intention boundary: the
runtime owns facts and strict policy; the model owns interpretation. A policy
expressed only in the prompt is a policy the model can forget under attention
pressure; a policy expressed as deterministic tool code is enforced by
construction and costs the model zero attention and zero extra reasoning turns.
The practical test for each policy: "is this deterministic given grounded
facts?" If yes, it is code in the tool layer. Prompt text is the fallback for
genuinely interpretive decisions only.

How to apply: when a policy rule keeps getting missed, do not strengthen the
prompt wording — move the rule into the tool layer (delegate the raw setter to
a safe helper, add a response obligation, add a membership/argument guard).
Prompt text is the fallback for genuinely interpretive decisions only.

The three rules compose: base prompt stays thin (1), so the Bitter Lesson test
(2) measures structure rather than scaffolding, because policy that would
otherwise be scaffolding has been converted into enforced tools (3).

## Measurement hygiene (enabling condition)

Near the plateau a single trial moves several points on evaluator/simulator
noise alone. Judge every candidate change on a **two-run (or three-trial)
consistent-failure set**, never a single run — otherwise rules 2 and 3 are
applied to ghosts. See `car-bench-eval-variance` and
`output/consistency_two_run_intersection.md`.
