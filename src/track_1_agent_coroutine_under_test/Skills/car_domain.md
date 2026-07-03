# CAR-bench Domain Skill

Use the evaluator-provided policy as the authority for domain behavior.

Key operating rules:

- Keep spoken responses short, natural, and suitable for text-to-speech.
- Use metric units and 24h time when speaking.
- Do not assume unavailable capabilities. If the needed tool or parameter is missing from the current workspace function list, say that transparently or ask a clarification.
- Before state-changing actions, check the policy for confirmation, disambiguation, weather, climate, navigation, and lighting prerequisites.
- If a tool description starts with `REQUIRES_CONFIRMATION`, ask the user for explicit confirmation before calling it.
- For ambiguous routes, contacts, POIs, windows, seats, lights, or parameter values, disambiguate using policy, explicit request, preferences, context, then user clarification.
- For relative adjustment requests (turn down/up, lower, raise, warmer, cooler, dim, brighten, reduce, increase) that do not state a target value, do not assume a fixed step change such as "one level". Apply an explicit target only if it is given in the request, policy, or preferences; otherwise ask the user for the target value before calling the setting tool. When the request covers several zones/seats/units, apply the same resolved target to all of them.
- Before turning on window defrost, gather the full precondition set, not just part of it: check window positions with `get_vehicle_window_positions` and close any window open more than 20% with `open_close_window`, and ensure fan speed is not 0 (set it to the policy-required level). Running defrost with a window open more than 20% or with fan speed 0 violates policy.
- Treat navigation changes, vehicle setting changes, communication actions, calls, and safety-relevant controls as side effects.
- Store grounded IDs, selected options, and stable derived facts in `scratchpad["entities"]` and `scratchpad["facts"]` so follow-up turns can continue from compact authoritative state.
- If a side effect depends on choosing among options, do not choose a default unless the user or policy allows it. Apply the user's stated preference to the actual options returned by tools.
- If a tool or policy requires confirmation, first summarize the intended action and relevant parameters, then wait for explicit user confirmation before calling the side-effect tool.
- For outbound communication, confirmation should cover recipients and message content when required.
- If an evaluator tool returns an execution error, do not retry the same tool with the same grounded arguments. Retry only when you can change a specific argument based on new evidence; otherwise use another supported tool path, answer with the grounded facts already available, or explain the limitation.
- For charging questions asking for the minimum and maximum charging time while still arriving on time, compute the maximum charging time as the remaining time budget after required driving time and requested arrival buffer. For the minimum charging time, prefer `calculate_charging_time_by_soc`: derive the required target SOC from grounded range/SOC facts, then compute the charging time to reach it. Use `calculate_charging_soc_by_time` only when the user asks for the SOC or range reached after charging for a given duration.
- `get_distance_by_soc` is directional: `initial_state_of_charge` must be greater than or equal to `final_state_of_charge`. Do not use it to invert a target distance into a required SOC. Derive required SOC from grounded current range/SOC or full-range facts, then optionally validate range with `get_distance_by_soc(initial_state_of_charge=target_soc, final_state_of_charge=0)`.
- If the user explicitly asks you to place a phone call and `call_phone_by_number` is available with a grounded phone number, call it. Do not ask for extra confirmation unless the tool description or policy requires confirmation.
