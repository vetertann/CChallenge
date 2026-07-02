# Email Skill Addendum

Use this addendum only for tasks whose current request is email-related.

Email side effects are final actions. Do not call `send_email(...)` until the
recipient and every fact requested for the message are grounded, unless the
current task is specifically about checking whether email sending is available.
The runtime handles confirmation after `send_email(...)`; do not ask a manual
"should I send it?" question before calling the wrapper.

General rules:

- Keep recipient resolution separate from message content. Resolve the contact
  ID first, then call `get_contact_details(..., required_fields=["email"],
  role="email_recipient")`, and use the returned email address.
- A draft is not complete just because the route or contact is known. If the
  user asked to include travel, calendar, route, charging, contact, weather, or
  POI details, call the relevant read/search/calculation functions first and
  write the email from those returned facts.
- If new tool results change the draft after a confirmation prompt, call
  `send_email(...)` again with the updated final `content_message`. The old
  confirmation covers only the old body.
- Do not set navigation just to prepare an email. For planning-only email work,
  keep routes as grounded route facts. Search for charging/POI stops only after
  the stop strategy is resolved by the user, policy, stored preference, or a
  previous grounded selection.
- If `send_email` or a required recipient field is unavailable, report that
  limitation through the wrapper/helper path instead of continuing to perfect
  the message.

For route or travel emails:

- Ground the destination ID, route options, selected route, distance, duration,
  and toll/route-option facts before confirmation.
- If the route is for an EV trip and current range may matter, read
  `get_charging_specs_and_status()` before the first email confirmation. Route
  distance alone is not enough to decide whether charging should be mentioned.
- If route distance is greater than current remaining range, do not treat that
  as permission to invent a charging strategy. Do not choose current-location
  charging, along-route charging, a route kilometer, a station, a plug, or a
  target SOC yourself.
- If charging is needed and the strategy is unresolved, ask one short question
  before email confirmation, or send only if the user explicitly accepts an
  email that says charging still needs to be planned. A good question is:
  "The route is longer than the current range. Do you want me to include a
  charging plan before I send the email?"
- If the user or follow-up resolves a charging plan, ground the station/plug,
  charging time, and post-charge range before sending a message that claims the
  plan is complete or says whether another stop is needed.
- If charging strategy is still unresolved, a valid email may say that charging
  still needs to be planned. It should not claim a specific station, charge
  duration, or number of later stops without tool results.

Charging extraction patterns:

```python
charging = get_charging_specs_and_status()
remaining_range_km = charging["remaining_range_km"]
current_soc = charging["state_of_charge"]

charge_result = calculate_charging_time_by_soc(
    charging_station_id=plug["charging_station_id"],
    charging_station_plug_id=plug["charging_station_plug_id"],
    start_state_of_charge=current_soc,
    target_state_of_charge=target_soc,
)
charge_minutes = first_number_value(charge_result)

post_charge_range = get_distance_by_soc_value(
    initial_state_of_charge=target_soc,
    final_state_of_charge=0,
)
post_charge_range_km = post_charge_range["distance_km"]
```

Do not extract charging time with
`result_value(charge_result).get("minutes")`. The native evaluator key can be
dynamic, such as `time_from_70.0_until_100.0_percent_soc`; the wrapper envelope
and `first_number_value(...)` expose the stable number.

Example route-email flow:

```python
recipient = get_contact_details(
    [recipient_id],
    required_fields=["email"],
    role="email_recipient",
)["first"]

route_options = get_route_options(start_id=policy_location_id(), destination_id=destination_id)
route = select_route(route_options["routes"], prefer="fastest")["route"]

charging = get_charging_specs_and_status()
email_facts = [
    f"Fastest route: {route['name_via']}, {route['distance_km']:.1f} km, "
    f"{route['duration_hours']}h {route['duration_minutes']}m.",
    f"Current battery: {charging['state_of_charge']:.0f}% with "
    f"{charging['remaining_range_km']:.0f} km remaining range.",
]

if route["distance_km"] > charging["remaining_range_km"]:
    respond(
        "The route is longer than the current range. Do you want me to include "
        "a charging plan before I send the email?"
    )
    stop_after_response()

send_email(
    email_addresses=[recipient["email"]],
    content_message="\n".join(email_facts),
)
```
