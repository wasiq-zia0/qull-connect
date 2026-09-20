# Class Action Cash

Matches your Gmail receipts against open class-action settlements, prepares
pre-filled claim packs, and earns **20% only when you confirm a payout**.
You always file the claim yourself — the connector never files, never sends
mail, never auto-charges.

> **Fee, stated plainly:** You will be charged 20% of the confirmed
> settlement payout, **only if you confirm the payout. No charge otherwise.**
> Scanning, matching, and claim packs are free.

## The one thing it does
**Trigger → one tap → done.** A receipt matches an open settlement → you get
a warm nudge with the payout and deadline → one tap gives you the pre-filled
claim pack → you file in minutes. Anything heavier (billing setup, payout
confirmation) is an advanced path the agent walks you through conversationally.

## Run

```bash
cd ~/workspace/connectors/class-action-cash
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn pydantic "mcp>=2"
.venv/bin/python run.py        # REST http://127.0.0.1:8476, MCP http://127.0.0.1:8576/mcp
```

REST alone: `.venv/bin/python -m uvicorn app:app --port 8476`
MCP alone: `.venv/bin/python mcp_server.py`

Hosting: TBD (connector runs locally for review; production hosting to be decided).

Testing without touching Stripe: `CLASS_ACTION_CASH_DRY_RUN=1` simulates all
Stripe calls. **Never invoke the Stripe CLI in tests.**

## REST endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/api/settlements` | Open settlements + `data_freshness` (`stale` flag + warning when newest `last_verified` is > 90 days old) |
| POST | `/api/scan` | `{"source":"gmail"\|"fixtures"}` — match receipts → candidate matches; emits `settlement_match` life events |
| GET | `/api/matches` | All matches found |
| POST | `/api/matches/{id}/claim-pack` | Filing pack: eligibility checklist, filing steps, pre-filled info sheet. Optional body: `{"full_name","email","address"}` |
| POST | `/api/matches/{id}/billing/setup` | `{"name","email"}` — Stripe customer + SetupIntent. Response carries the exact fee disclosure **before** the card is saved |
| POST | `/api/matches/{id}/payout-confirmed` | `{"amount": 123.45}` — 20% off-session charge against the saved card |
| POST | `/api/life-events` | Shared-bus receiver: `{"event_type":"settlement_match","payload":{...}}` → draft match + proactive nudge |

Every response includes a `user_message` field — a warm, ready-to-speak
sentence the agent says verbatim. The agent is the UI.

## MCP

Endpoint: `http://127.0.0.1:8576/mcp` (streamable-http).
Tools: `list_settlements`, `scan_receipts`, `get_matches`,
`generate_claim_pack`, `setup_billing`, `confirm_payout`, `receive_life_event`.
Same payloads as REST, including `user_message`.

## Money flow
1. Receipt matches a settlement → candidate match (free).
2. User taps → claim pack (free). User files on the official settlement site.
3. User optionally saves a card via `/billing/setup` — fee disclosure shown first.
4. User says "payout confirmed: $X" → 20% of $X charged off-session via Stripe PaymentIntent.
5. No confirmation → no charge. Card can be removed any time before a charge.

## Data freshness policy
`data/open_settlements.json` holds settlements verified open as of their
`last_verified` date (currently 2026-09-19). Settlement lists go stale:
deadlines pass and new cases open. `GET /api/settlements` returns a
`data_freshness` block — when the newest `last_verified` is more than 90 days
old, responses carry a `stale: true` flag and a warning telling the user to
confirm deadlines on the official site before filing. Refresh the JSON at
least quarterly.

## Security notes (review)
- Gmail access is strictly read-only (search/list/read only). No send, reply,
  forward, trash, or mark operations exist in this codebase.
- No raw keys anywhere. Stripe calls go through the stripe skill CLI, which
  carries the user-connected `custom.stripe-billing` credential.
- Production must use a **restricted Stripe secret key** scoped to Customers,
  SetupIntents, and PaymentIntents write only.
- All inputs validated with pydantic (types, lengths, `amount > 0`).
- All user-supplied text is treated as untrusted: `safety.sanitize()` strips
  control characters, caps length, and HTML-escapes before anything is
  rendered into claim packs or nudges. User input can never alter Gmail
  queries or tool instructions.
- `CLASS_ACTION_CASH_DRY_RUN=1` simulates Stripe in tests; the real CLI is
  never invoked during testing.

## Files
- `app.py` — FastAPI REST API
- `mcp_server.py` — MCP server (port 8576)
- `run.py` — starts both
- `core.py` — shared business logic (both interfaces)
- `db.py` — SQLite (`data/app.db`), file-backed, never in-memory
- `matcher.py` — Gmail (read-only) + fixtures receipt matching
- `billing.py` — Stripe via skill CLI, 20% fee
- `safety.py` — input sanitization
- `data/open_settlements.json` — verified-open settlements
- `data/fixtures/gmail_receipts.json` — demo receipts for tests
- `connector/manifest.json`, `connector/triggers.yaml`, `connector/TERMS.md`
