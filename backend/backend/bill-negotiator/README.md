# Bill Negotiator

A Muse connector that helps users lower their internet/cable/phone bills and earns a **35% fee on user-confirmed documented savings**.

The agent is the UI — there is no app screen. **Every API response and MCP tool output includes a `user_message` field**: a warm, ready-to-speak sentence the agent can say verbatim, so every flow is fully demoable inside a plain chat transcript.

## What it does

1. **Trigger** — a `bill_spike` life event (bill email shows an increase vs prior months, or a promo expiry approaches) creates a draft case and returns the proactive nudge: *"Your internet bill jumped from $70 to $110 — your promo expired. People are getting it back down to $75 with one call. Want the script? One tap."* Trigger → one tap → done.
2. **Intake** — or the user just says their provider, service type, and current bill.
3. **Negotiation script pack** — per-provider call script + chat script + talking points (researched: retention/loyalty department, competitor pricing mention, promotion ask, fee waivers). **The user makes the call/chat — the connector never contacts providers.**
4. **Outcome tracking** — user reports the result in one sentence: new monthly bill and how many months the rate is locked, or "no success". Savings = (old − new) × months locked (months capped at 12).
5. **Billing** — Stripe customer + SetupIntent to save a card. Before the card is
   saved, the user is told in plain language:

   > "You will be charged 35% of your documented bill savings, only if you confirm the new lower bill. No charge otherwise."

   The **35% fee is charged off-session ONLY after the user confirms documented savings**. No savings → no fee.

Scripts are negotiation guidance only, not legal or financial advice.

## REST API (port 8474)

| Method & path | Purpose |
|---|---|
| `GET /health` | Health check |
| `GET /api/providers` | Providers with researched scripts |
| `POST /api/cases` | Intake: `provider, service_type, current_monthly_bill, promo_end_date?, account_tenure_months?, user_name?` |
| `GET /api/cases/{id}` | Case detail incl. outcome/savings/billing status |
| `GET /api/cases/{id}/script` | Negotiation script pack (call + chat scripts) |
| `POST /api/cases/{id}/outcome` | `{success, new_monthly_bill?, months_locked?}` → computes documented savings |
| `POST /api/cases/{id}/billing/setup` | Stripe customer + SetupIntent `client_secret` (collect card client-side; fee disclosed in plain language first) |
| `POST /api/cases/{id}/savings-confirmed` | Charges 35% of documented savings off-session |
| `POST /api/life-events` | Receives shared life-events bus fan-out; `bill_spike` creates a draft case and returns the proactive nudge + ready script pack |

Example: `$120 → $85` locked 12 months = `$420` documented savings → fee `$147.00` (35%).

## Proactive triggers (shared life-events bus)

This connector integrates with the suite-wide event bus at `~/workspace/connectors/life-events/`:

- **Emits:** `bill_spike` when intake includes a `prior_monthly_bill` lower than the current bill (logged to the shared `events.jsonl`).
- **Receives:** `POST /api/life-events` accepts `{"event_type","payload"}`; a `bill_spike` payload pre-fills a draft case and returns the nudge as `user_message` with the script pack attached.
- **Triggers:** see `connector/triggers.yaml` — the watched signal and the exact nudge copy.

## MCP endpoint (port 8574, streamable-HTTP)

Tools: `create_negotiation_case`, `get_negotiation_script`, `report_negotiation_outcome`, `setup_negotiation_billing`, `charge_negotiation_fee`, `list_supported_providers`. See `connector/manifest.json` for the connector descriptor.

## Money flow

- Stripe via the `stripe` skill CLI (`~/workspace/skills/stripe/bin/stripe`), which carries the user-connected `custom.stripe-billing` credential. **No raw keys anywhere.**
- Deployment uses a least-privilege restricted Stripe key: Customers, SetupIntents, and PaymentIntents write only.
- 35% of **documented** savings: `(old_monthly_bill − new_monthly_bill) × months_locked` (cap 12).
- Charge happens only on `POST /api/cases/{id}/savings-confirmed` (or the `charge_negotiation_fee` MCP tool), after the user confirms the outcome. Off-session charge against the card saved via SetupIntent.
- A "no success" outcome yields zero savings and zero fee.

## Run

```bash
cd ~/workspace/connectors/bill-negotiator
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py        # REST on :8474, MCP on :8574
```

## Storage

SQLite at `data/app.db` (file-backed; cases carry `stripe_customer_id`, `billing_status`, `savings`, `fee_cents`).

## Hosting

TBD.
