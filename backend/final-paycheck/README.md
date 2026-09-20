# Final Paycheck Recovery (connector)

**One thing, done magically:** you changed jobs — we notice, we know your
state's deadline, and the moment your final paycheck is overdue we draft the
demand letter. Trigger → one tap → done.

Recovers unpaid final wages: opens a case, diaries the employee's state-law
payment deadline from the termination type, generates a statute-citing demand
letter once the deadline is overdue, and charges a **25% contingency fee on
user-confirmed recoveries** via Stripe.

## Agent-native design

The agent is the UI — there is no app screen. **Every REST response and every
MCP tool result carries a `user_message` field**: a warm, ready-to-speak
sentence the agent can say verbatim. Every flow is demoable inside a plain
chat transcript; nothing requires a form.

## Proactive triggers

- `connector/triggers.yaml` declares the watched life event (`job_change`),
  the detection signal (farewell emails, offer letters, employment-end
  documents in Gmail), and the exact nudge copy.
- `POST /api/life-events` receives fan-out from the shared suite bus
  (`~/workspace/connectors/life-events/`): `{"event_type": "job_change",
  "payload": {...}}` creates a **draft case** (pre-filling every field the
  payload provides) and returns the proactive nudge as `user_message`.
- `POST /api/cases/{id}/confirm` is the **one tap**: activates the draft
  (filling any remaining fields) and starts deadline tracking.
- This connector receives `job_change`; it emits nothing (no downstream
  consumer in the bus map).

## ⚠️ Not legal advice — employment-lawyer review required

This connector generates **template automation, not legal advice**. The
state-law dataset (`data/final_pay_laws.json`) is prototype-grade: it was
compiled from public payroll/labor-law summaries and state labor-department
pages (see Data provenance below) and encoded conservatively, but it **must
be verified by a licensed employment attorney before production use**. Rules
change, exceptions exist (seasonal workers, commissions, written demands), and
nothing here creates an attorney-client relationship. Every demand letter PDF
carries this disclaimer, and letters are **never sent by the service** — the
PDF is generated for the user, who sends it themselves.

## Money flow

> **Fee disclosure (shown before any card is saved):** You will be charged
> 25% of the recovered wages, only if you confirm the recovery. No charge
> otherwise. You can cancel at any time before a charge — simply do not
> confirm a recovery, or ask support to close your case. This exact text is
> returned by `POST /api/cases/{id}/billing/setup` and the `setup_billing`
> MCP tool.

1. **Intake** — user opens a case (no charge).
2. **Billing setup** — user saves a card via the SetupIntent (no charge).
3. **Deadline passes** — demand letter PDF becomes available (no charge).
4. **Recovery confirmed** — the user confirms the dollar amount actually
   recovered; the connector charges **25% of that amount** off-session via a
   Stripe PaymentIntent, at most once per case.
5. No recovery, no fee.

All Stripe calls go through the stripe skill CLI
(`~/workspace/skills/stripe/bin/stripe`), which carries the user-connected
`custom.stripe-billing` credential. No raw keys anywhere.

## Security posture (for platform review)

- **No secrets in code or logs.** Auth is injected by the Stripe skill CLI
  surrogate flow; the connector never reads, prints, or persists credentials.
- **Least-privilege Stripe key:** production deployment must use a restricted
  Stripe API key with write access limited to **Customers, SetupIntents, and
  PaymentIntents only**.
- **All inputs validated** via pydantic (length caps, amount bounds,
  enum/ISO-date parsing, state allow-list).
- **User text is untrusted data.** Every user-supplied string is sanitized
  (`sanitize.py`: control characters stripped, whitespace collapsed, length
  and line-count caps) before it is rendered into PDFs or returned in tool
  outputs. All SQL uses parameterized queries. The connector assembles no LLM
  prompts, so there is no prompt-injection surface in this service.
- **No external sends.** Demand letters are PDFs generated for the user, who
  sends them; nothing is ever sent by the service.

