# EU261 Flight Comp

Turns EU flight delays into money. You land late; the agent notices, checks the
flight against **Regulation (EC) No 261/2004**, and drafts your claim letter —
**€250 / €400 / €600** depending on distance. The agent is the UI: everything
happens in chat, and every response includes a warm, speakable `user_message`.

> **Template automation, NOT legal advice.** The connector prepares the claim
> pack; **you** file it with the airline. Every pack is labeled accordingly.

## The golden path

**Trigger → one tap → done.**

1. A `flight_delayed` life event arrives (delay email, boarding pass + schedule change).
2. The agent nudges you: *"Your flight AF347 to Paris landed 4 hours late. EU law
   says that's €600 back in your pocket. Want me to draft the claim letter? One tap."*
3. You say yes → the claim letter PDF is generated. You sign and send it.

## REST API

Base: `http://127.0.0.1:8472`

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/api/life-events` | Receive a fanned-out life event (`flight_delayed`); creates a draft claim, returns the proactive nudge as `user_message` |
| POST | `/api/claims` | Intake a disruption → eligibility verdict + tier + amount |
| GET | `/api/claims/{id}` | Verdict for a stored claim |
| POST | `/api/claims/{id}/claim-pack` | Claim letter PDF (only if eligible) |
| POST | `/api/claims/{id}/billing/setup` | Stripe customer + SetupIntent; card saved, **no charge** |
| POST | `/api/claims/{id}/payout-confirmed` | User confirms payout → 30% off-session fee charge |

Every response includes `user_message` — a sentence the agent can say verbatim.

## MCP endpoint + tools

MCP (streamable-http): `http://127.0.0.1:8572/mcp`

Tools: `check_eligibility`, `get_claim`, `generate_claim_pack`,
`setup_billing`, `confirm_payout`, `handle_life_event`.

## Money flow

- **What:** 30% contingency fee on the confirmed EU261 payout.
- **When:** only after the user confirms the airline paid (POST `/payout-confirmed`
  with `amount_eur`). Off-session charge against the saved card via Stripe.
- **Before the card is saved**, the billing/setup response states the exact deal
  in plain language: *"You will be charged 30% of the confirmed EU261 payout,
  only if you confirm the payout. No charge otherwise."*
- **Cancel anytime** before confirming a payout; nothing is ever charged.
- Full terms: `connector/TERMS.md`.

## Security

- All inputs validated with pydantic; all user-supplied text is treated as
  untrusted data and HTML-escaped before PDF rendering (no markup/query
  injection; no prompt-injection-vulnerable surfaces).
- No secrets in code or logs. Stripe is accessed only via the skill CLI, which
  carries the connected credential.
- Production deployment uses a **least-privilege restricted Stripe key**
  (Customers, SetupIntents, and PaymentIntents write-only) — the CLI cannot
  read balances, payouts, or other account data.

## Run

```bash
cd ~/workspace/connectors/eu261-flight-comp
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn pydantic "mcp>=2" reportlab
.venv/bin/python run.py        # REST :8472 + MCP :8572
```

Public hosting: TBD (currently runs locally; the connector directory listing
points at the hosted URLs once deployed).

## Event bus

Part of the connector suite's shared life-events bus
(`~/workspace/connectors/life-events/`): emits `flight_delayed` when a
qualifying disruption is intaked, receives fan-out via `POST /api/life-events`,
and ships `connector/triggers.yaml`.

## Data

`data/airports.csv` — 279 major world airports with lat/lon from the OpenFlights
dataset, plus an EU/EEA flag. Great-circle distance is computed with the
haversine formula; if an airport code is unknown, the caller supplies
`distance_km` manually.
