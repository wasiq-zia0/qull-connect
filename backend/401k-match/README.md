# 401(k) Match Finder — connector

Finds uncaptured employer 401(k) match and delivers a contribution fix plan.
**Flat $99.00/year** (charged once, after the user confirms — see "Money flow").

## Agent-native design

The agent is the UI — there is no app screen. **Every success response carries
a `user_message` field**: a warm, ready-to-speak sentence the agent says
verbatim, so every flow is fully demoable inside a plain chat transcript.

**Golden path (trigger → one tap → done):** `job_change` life event →
proactive nudge → one draft question (salary + contribution %) → confirm the
match formula (defaults to the common 50%-up-to-6%) → payoff summary →
optional $99/year fix pack. No forms, no homework.

**Advanced path:** `POST /api/plans` with the full intake in one call.

## Run

```bash
cd ~/workspace/connectors/401k-match
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn pydantic "mcp>=2"
.venv/bin/python run.py        # REST :8479 + MCP :8579
# or individually:
.venv/bin/python -m uvicorn app:app --port 8479
.venv/bin/python mcp_server.py  # MCP on :8579
```

Data: file-backed SQLite at `data/app.db` (never in-memory).

## REST endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/api/life-events` | Receive a fanned-out life event (`{"event_type","payload"}`). `job_change` starts a draft plan and returns the proactive nudge in `user_message` |
| POST | `/api/plans/draft` | Start the conversational intake; returns the first question in `user_message` |
| POST | `/api/plans/draft/{id}/answer` | Answer the current question; returns the next question or the finished payoff summary |
| POST | `/api/plans` | Full intake in one call (advanced path). Plan stored; **fix pack locked** |
| GET | `/api/plans/{id}` | Summary: uncaptured $, current vs max match, paid status |
| POST | `/api/plans/{id}/billing/setup` | Creates Stripe customer + SetupIntent; returns `client_secret` to save a card. **No charge at this step.** Includes the exact fee disclosure. |
| POST | `/api/plans/{id}/pay` | Charges the flat $99.00/year off-session against the saved card. Includes the exact fee disclosure. |
| GET | `/api/plans/{id}/pack` | Full fix plan. **402 Payment Required** until the fee is paid |

Validation errors return 422 with details; unknown plan/draft → 404.

## Life events (shared bus)

Integrated per `~/workspace/connectors/life-events/README.md`:

- **Receives** `job_change` via `POST /api/life-events` (the bus fans it out to
  `final-paycheck` and `401k-match`). Pre-fills whatever the payload provides
  (salary, contribution %, name, new employer) and returns the nudge:
  > "New job, new 401(k). Most people leave free match money on the table in
  > year one. Tell me your salary and contribution % and I'll check yours in
  > 30 seconds."
- **Emits** `job_change` when the user mentions a new employer in the plan
  intake (`new_employer` field), so sibling connectors can act on it too.
- Proactive trigger spec: `connector/triggers.yaml`.

## MCP server

Streamable HTTP at `http://127.0.0.1:8579` (via `mcp_server.py`).

Tools: `start_draft`, `answer_draft`, `create_plan`, `get_plan_summary`,
`setup_billing`, `pay_fee`, `get_fix_pack`, `disclaimer`.
Every tool return carries `user_message`.

## Money flow (flat $99.00/year)

1. `POST /api/plans` — free. Summary shows the uncaptured match and the
   recommendation; the fix pack stays locked.
2. `POST /api/plans/{id}/billing/setup` — creates a Stripe customer and a
   SetupIntent; the user saves a card against the returned `client_secret`.
   Response states: "You will be charged a flat $99.00 per year for the
   match analysis and fix plan. Charged once, after you confirm."
   **No money moves here.**
3. `POST /api/plans/{id}/pay` — charges **$99.00 once** off-session
   (Stripe PaymentIntent, `$99.00` = 9900 cents). The fee disclosure is
   repeated in the response. Plan flips to `paid`.
4. `GET /api/plans/{id}/pack` — unlocked only when `paid = true`.

Refund policy: full refund within 14 days of the charge if the fix plan was
not used/viewed. Full terms: `connector/TERMS.md`.

Stripe is called through the skill CLI
(`~/workspace/skills/stripe/bin/stripe`) via `src/billing.py`; no raw keys
in code or logs. Deployment uses a least-privilege restricted Stripe key
(Customers / SetupIntents / PaymentIntents write only).

## Math (every step shown in `result`)

Inputs: `salary`, `pay_frequency` (weekly 52 / biweekly 26 / semimonthly 24 /
monthly 12), `current_contrib_pct`, `match_pct` (e.g. 50), `match_cap_pct`
(e.g. 6), `true_up` (bool).

1. `per_paycheck_gross = salary / periods`
2. `current_annual_employee = per_paycheck × pct/100 × periods`
3. `match_cap_dollars = salary × match_cap_pct/100`
4. `matchable = min(current_annual_employee, match_cap_dollars)`
5. `employer_match_now = matchable × match_pct/100`
6. `max_match = match_cap_dollars × match_pct/100`
7. `uncaptured = max_match − employer_match_now`
8. `required_contrib_pct = ceil(match_cap_pct to nearest 0.5%)`, capped so
   `salary × pct/100` never exceeds the IRS elective-deferral limit
9. `new_per_paycheck = salary × required_pct/100 / periods`
10. `projected_annual_gain = new_employer_match − employer_match_now`

Worked example: $120,000 salary, biweekly, 4% contribution, 50% match up to
6% → per-paycheck $4,615.38; current annual $4,800; cap $7,200; match now
$2,400; max match $3,600; **uncaptured $1,200/yr**; required **6%** → $276.92
per paycheck.

### IRS-limit constant note
`src/calc.py` holds `IRS_ELECTIVE_DEFERRAL_LIMIT = 23500.0` with
`IRS_LIMIT_TAX_YEAR = 2026`. This constant is tax-year-specific and **must
be reviewed and updated every year** (the 2026 figure is the IRS-published
elective-deferral limit).

## Fix plan (pack)

Unlocked after payment. Contains: recommended contribution %, per-paycheck
dollars, annual employee contribution, projected annual gain, generic steps
to change the rate in any 401(k) provider, and notes on true-up (year-end
true-up vs per-paycheck matching) and any IRS-limit binding.

## Disclaimer (not financial advice)

This connector is an **educational/financial-education tool only — NOT
financial advice**. Every summary and pack carries the disclaimer, and users
should confirm their match formula, vesting, and contribution rules with
their plan administrator. Full terms: `connector/TERMS.md`.

## Security

- All inputs validated via pydantic; SQLite access uses parameterized queries.
- All user-supplied text is treated as untrusted data: `sanitize_text()`
  strips control characters and caps length before storage/use; user input is
  never interpolated into prompts, SQL, or shell commands.
- No secrets in code, logs, or the database.

## Hosting

TBD — deployment target not yet chosen.
