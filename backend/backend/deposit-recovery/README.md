# Deposit Recovery — Muse connector

Recovers wrongfully withheld rental security deposits. The agent detects a
move, diaries the state's legal return deadline, generates a statute-citing
demand letter the day the deadline passes, and charges a 25% contingency fee
only when the tenant confirms the deposit came back.

**The agent is the UI.** Every API response carries a `user_message` field — a
warm, ready-to-speak sentence the agent can say verbatim — alongside the
machine JSON. Every flow is demoable inside a plain chat transcript. Golden
path: trigger → one tap → done.

## What it does

1. **Detects the move** — via the shared life-event bus (`POST /api/life-events`
   receives the `move` fan-out) or a directly opened case.
2. **Diaries the deadline** — 50 states + DC table; the clock runs from
   move-out, except TX, CT, MN, WY where it runs from the date the tenant gave
   the landlord a forwarding address (that date is *required* there — the API
   returns 400 without it, never falls back to move-out).
3. **Generates the demand letter** — statute-citing PDF, only once the deadline
   has passed.
4. **Charges 25% on confirmed recovery** — and only then. No recovery, no charge.

Proactive triggers live in `triggers.yaml` (life event, detection signal, exact
nudge copy). Suite integration follows the contract in
`~/workspace/connectors/life-events/README.md`: this connector emits `move`
events it detects and receives the bus fan-out.

## Run it

```bash
cd ~/workspace/deposit-recovery
.venv/bin/python run.py            # REST :8471 + MCP :8571
# overrides:
.venv/bin/python run.py --rest-port 8471 --mcp-port 8571 --host 127.0.0.1
# or: DEPOSIT_REST_PORT=8471 DEPOSIT_MCP_PORT=8571 .venv/bin/python run.py
```

REST only: `.venv/bin/uvicorn app:app --port 8471`
MCP only: `.venv/bin/python mcp_server.py --port 8571`

Open http://127.0.0.1:8471 for the thin demo page.

## REST API (http://127.0.0.1:8471)

| Method | Path | What |
|---|---|---|
| GET | `/api/state-laws` | all states: deadline, statute |
| GET | `/api/state-laws/{abbr}` | one state's law |
| POST | `/api/cases` | open a case (400 if `forwarding_date` missing in TX/CT/MN/WY) |
| GET | `/api/cases/{id}` | status: deadline, days remaining/overdue, max recovery, next action |
| POST | `/api/cases/{id}/demand-letter` | demand-letter PDF as base64 JSON — only when overdue (400 otherwise) |
| POST | `/api/cases/{id}/billing/setup` | Stripe customer + SetupIntent `client_secret` for the card |
| POST | `/api/cases/{id}/recovery-confirmed` | `{"amount_recovered": N}` → charges 25% off-session |
| POST | `/api/life-events` | receive bus fan-out `{"event_type","payload"}` → case or draft + nudge |
| GET | `/api/drafts/{id}` | inspect a draft case |
| POST | `/api/drafts/{id}/promote` | merge conversationally collected fields → open the case |

Every response includes `user_message`. Errors too (`{"detail", "user_message"}`).

## MCP (http://127.0.0.1:8571/mcp, streamable HTTP)

Tools (same rules as REST, `user_message` included):

- `create_case` — open a case; `forwarding_date` required in TX/CT/MN/WY
- `get_case_status` — deadline, days remaining/overdue, max recovery, next action
- `generate_demand_letter` — PDF (base64) once overdue; error explains the wait
- `get_state_law` — deadline, statute, penalty multiple, forwarding-date requirement

## Money flow

1. Case opened → `POST /api/cases/{id}/billing/setup` creates the Stripe
   customer and returns a SetupIntent `client_secret`. The tenant saves a card.
   **No charge.** The response states, before the card is saved: *"You will be
   charged 25% of the recovered deposit, only if you confirm the recovery. No
   charge otherwise."*
2. Deadline passes → demand letter (template, not legal advice).
3. Tenant confirms the deposit landed → `POST /api/cases/{id}/recovery-confirmed`
   charges 25% of the confirmed amount off-session against the saved card.
   Example: $1,800 recovered → $450.00 fee.
4. No recovery confirmed → $0, forever. Cancel any time before confirming.

Full terms: `TERMS.md`.

## Data

- `data/state_laws.json` — all 50 states + DC: return deadline, basis
  (move-out vs forwarding address), statute, penalty multiples where verified.
  Deadlines from Nolo's 50-state chart; penalties from Apartments.com's
  rental-manager chart (both retrieved Sep 2026).
- `data/app.db` — SQLite case + draft storage (created on startup). Cases
  persist across restarts.
- `triggers.yaml` — proactive trigger definitions and exact nudge copy.

## Security

- No secrets in code or logs. Stripe goes through the `stripe` workspace skill,
  which uses a **least-privilege restricted key (Customers / SetupIntents /
  PaymentIntents write only)** — no raw keys anywhere.
- All inputs validated with pydantic (length caps, state normalization,
  positive deposit).
- All SQL is parameterized; user input never touches query structure.
- All user-supplied text is treated as untrusted data: control characters are
  stripped before rendering into letters/PDFs, and user input is never
  interpreted as markup or instructions.
- The demand letter is labeled **template automation, NOT legal advice**, in
  the letter itself and in every surface that presents it.

## Disclaimer

**Template automation, NOT legal advice.** Statutes change; verify the cited
statute against current law in your state before sending anything. See
`TERMS.md`.

## Status

Private VM currently — **public hosting is TBD**. The connector is built to
Meta's connector review bar (functional end-to-end, security, honest fee
disclosure, legal labels); submission notes live in
`~/workspace/connectors/submissions/deposit-recovery.md`.
