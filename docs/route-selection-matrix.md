# Route Selection Matrix

This document records how route-choice policy is currently interpreted by the
coroutine agent and where wrapper behavior differs by route shape.

Core policy text in `docs/policy.md`:

- Policy 021: if a presented detailed route includes toll roads, inform the
  user.
- Policy 022: if the user asks for a multi-stop route and does not specify
  route selection, take the fastest route proactively per segment, inform the
  user, and offer more information about alternatives.
- Explicit user route choices, stored route preferences, and already accepted
  route selections override the default fastest route.

The important implementation detail is that active-route edits must be judged
from the route shape produced by the edit, not only from the route shape before
the edit.

| Request type | Route shape after request | User route signal | Expected behavior | Current wrapper/tool used | Current behavior |
| --- | --- | --- | --- | --- | --- |
| Route information only | Any route lookup | No mutation requested | Present fastest and shortest route details, mention tolls for detailed routes, and ask whether the user wants more information or wants to start one route. | `get_route_options(...)`, or `get_routes_from_start_to_destination(...)` routed through `get_routes_guarded(...)`. This is a read/normalization path, not a policy-022 mutation wrapper. Policy 021 toll wording is supported by route narration. | Mostly aligned. |
| New multi-stop navigation | Multi-stop | No explicit or stored route preference | Use fastest route per segment, then say that the fastest alternatives were selected and offer alternatives. | `set_new_navigation(...)` routed through `set_new_navigation_guarded(...)`, or multi-stop helpers such as `set_navigation_via_route_stop_with_open_poi(...)`. These are policy-touching paths for Policy 022 route selection and Policy 021 toll narration. | Mostly aligned. |
| New navigation or edit | Any | Explicit shortest, fastest, toll-free, via-road, stored preference, or accepted previous option | Use that grounded selection. Do not override it with default fastest. | `select_route(...)`, `select_route_by_user_preferences(...)`, then the relevant navigation wrapper. `select_route_by_user_preferences(...)` encodes preference resolution, not Policy 022. | Mostly aligned. |
| Delete intermediate waypoint | Route remains multi-stop | No explicit or stored route preference | Delete the waypoint and use the fastest route only for the newly created previous-to-next segment. Do not change unrelated existing segments. | `navigation_delete_waypoint(...)` routed through `navigation_delete_waypoint_guarded(...)`. This is a policy-touching wrapper for Policy 022 on the replacement segment and Policy 021 narration. | Aligned. The wrapper derives the fastest previous-to-next route when no valid grounded replacement route was supplied. |
| Delete intermediate waypoint | Deletion leaves one direct start-to-final route | No explicit or stored route preference | Delete the waypoint and use the fastest previous-to-next replacement route. | `navigation_delete_waypoint(...)` routed through `navigation_delete_waypoint_guarded(...)`. Same policy-touching wrapper as the multi-stop case: preserve explicit/stored/accepted route IDs, otherwise derive fastest. | Aligned after the 2026-07-07 update. The wrapper no longer blocks to ask for route choice merely because the post-edit route is direct. |
| Replace intermediate waypoint | Route remains multi-stop | No explicit or stored route preference | Replace the waypoint and use fastest route for each newly created segment. | `navigation_replace_one_waypoint(...)` routed through `navigation_replace_one_waypoint_guarded(...)`. This is a policy-touching wrapper for Policy 022 on new segments and Policy 021 narration. | Mostly aligned. The wrapper validates or derives the required new-segment routes. |
| Replace final destination | Route remains multi-stop | No explicit or stored route preference | Use fastest route for the newly created final segment unless another route choice is grounded. | `navigation_replace_final_destination(...)` routed through `navigation_replace_final_destination_guarded(...)`. This wrapper validates route dependency and stores Policy 021/022-style narration. | Mostly aligned for multi-stop active routes. |
| Replace final destination | Active route becomes or remains one direct segment | No explicit or stored route preference | Open question in current implementation. If the default fastest rule applies to this edit, use fastest direct replacement route. If single-destination replacement is treated as unresolved route choice, present options and wait. | `navigation_replace_final_destination(...)` routed through `navigation_replace_final_destination_guarded(...)`. Same public tool as the multi-stop case. | Currently conservative: `_single_segment_final_destination_needs_route_choice(...)` blocks when multiple direct routes exist and no explicit route selection is grounded. |
| Replace final destination with a POI | Any | POI is not uniquely selected yet | Resolve the concrete POI first. Do not route to the host city and do not apply route choice before the real destination exists. | `replace_final_destination_with_poi(...)`, `select_poi(...)`, and POI-specific navigation helpers. These are grounding helpers first, not Policy 022 route-selection helpers. | Mostly aligned. |
| Conditional destination | Branching request, such as weather-dependent route | Explicit or stored route preference may apply after branch is resolved | Resolve the branch first, then apply the grounded route preference to that branch. Do not silently default fastest when the helper says route choice is unresolved. | `navigate_by_arrival_weather(...)`, `navigate_to_poi_unless_arrival_weather(...)`, `set_navigation_conditioned_on_arrival_weather(...)`. These are branch-resolution helpers, not pure Policy 022 wrappers. | Mostly aligned, with some evaluator policy-LLM sensitivity around explicit non-fastest preferences. |

## Current Decision Point

`navigation_delete_waypoint(...)` now treats both intermediate-deletion shapes
the same way: preserve an explicit/stored/accepted grounded route ID if the
model supplies one; otherwise derive the fastest valid previous-to-next
replacement route. This avoids making the helper inspect user text while still
letting the model preserve non-default choices through explicit arguments.

The same question exists for single-segment final-destination replacement:
`navigation_replace_final_destination_guarded(...)` currently asks for route
choice instead of defaulting fastest when multiple alternatives exist.
