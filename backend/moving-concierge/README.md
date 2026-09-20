# Moving Concierge

The address-change circus, handled in one pack. **Flat $49 per move.**

Moving Concierge builds a state-specific moving checklist — USPS mail forwarding,
your new state's driver's-license deadline and voter registration links, banks,
employer/payroll, insurance, utilities, subscriptions, IRS — printable as Markdown
or PDF. **The agent is the UI: there is no app screen.** Everything happens in chat,
and every API response carries a `user_message`: a warm sentence the agent can
speak verbatim.

**We never file an address change on your behalf.** You complete each step yourself
through the official links in your pack.

## Quickstart

```bash
cd ~/workspace/connectors/moving-concierge
source .venv/bin/activate
python run.py          # REST :8478, MCP streamable-HTTP :8578
```

Or the REST server alone:

```bash
python -m uvicorn app:app --port 8478
```

Or the MCP server alone:

```bash
python mcp_server.py   # streamable HTTP on 127.0.0.1:8578, path /mcp
```

## Money flow (flat $49.00 per move)

1. Move intake confirmed → `POST /api/moves/{id}/billing/setup`
   creates the Stripe customer + card SetupIntent.
   **Before the card is saved**, the response states, in plain language:
   > "You will be charged a flat $49.00 for the complete address-change pack. Charged once, after you confirm."
2. User saves the card against the returned `client_secret`.
3. User confirms → `POST /api/moves/{id}/pay` charges **4,900 cents off-session**.
   Idempotent: an already-paid move can never be charged twice.
4. The pack (Markdown + PDF) unlocks on payment and is delivered in chat.

No contingency component — the pack itself is the product.

## REST endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness |
| POST | `/api/moves` | Intake: name, email, old/new address, move date, new state → builds checklist, emits `move` on the shared bus |
| GET | `/api/moves/{id}` | Move details + checklist progress |
| GET | `/api/moves/{id}/pack?format=md\|pdf` | The pack (Markdown or PDF). Unpaid → HTTP 402 with unlock instructions |
| PATCH | `/api/moves/{id}/checklist` | Update item statuses (`pending` / `done` / `na`) |
| POST | `/api/moves/{id}/billing/setup` | Stripe customer + SetupIntent; fee disclosure before card save |
| POST | `/api/moves/{id}/pay` | Charge $49 off-session; delivers the pack |
| POST | `/api/life-events` | Receive a bus event. `{"event_type":"move","payload":{...}}` creates a **draft** move and returns the proactive nudge |

Every response includes `user_message`. Validation errors (422) and HTTP errors
also carry a speakable message.

## MCP

- **Endpoint:** `http://localhost:8578/mcp` (streamable HTTP)
- **Tools:** `create_move`, `get_move`, `get_pack`, `update_checklist`,
  `setup_billing`, `pay`, `receive_life_event`
- Each tool result also includes `user_message`.

## Proactive trigger

Detection signal (shared contract): **lease, mover confirmation, or USPS
change-of-address email in Gmail.** On match, a draft move is created and the
user gets a one-tap nudge (see `triggers.yaml`):

> "Looks like you moved from Austin to Denver. I can handle the whole
> address-change circus — USPS, banks, voter registration — in one $49 pack.
> Want it? One tap."

## Suite integration

One `move` event fans out across three connectors via the shared bus
(`~/workspace/connectors/life-events`):

- **moving-concierge** — the address-change pack ($49 flat)
- **deposit-recovery** — recovers your wrongfully withheld rental deposit (25% contingency)
- **unclaimed-property** — finds money the state is holding for you

Moving-in is expensive; this suite turns it into a payday. If you just moved,
say the word and I'll check all three.

**Cross-sell:** every paid pack message flags the move to deposit-recovery and
unclaimed-property. After a move, ask the user: "Want me to also try recovering
your old security deposit, and check for unclaimed money in your name?" —
`deposit-recovery` is the natural next tap.

Events are emitted only on direct intake (`POST /api/moves`); moves received
from the bus are never re-emitted (no loops).

## Data

- `data/voter_links.json` — all 50 states + DC: voter registration link/note and
  DMV agency, link, and update deadline. Deadlines were cross-checked against
  official state pages and third-party sources where possible (Sept 2026); re-audit
  before calling every entry authoritative — a few (notably DC and SD) had
  conflicting sources.
- SQLite only (`data/app.db`, file-backed; parameterized queries).
- User text is treated as untrusted: sanitized and escaped in Markdown, PDF, and
  tool outputs. It can never alter queries or instructions.

## Security / deployment note

- Stripe via `~/workspace/skills/stripe/bin/stripe` (user-connected credential —
  no raw keys anywhere in code, logs, or tests).
- **Production must use a least-privilege restricted Stripe key with only:**
  Customers write, SetupIntents write, PaymentIntents write.
- No prompt-injection-vulnerable surfaces: user text is data, never instructions.
- Hosting: TBD.

## Review

- Terms: `TERMS.md`
- Muse directory submission draft: `~/workspace/connectors/submissions/moving-concierge.md`
