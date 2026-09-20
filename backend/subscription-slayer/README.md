# Subscription Slayer

Finds every recurring subscription hiding in your Gmail receipts, walks you
through cancelling the ones you don't want, and tracks what you save.
The agent is the UI — every response carries a `user_message` field: a warm,
ready-to-speak sentence the agent can say verbatim inside a plain chat.

**One thing, done magically:** trigger → one tap → done. A proactive nudge
("You've paid $14.99 to StreamBox for 11 months... that's $165 a year. Want me
to walk you through cancelling? One tap.") is the product; reactive endpoints
are just the machinery.

## Money flow

- **Fee: $10 flat per completed cancellation you confirm.** Mark
  subscriptions cancelled, confirm, and each confirmed cancellation costs
  $10 — nothing if you confirm nothing.
- A charge happens **only** when you explicitly confirm your savings via
  `POST /api/savings/confirmed`. No confirmation, no charge — ever.
- Saving a card (`POST /api/billing/setup`) is free and creates no
  obligation. The honest fee disclosure is returned in that response before
  the user saves a card:

  > "You will be charged $10 for each subscription cancellation you confirm
  > — charged once, after you confirm. No charge otherwise."

- Stripe is called through the skill CLI
  (`~/workspace/skills/stripe/bin/stripe`), which carries the user-connected
  `custom.stripe-billing` credential. No raw keys anywhere in this code.
  Deployment should use a least-privilege restricted Stripe key
  (Customers / SetupIntents / PaymentIntents write-only).

## REST API (port 8473)

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/api/scan` | Scan Gmail receipts → detect recurring subscriptions (`{"source":"gmail"\|"fixtures","max":N}`) |
| GET | `/api/subscriptions[?status=]` | List tracked subscriptions |
| PATCH | `/api/subscriptions/{id}` | `status`, `savings_monthly`, `notes` |
| GET | `/api/subscriptions/{id}/cancel-pack` | Curated cancel steps + direct link |
| POST | `/api/billing/setup` | Stripe customer + SetupIntent (no charge; returns fee disclosure) |
| POST | `/api/savings/confirmed` | $10 per confirmed cancellation, charge off-session |
| POST | `/api/life-events` | Shared-bus receiver for `recurring_charge_detected` → draft case + nudge |

Every response includes `user_message`. Validation errors are 422 with clear
messages; unknown IDs are 404; charging without billing on file or with
zero/unconfirmed savings returns 409/422 — never a surprise charge.

## MCP endpoint (port 8573, streamable HTTP)

Six tools, mirroring the REST actions, each returning machine JSON plus
`user_message`: `scan_subscriptions`, `list_subscriptions`,
`update_subscription`, `get_cancel_pack`, `setup_billing`, `confirm_savings`.

## Event bus (suite compounding)

Integrates with the shared bus at `~/workspace/connectors/life-events/`:

- **Emit:** every scan that finds a recurring subscription emits
  `recurring_charge_detected` with merchant, amount, frequency, occurrences.
- **Receive:** `POST /api/life-events` handles the fan-out — creates or
  refreshes a draft subscription and returns the proactive nudge.
- `connector/triggers.yaml` declares the life event, the Gmail signal, and
  the exact nudge copy.

## Gmail: read-only, always

All Gmail access goes through the `gmail` skill's read/search commands
(`+triage`, `+read`). The connector never sends, replies, forwards, labels,
archives, or deletes anything. Production needs the user's Gmail connected
via Muse's Custom connector flow; reviewers can run the full pipeline
deterministically with `{"source":"fixtures"}`.

## Run

```bash
cd ~/workspace/connectors/subscription-slayer
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn pydantic "mcp>=2"
.venv/bin/python run.py        # REST on :8473, MCP on :8573
```

Data lives in `data/app.db` (SQLite, file-based). Sample receipts in
`data/fixtures/`. Curated cancellation guides for ~48 merchants in
`cancel_guides.json`; unknown merchants get the generic fallback playbook.

## Hosting

Hosting TBD — the service is designed to run as a small always-on process
(`run.py`) behind the Custom connector flow; the SQLite DB and JSONL event
log move with the deployment.

## Legal

See `connector/TERMS.md`: plain-language terms, exact fee terms,
cancellation/refund policy (cancel any time before a charge; fee only on
confirmed savings), support-contact and governing-law placeholders, and the
data-use note.
