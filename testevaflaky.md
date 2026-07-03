# Public Test Evaluator/Simulator Flakiness

This file tracks public test-split cases where the local Gemini simulator or
policy evaluator appears to make the reward unstable or contradict the policy.
Use it for score interpretation and Q&A with organizers, not for task-specific
agent fixes.

Boundaries:
- Do not change evaluator code.
- Do not add hidden-test or public-test task branches.
- Do not bypass required confirmations to satisfy a flaky simulator branch.
- Only implement general fixes when the trace shows a real agent mistake.

Latest relevant run:
`output/run_configs/20260628-111324__run_configs-coroutine_test_disambiguation_cerebras_gemini_1__test-trials1-base0-hall0-disall__gpt-oss-120b.json`

Latest raw test-disambiguation score: `18/25`.

If the strong evaluator-side cases below are factored out:
- Strong only: `disambiguation_39` and `disambiguation_49` -> `20/25`.
- Including the weaker candidate `disambiguation_31` -> `21/25`, but do not
  count that as confirmed because action checks were not fully clean.

Cross-model update:
- Latest Cerebras full public-test run
  `output/run_configs/20260628-143015__run_configs-coroutine_full_test_cerebras_gemini_1__test-trials1-baseall-hallall-disall__gpt-oss-120b.json`
  passed `disambiguation_39` and `disambiguation_49`.
- Kimi full public-test run
  `output/run_configs/20260628-174003__run_configs-coroutine_full_test_kimi_nebius_gemini_1__test-trials1-baseall-hallall-disall__moonshotai-Kimi-K2.6.json`
  also passed `disambiguation_39` and `disambiguation_49`.
- GPT-5.5 targeted rerun on Kimi-failed disambiguation tasks
  `output/run_configs/20260628-180058__run_configs-coroutine_test_disamb_failures_openai_gpt55_gemini_1__test-trials1-base0-hall0-dis8ids__gpt-5.5.json`
  still failed `disambiguation_31`.

Interpretation:
The strong evaluator-side cases remain useful historical/Q&A evidence, but they
are not active misses in the latest full Kimi/Cerebras public-test runs.
`disambiguation_31` should stay a weak watch item, not a score adjustment,
because it persisted under GPT-5.5 and was never a fully clean action-correct
trace.

Additional current evidence:
- `base_81` is now classified as a strong evaluator-inconsistency case. The
  same waypoint-delete scenario has produced both sides of the contradiction:
  fastest replacement passed policy but failed action matching, while
  shortest-after-user-selection passed all action/tool checks but failed only
  the policy LLM.

## Strong Eval-Side Cases

### `disambiguation_39`: confirmation simulator branch stops before required `send_email`

Classification: evaluator/user-simulator flake.

Why this is not an agent fix:
- `send_email` is marked `REQUIRES_CONFIRMATION`.
- The agent must ask for explicit confirmation before calling it.
- In passing branches, the simulator says `Yes`, then the agent calls
  `send_email(...)` and the task passes.
- In the failing branch, the simulator sends `###STOP###` immediately after the
  valid confirmation request, so `send_email(...)` is never legally callable.

Passing branch evidence from a public test split run:

User:
`Send an email reminder to the attendees of my meeting today.`

Assistant:
`This action requires confirmation. I will send an email to leo.thomas5640@zoho.com, zane.allen1492@gmail.com, grace.lewis8349@yahoo.com, and frank.thomas8419@gmail.com saying: Reminder: Marketing Campaign is scheduled for 15:30.. Please confirm with yes.`

User simulator:
`Yes.`

Then the agent calls:
`send_email {"content_message": "Reminder: Marketing Campaign is scheduled for 15:30.", "email_addresses": ["leo.thomas5640@zoho.com", "zane.allen1492@gmail.com", "grace.lewis8349@yahoo.com", "frank.thomas8419@gmail.com"]}`

Tool result:
`{"status": "SUCCESS", "result": {"email_sent": true}}`

Failing branch evidence from the latest test-disambiguation run:

User:
`Send an email reminder to the attendees of my meeting today.`

Assistant:
`This action requires confirmation. I will send an email to leo.thomas5640@zoho.com, zane.allen1492@gmail.com, grace.lewis8349@yahoo.com, and frank.thomas8419@gmail.com saying: Reminder: Marketing Campaign at 15:30 today.. Please confirm with yes.`

User simulator:
`###STOP###`

Evaluator fields in the failing branch:
- `r_policy = 1.0`
- `r_tool_execution = 1.0`
- `r_actions_intermediate = 1.0`
- `r_tool_subset = 0.0`
- Missing tool: `send_email`

Interpretation:
The only meaningful branch difference is the simulator response after a valid
confirmation request. The agent cannot legally call `send_email(...)` without
that confirmation.

Suggested Q&A framing:
Ask whether the user simulator should confirm after a valid email confirmation
request in this task, since otherwise the task is impossible to complete under
the confirmation policy.

### `disambiguation_49`: explicit route-options request judged as if default-fastest must happen first

Classification: evaluator-policy false negative candidate, strong by analogy to
organizer-confirmed route-options cases.

Why this is probably evaluator-side:
- The user explicitly asked to show multiple route options.
- The agent presented route options.
- The user selected the shortest route and asked to set the new navigation.
- The agent called `navigation_replace_final_destination(...)`.
- All action, tool-subset, tool-execution, and final checks passed.
- Only the LLM policy check failed.

Latest failing branch evidence:

User:
`Change my final destination to Barcelona. Show me multiple route options.`

Assistant:
`Here are the route options to Barcelona from Frankfurt. Fastest (and shortest) route: K105, K121, L558, 1257.75 km, 15h 46m (route_id: rll_fra_bar_981238; fastest, first, shortest). There are 2 other route alternatives. Which route would you like to use?`

