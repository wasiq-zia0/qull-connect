# Unclaimed Property Finder

Finds unclaimed money held by U.S. states in your name, builds per-state claim
packs you file yourself, and earns a 10% (state-capped) contingency fee on user-confirmed
recoveries — via Stripe.

The agent is the UI. **Every response carries a `user_message` field** — a
warm, ready-to-speak sentence the agent can say verbatim — alongside the
machine JSON. Every flow is demoable in a plain chat transcript.

Golden path: **trigger → one tap → done.**
1. A `move` life event arrives on the shared bus → `POST /api/life-events`
   pre-fills old/new states into a draft search and returns the proactive nudge.
2. User replies with their full legal name → `PATCH /api/searches/{id}` —
   the one tap. Claim packs are ready.
3. User files on each state's official portal. Statuses tracked per state.
4. Card saved via Stripe (with the exact fee stated first). On the user's
   recovery confirmation, up to 10% is charged off-session (state-capped).

## Run

```bash
.venv/bin/python run.py
# REST: http://127.0.0.1:8477
# MCP:  http://127.0.0.1:8577/mcp
```

Setup (first time):
```bash
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn pydantic "mcp>=2"
```

## REST endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Service status, state count |
| GET | `/api/states` | All 50 states + DC with official portal URLs |
| POST | `/api/life-events` | Receive a fanned-out life event (`{"event_type","payload"}`); a `move` creates a draft search and returns the nudge as `user_message` |
| POST | `/api/searches` | Full intake (name, email, states lived in) → per-state claim checklist |
| PATCH | `/api/searches/{id}` | Conversational step: complete a draft (the one tap) or update a search |
| GET | `/api/searches/{id}` | Search detail, per-state statuses, recoveries |
| GET | `/api/searches/{id}/claim-pack?state=XX` | Claim pack: portal link, filing steps, document checklist, pre-filled cover sheet |
| PATCH | `/api/searches/{id}/states/{abbr}` | Update claim status (`not_started`/`in_progress`/`filed`/`paid`/`denied`) |
| POST | `/api/searches/{id}/billing/setup` | Stripe customer + SetupIntent; states the exact fee **before** card save |
| POST | `/api/searches/{id}/recovery-confirmed` | `{amount}` → up to 10% off-session charge via Stripe (state-capped) |

All responses include `user_message`. Errors are JSON with `error`, `message`,
and `user_message`.

## MCP endpoint + tools

`http://127.0.0.1:8577/mcp` (streamable HTTP), served by `mcp_server.py`
(`mcp` SDK v2 `MCPServer`, `@server.tool()`, `server.run(transport="streamable-http")`).

Tools: `list_states`, `start_search`, `handle_life_event`, `complete_search`,
`get_claim_pack`, `update_claim_status`, `setup_billing`, `confirm_recovery`.
Every tool result carries `user_message`.

## Money flow

- **What:** up to 10% of confirmed recovered unclaimed funds (state finder-fee caps). Nothing else.
- **When:** only after the user confirms the recovery and the amount in chat.
- **How:** card saved earlier via Stripe SetupIntent (off-session); on
  confirmation the connector charges a Stripe PaymentIntent off-session.
- **Disclosure:** before the card is saved, the user is told verbatim:
  "You will be charged 10% of the recovered unclaimed funds (or your state's lower legal maximum), only if you
  confirm the recovery. No charge otherwise."
- **Cancellation/refund:** cancel any time before a charge by not confirming a
  recovery; fee charged in error is reviewable/refundable within 30 days.
  Full terms: `connector/TERMS.md`.

## Suite compounding (shared life-event bus)

Integrates with `~/workspace/connectors/life-events/` per its contract:
- **Receives** `POST /api/life-events` — a `move` event fans out from the bus
  (deposit-recovery, moving-concierge, and this connector all get it).
- **Emits** — intake accepts an optional `recent_move {from_state, to_state}`;
  when present, the connector emits `move` on the bus so sibling connectors can act.
- **Ships** `connector/triggers.yaml` with the watched event, signal, prefill,
  and exact nudge copy.

## Data provenance + refresh

- `data/state_claims.json`: 51 entries (50 states + DC). Portal URLs sourced
  from the FDIC's unclaimed-property state list (derived from
  missingmoney.com / NAUPA), retrieved 2026-09-19; FL and KS spot-verified
  against official state sources the same day.
- **Portal URLs and claim processes change without notice.** Re-verify links
  against unclaimed.org / missingmoney.com before any public release, and
  re-check whenever a user reports a dead link. Each entry carries
  `last_verified`; states flagged with stale or redirect links carry a `note`.
- Regenerate: `python3 data/build_states.py`.

## PII minimization

- Intake collects: full legal name, prior names, email, states of residence.
- Date of birth is collected **only** with explicit `dob_consent=true` (some
  states ask for it at filing); without consent the API refuses to store it.
- **SSN is never collected or stored** — states may ask for it at filing, but
  the connector never touches it.
- Stored data is used only to build claim packs and track status.

## Security notes (review-ready)

- No secrets in code or logs. Stripe auth rides the `custom.stripe-billing`
  credential through `~/workspace/skills/stripe/bin/stripe`; deployment must
  use a least-privilege restricted Stripe key (Customers, SetupIntents, and
  PaymentIntents write-only).
- All inputs validated via pydantic (length caps, state-abbr allowlist, date
  and amount bounds).
- All user-supplied text is treated as untrusted data: length-capped, stripped
  of control characters, and HTML-escaped before being rendered into claim
  packs, nudges, or tool outputs. User input can never alter queries or
  instructions.
- The connector never files claims and never sends personal data to any state
  or third party.

## Storage

SQLite at `data/app.db` (never in-memory). Tables: `searches`, `search_states`,
`recoveries`.

## Hosting

TBD.
