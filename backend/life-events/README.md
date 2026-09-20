# Life-event routing suggestions

`events.py` provides a mapping from an explicitly supplied event to potentially
relevant connectors. It does not monitor a mailbox, schedule work, send requests,
persist customer payloads, or share a global history.

| Event | Suggested connectors |
|---|---|
| `move` | Deposit Recovery, Moving Concierge, Found Money |
| `flight_delayed` | FlightPay |
| `job_change` | Final Paycheck Recovery, MatchMax |
| `bill_spike` | BillCut |
| `recurring_charge_detected` | Subscription Slayer |
| `medical_bill_received` | Medical Bill Fighter |
| `settlement_match` | Class Action Cash |

`emit(event_type, payload, source)` returns an event identifier and suggested
connector slugs with `dispatched: false`, an empty `fanned_out_to` list and
`user_authorization_required: true`. `recent()` returns an empty list because
there is no shared event log.

An integrator may offer the relevant service to the user. Only after appropriate
user authorization should it send the required event data to the destination's
`POST /api/life-events`, authenticated with that user's scoped bearer credential.
The destination derives ownership from authentication, never the payload.

A suggestion is not permission to forward rental, employment, medical, receipt,
or financial details to another connector. Do not promise automatic mailbox
watching, reminders, dispatch, cross-service action, or letter sending. Historical
`triggers.yaml` files describe product ideas; they are not deployed schedulers.
See the per-service README for the current supported draft/intake behavior.