User:
`Select the shortest route. Set up the new navigation.`

Agent tool call:
`navigation_replace_final_destination {"new_destination_id": "loc_bar_223644", "route_id_leading_to_new_destination": "rll_fra_bar_981238"}`

Evaluator fields:
- `r_actions = 1.0`
- `r_actions_intermediate = 1.0`
- `r_actions_final = 1.0`
- `r_tool_subset = 1.0`
- `r_tool_execution = 1.0`
- `r_policy = 0.0`

Policy evaluator message:
`The policy states that if the user asks for a multi-stop route and does not specify the route selection, the agent should proactively take the fastest route. In this trajectory, the agent presented multiple route options to the user and waited for the user's explicit selection, rather than proactively selecting the fastest route.`

Interpretation:
This matches the already-discussed route-options contradiction: explicit user
request for route options should override the default proactive-fastest rule.
The current trace is action-correct and fails only on the policy judge.

Do not fix by forcing fastest before showing options. That would violate the
explicit route-options request and the organizer-confirmed interpretation from
the analogous route-policy Q&A.

### `base_81`: waypoint deletion route choice accepted by one evaluator component and rejected by another

Classification: evaluator-policy/action-oracle inconsistency.

Why this is evaluator-side:
- The user asks to remove Stuttgart from an active Mannheim -> Stuttgart ->
  Paris route.
- The direct Mannheim -> Paris route lookup returns at least two valid options:
  fastest `rll_man_par_341293` and shortest `rll_man_par_416568`.
- The hidden simulator instruction says that if the assistant presents multiple
  route options for the direct route, the user should choose the shortest one.
- In one branch, the agent proactively chose fastest. The policy LLM accepted
  the behavior, but the action oracle expected shortest.
- In the latest branch, the agent presented options, the user chose shortest,
  and the agent deleted Stuttgart using the shortest route. All action/tool
  checks passed, but the policy LLM rejected it for not proactively choosing
  fastest.

Fastest branch evidence:

Agent tool call:
`navigation_delete_waypoint {"waypoint_id_to_delete": "loc_stu_828398", "route_id_without_waypoint": "rll_man_par_341293"}`

Tool result:
`{"status": "SUCCESS", "result": {"waypoint_deleted": true, "new_waypoints": ["loc_man_660365", "loc_par_405686"], "new_routes": ["rll_man_par_341293"]}}`

Evaluator fields:
- `r_policy = 1.0`
- `r_tool_subset = 1.0`
- `r_tool_execution = 1.0`
- `r_actions = 0.0`

Shortest-after-options branch evidence from
`output/run_configs/20260702-194751__run_configs-coroutine_route_delete_test_cerebras_gemini_1__test-trials1-base1ids-hall0-dis1ids__gpt-oss-120b.json`:

Assistant:
`I found several direct routes from Mannheim to Paris. The fastest route is A35, B684, B808, 535.58 km, 6h 38m (route_id: rll_man_par_341293; fastest, first). The shortest route is B634, K322, L234, 534.14 km, 6h 42m (route_id: rll_man_par_416568; second, shortest). There are 1 other route options. Which route should I use for the direct drive after removing Stuttgart?`

User simulator:
`Let's go with the shortest one, please.`

Agent tool call:
`navigation_delete_waypoint {"waypoint_id_to_delete": "loc_stu_828398", "route_id_without_waypoint": "rll_man_par_416568"}`

Evaluator fields:
- `r_actions = 1.0`
- `r_actions_intermediate = 1.0`
- `r_actions_final = 1.0`
- `r_tool_subset = 1.0`
- `r_tool_execution = 1.0`
- `r_policy = 0.0`

Policy evaluator message:
`The user asked to remove a waypoint, which resulted in a new single-segment route. The agent did not proactively take the fastest route as per policy, but instead presented multiple options and asked the user to choose.`

Interpretation:
The expected behavior is internally inconsistent across evaluator components.
The action oracle and simulator branch accept shortest after route options; the
policy LLM sometimes applies policy 022 as if the fastest route must be chosen
proactively. Do not add task-specific code for this case. Keep the general
agent behavior: when deleting the only intermediate waypoint leaves a single
direct segment with multiple valid replacement routes, present options unless a
user request, stored preference, prior accepted route, or unique route already
selects one.

## Weaker Candidate

### `disambiguation_31`: toll-road disclosure expected for pre-existing unrelated segment

Classification: evaluator-policy sensitivity candidate, not confirmed.

Why it is weaker:
- Final action checks passed, but intermediate action checks did not.
- The policy failure itself resembles the organizer-confirmed unrelated-segment
  route issue, but the trace is not as clean as `disambiguation_49`.

Latest failing branch evidence:

Evaluator fields:
- `r_actions = 0.0`
- `r_actions_intermediate = 0.0`
- `r_actions_final = 1.0`
- `r_tool_subset = 1.0`
- `r_tool_execution = 1.0`
- `r_policy = 0.0`

Policy evaluator message:
`The agent failed to inform the user about the existing toll road in the first segment of the multi-stop route in the first two responses, as required by the policy. It only informed about a toll road in the final response after a new route segment was introduced.`

Relevant final action:
`navigation_delete_waypoint {"waypoint_id_to_delete": "loc_par_405686", "route_id_without_waypoint": "rll_and_mil_561986"}`

Final assistant response included:
`Paris removed. New route: K991, 804.76 km, 9h 55m, includes toll roads ...`

Interpretation:
The policy message asks for toll disclosure about a pre-existing first segment
before the user's later edit. That resembles the previously confirmed issue
where the evaluator expected unrelated existing segments to be reprocessed.
However, because this trace also has failed intermediate action checks, keep it
as a watch item rather than a confirmed eval-flaky adjustment.
