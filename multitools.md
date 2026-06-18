# CAR-bench Workspace Multitools

This document tracks planned Python workspace helpers for the coroutine agent. These helpers wrap evaluator tools, but the evaluator remains the only executor of CAR-bench tool calls.

## Implemented Or Safe One-Shot Helpers

These helpers do not need a live user selection or confirmation inside the helper. They either complete deterministically, return structured status, or emit a final limitation response when required evaluator capabilities are missing.

| Helper | Status | Why it is safe as a one-shot helper |
|---|---|---|
| `defrost_front_window()` | Implemented | Front defrost has deterministic policy actions under 010/011. If required controls are missing, the helper emits a policy-safe limitation response. |
| `get_distance_by_soc_value(initial_state_of_charge, final_state_of_charge=0)` | Implemented | Read-only normalizer for dynamic `distance_*` output keys. |
| `set_air_conditioning_on_safe()` | Implemented | AC-on policy 011 is deterministic: check climate/window state, close windows over 20%, set fan speed to 1 if currently 0, then turn AC on. Missing required tool surface becomes a final limitation response. |
| `set_climate_temperature_safe(seat_zone, temperature)` | Implemented | Temperature setting is deterministic when seat zone and temperature are explicit. For single-zone changes, the helper checks the opposite zone and informs the user if the resulting difference is over 3 degrees Celsius, per policy 012. |
| `get_route_options(start_id, destination_id)` | Implemented | Read-only route-result normalizer. It returns routes as a stable list with `route_id`, aliases, duration totals, and toll metadata. It does not choose a route by itself. |
| `select_route(routes, route_id=None, alias=None, name_via=None, prefer=None)` | Implemented | Pure selector over already retrieved routes. It only succeeds on a unique match; otherwise it returns `AMBIGUOUS` or `NOT_FOUND`. |
| `get_preferred_ambient_light_color()` | Implemented | Read-only preference extraction. It only returns a unique valid color when the preference data supports one; otherwise it returns `AMBIGUOUS` or `NOT_FOUND`. |

## Requires User Interaction Or Two-Stage State

These should not be implemented as unconditional one-shot action helpers. They need a pending-action state machine or explicit user follow-up before side effects.

| Planned helper | Why it requires interaction |
|---|---|
| `send_email_confirmed(...)` | `send_email` starts with `REQUIRES_CONFIRMATION`; the agent must list recipients/content and wait for explicit yes before sending. |
| `open_window_safe(window, percentage)` | If requested opening is over 25% while AC is on, policy 007 requires warning and confirmation. If window or percentage is unspecified, disambiguation requires clarification. |
| `open_sunroof_safe(percentage)` | Opening sunroof requires sunshade handling and weather check. Unsafe weather requires explicit yes before action. Missing/unspecified percentage may require clarification. |
| `set_fog_lights_safe(on=True)` | Activating fog lights requires weather check; unsafe weather requires explicit yes. It also has deterministic low/high beam side effects only after confirmation is satisfied. |
| `set_ambient_lights_from_preference_or_clarify()` | If preferences yield exactly one valid color, action is safe. If no preference or multiple colors remain, the agent must ask the user instead of choosing. Current implementation exposes only the read-only color extractor. |
| `search_poi_and_offer_navigation(...)` | POI policy requires presenting found POIs and asking which POI the user wants directions to when multiple valid POIs exist. |
| `present_route_options_and_wait(...)` | Route policy requires presenting fastest/shortest and asking whether to start one or show more alternatives. |
| `replace_final_destination_by_city(...)` | Safe only when the user already selected a unique route or route selector. If multiple route alternatives remain, the agent must ask. |
| `call_phone_by_contact(...)` | Safe only after contact identity resolves uniquely. Multiple contact matches or missing first/last name require clarification. Calling ends the conversation, so guessing is unacceptable. |

## Implementation Rule

Action helpers may emit a predefined limitation response when the current task's tool surface is missing required evaluator tools. They must not silently claim success, invent unavailable tools, or choose among multiple valid user-facing options.
