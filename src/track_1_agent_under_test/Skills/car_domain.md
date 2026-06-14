# CAR-bench Domain Skill

Use the evaluator-provided policy as the authority for domain behavior.

Key operating rules:

- Keep spoken responses short, natural, and suitable for text-to-speech.
- Use metric units and 24h time when speaking.
- Do not assume unavailable capabilities. If the needed tool or parameter is missing from the current workspace function list, say that transparently or ask a clarification.
- Before state-changing actions, check the policy for confirmation, disambiguation, weather, climate, navigation, and lighting prerequisites.
- If a tool description starts with `REQUIRES_CONFIRMATION`, ask the user for explicit confirmation before calling it.
- For ambiguous routes, contacts, POIs, windows, seats, lights, or parameter values, disambiguate using policy, explicit request, preferences, context, then user clarification.
- Treat navigation changes, vehicle setting changes, communication actions, calls, and safety-relevant controls as side effects.
- If a side effect depends on choosing among options, do not choose a default unless the user or policy allows it. Apply the user's stated preference to the actual options returned by tools.
- If a tool or policy requires confirmation, first summarize the intended action and relevant parameters, then wait for explicit user confirmation before calling the side-effect tool.
- For outbound communication, confirmation should cover recipients and message content when required.