## Run

```bash
cd ~/workspace/connectors/final-paycheck
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn pydantic "mcp>=2" reportlab

# REST :8475 + MCP :8575 together
.venv/bin/python run.py

# or individually:
.venv/bin/python -m uvicorn app:app --port 8475
MCP_PORT=8575 .venv/bin/python mcp_server.py
```

Test mode (no Stripe CLI calls): `FINAL_PAYCHECK_STRIPE_MOCK=1`.
Deterministic clock for tests: `FINAL_PAYCHECK_TODAY=YYYY-MM-DD`.
SQLite DB lives at `data/app.db` (never in-memory).

## REST API

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/api/state-laws` | All 50 states + DC rules |
| GET | `/api/state-laws/{abbr}` | One state's rule, statute, penalty |
| POST | `/api/cases` | Intake (conversational: the agent collects fields step by step) |
| GET | `/api/cases/{id}` | Case + computed deadline/status (`waiting` / `overdue` / `needs_info` / `no_state_deadline`) |
| POST | `/api/cases/{id}/confirm` | **One-tap** activation of a draft case from a life event |
| POST | `/api/life-events` | Shared-bus fan-out receiver: `{"event_type":"job_change","payload":{...}}` → draft case + nudge in `user_message` |
| POST | `/api/cases/{id}/demand-letter` | Demand letter PDF — **400 unless overdue** |
| POST | `/api/cases/{id}/billing/setup` | Stripe customer + SetupIntent |
| POST | `/api/cases/{id}/recovery-confirmed` | `{amount}` → 25% off-session charge |

Errors are JSON: `{"error": "..."}`.

## MCP server

Streamable HTTP at `http://127.0.0.1:8575` (MCPServer name
`final-paycheck`). Tools: `create_case`, `get_case_status`,
`generate_demand_letter`, `get_state_law`, `setup_billing`,
`confirm_recovery`.

## Legal

- `connector/TERMS.md`: plain-language terms — fee terms, cancellation and
  refund policy, not-legal-advice statement, support contact placeholder,
  governing-law placeholder.
- Every demand letter PDF and `generate_demand_letter` output is labeled
  "template automation, not legal advice".

## Data provenance

`data/final_pay_laws.json` covers all 50 states + DC. Deadline rules were
compiled 2026-09-19 from:

- Patriot Software, "Final Paycheck Laws by State" (state-by-state chart with
  links to each state labor department):
  https://www.patriotsoftware.com/blog/payroll/final-paycheck-laws-by-state/
- The state labor-department pages linked from that chart (each entry carries
  its own `source_url`, e.g. https://www.dir.ca.gov/ for California).
- Cross-checked against TaxUni, LawInfo, ManagedPAY, and payrollpartners
  charts.

Every entry carries `source_url` and `last_verified: 2026-09-19`. Statute
citations are included only where verified with high confidence (CA, CO, IL,
MA, MN, MT, NV, NY, OR, TX, UT, AK, AZ, DC, HI); the rest are left blank and
must be filled during lawyer review. Entries whose rules were ambiguous or
disputed (DE, MN demand-date proxy, NM wage-type carve-outs, OR quit-notice
rule, VT quit rule, IL "if possible") are encoded conservatively and flagged
in their `notes`.

## Files

- `app.py` — FastAPI REST API
- `mcp_server.py` — MCP server (mcp SDK v2)
- `run.py` — boots REST :8475 + MCP :8575
- `db.py` — SQLite storage (`data/app.db`)
- `deadlines.py` — deadline rule engine
- `billing.py` — Stripe via skill CLI (mockable)
- `letters.py` — demand letter PDF (reportlab)
- `data/final_pay_laws.json` — 50 states + DC deadline rules
- `connector/manifest.json` — Muse Custom-connector manifest

## Hosting

TBD — this build is a self-hosted prototype. For production it needs a host
with HTTPS, a persisted `data/app.db`, and `FINAL_PAYCHECK_*` env config.
