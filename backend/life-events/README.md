# life-events — shared event bus for the connector suite

One life event fans out to every connector that can act on it. The ten
connectors feel like one intelligence because they share this trigger layer.

## How it works

`events.py` holds an append-only JSONL log (`events.jsonl`) plus the fan-out map:

| Life event                | Fans out to                                              |
|---------------------------|----------------------------------------------------------|
| `move`                    | deposit-recovery, moving-concierge, unclaimed-property    |
| `flight_delayed`          | eu261-flight-comp                                         |
| `job_change`              | final-paycheck                                            |
| `bill_spike`              | bill-negotiator                                           |
| `recurring_charge_detected`| subscription-slayer                                      |
| `medical_bill_received`   | medical-bill-fighter                                      |
| `settlement_match`        | class-action-cash                                         |

No server, no dependencies — just `import events`.

## Connector integration contract

Every connector MUST:

1. **Emit** — when it detects a life event, call:
   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
   import events as life_events
   life_events.emit("move", {"state": "TX", "deposit": 1800, "move_out": "2026-07-06"})
   ```
   The return value lists which connectors were fanned out to.

2. **Receive** — expose `POST /api/life-events` accepting
   `{"event_type": "...", "payload": {...}}`. Create a draft case from the
   payload (pre-fill every field the payload provides; ask for the rest
   conversationally) and return the proactive nudge in a `user_message` field:
   a warm, speakable sentence with realistic specifics, e.g.
   "Your Texas landlord had 30 days to return your $1,800 deposit. It's day 42.
   Want me to send the demand letter? One tap."

3. **Ship `triggers.yaml`** in the connector dir:
   ```yaml
   life_event: move
   signal: "lease, mover confirmation, or USPS change-of-address email in Gmail"
   nudge: "Your Texas landlord had 30 days to return your $1,800 deposit. It's day 42. Want me to send the demand letter? One tap."
   ```

## Design rule

The golden path is **trigger → one tap → done**. If a flow needs more than a
trigger plus one confirmation, simplify until it doesn't.
