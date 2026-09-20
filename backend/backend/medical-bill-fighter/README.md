# Medical Bill Fighter

Spots errors in medical bills and EOBs, builds dispute letter packs, and
earns **25% of the bill reduction the user confirms** — charged only then,
via Stripe off-session.

> **Template automation — NOT legal advice. Review before sending.**
> Findings never assert legal conclusions; balance-billing flags use
> "may be protected — verify" language.

## Run

```bash
cd ~/workspace/connectors/medical-bill-fighter
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn pydantic "mcp>=2" reportlab
.venv/bin/python run.py        # REST on :8480, MCP (streamable-http) on :8580
# or individually:
.venv/bin/python run.py --rest-only
.venv/bin/python run.py --mcp-only
```

Data: SQLite at `data/app.db` (file-based, never in-memory).

## Golden path (agent-native)

The agent is the UI. Every response carries a `user_message` — a warm,
ready-to-speak sentence the agent can say verbatim. The bill check is a
conversation, one question at a time:

1. `POST /api/life-events` `{"event_type":"medical_bill_received","payload":{"provider_name":"City General","total":4200}}`
   → draft case + proactive nudge as `user_message`.
2. User says yes → `POST /api/cases/draft` (or reuse the draft case) → first question.
3. `POST /api/cases/{id}/answer` per answer → next question, until detection runs and the payoff message returns.
4. "Want me to draft the dispute letter? One tap." → `GET /api/cases/{id}/pack?type=dispute` (PDF).
5. User reports a reduction → `POST /api/cases/{id}/outcome` → fee computed, honest disclosure spoken.
6. `POST /api/cases/{id}/billing/setup` → Stripe customer + SetupIntent (fee terms stated **before** card save).
7. `POST /api/cases/{id}/reduction-confirmed` → 25% off-session charge.

Agents that already have structured data can skip the conversation with
`POST /api/cases`.

## REST endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness |
| POST | `/api/life-events` | receive fan-out; creates draft case, returns nudge |
| POST | `/api/cases/draft` | start conversational bill check |
| POST | `/api/cases/{id}/answer` | answer one question; advances to detection |
| POST | `/api/cases` | direct structured intake (runs detector) |
| GET | `/api/cases/{id}` | case + findings |
| GET | `/api/cases/{id}/pack?type=` | `dispute\|itemized\|assistance\|negotiate` → PDF |
| GET | `/api/packs` | pack descriptions |
| POST | `/api/cases/{id}/outcome` | `{reduction_amount}` → fee computed |
| POST | `/api/cases/{id}/billing/setup` | customer + SetupIntent; exact fee disclosure in response |
| POST | `/api/cases/{id}/reduction-confirmed` | 25% off-session charge |

Every JSON response includes `user_message`.

## MCP (streamable-http, port 8580)

Tools: `receive_life_event`, `start_bill_check`, `answer_question`,
`create_case`, `get_case`, `list_packs`, `generate_pack`, `report_outcome`,
`setup_billing`, `confirm_reduction_charge`. Every tool result includes
`user_message`.

## Money flow

1. Fee trigger: the **user confirms a reduction** (`report_outcome` /
   `POST .../outcome`).
2. Fee: **25% of the confirmed reduction**, computed by
   `src/billing.py::contingency_cents`.
3. Collection: Stripe customer + SetupIntent at `/billing/setup` (card saved
   for off-session use), then PaymentIntent off-session at
   `/reduction-confirmed`.
4. Honest disclosure: the setup response and the outcome response state in
   plain language — *"You will be charged 25% of the confirmed bill
   reduction, only if you confirm the reduction. No charge otherwise."* —
   **before** any card is saved. See `connector/TERMS.md` for cancellation
   and refund policy.

Stripe calls go through `~/workspace/skills/stripe/bin/stripe` via
subprocess; no raw keys in code, logs, or the database. **Deployment must
use a least-privilege restricted Stripe key** with write access limited to
Customers, SetupIntents, and PaymentIntents only.

## Detector rules (`src/detector.py`)

Each finding: `{rule, severity, line_refs, explanation, suggested_action}`.

1. `duplicate_line_items` (error) — same code + amount billed 2+ times.
2. `bill_vs_eob_mismatch` (error) — billed patient responsibility ≠ EOB figure.
3. `possible_balance_billing` (warning) — out-of-network emergency care or
   out-of-network charges at an in-network facility. Notes federal No
   Surprises Act protection with **"may be protected — verify"** language;
   flagged for review, never asserted.
4. `possible_unbundling` (warning) — curated mutually-exclusive CPT pairs
   (`src/code_pairs.json`), explicitly labeled **heuristic**.
5. `missing_itemized_detail` (warning) — <3 lines but total >$1,000 → request
   itemized bill.
6. `prompt_pay_suggestion` (info) — 10–20% prompt-pay discount negotiation
   tip, not an error finding.

## Security notes

- All inputs validated with pydantic; all SQL parameterized.
- All user-supplied text is `html.escape()`d before rendering into PDFs or
  tool output (reportlab Paragraphs interpret markup) — user data is inert
  text and can never alter letter structure or instructions.
- No prompt-injection surfaces: findings/letters are data derived from
  intake; user text is never executed.

## PII minimization (medical data)

We collect only: patient name/email, provider name, bill date, line items,
EOB figures. **No diagnoses, procedure notes, or insurance member IDs.**
See `connector/TERMS.md` for handling, export, and deletion.

## Disclaimers

- Template automation — NOT legal or medical advice. Review before sending.
- No letters are sent and no calls are made by this service; the user
  reviews, signs, and sends everything themselves.
- Hosting: TBD.
