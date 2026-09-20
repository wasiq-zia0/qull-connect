# API Completeness Audit — "The API is the product" (2026-09-19)

Read-only audit of all 10 Qull Connect connectors. Question asked: can an AI agent operate
every user-facing capability through REST/MCP, with no hidden human-dashboard step and no
endpoint that claims success when nothing verifiable happened?

**Classifications used:**
- **AGENT-COMPLETABLE** — the API actually performs the action (real provider call).
- **STRUCTURED-HANDOFF** — the API returns exact instructions/deep-link/document for the human, with a status the agent can poll afterward.
- **PRETEND-COMPLETE** — code claims success but nothing verifiable happened. **None found in any connector.**

---

## Per-connector tables

### 1. Deposit Recovery (`~/workspace/deposit-recovery`, REST 8471 / MCP 8571)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| Move detection / draft intake | `POST /api/life-events`, `POST/GET /api/drafts/{id}` (+ `/promote`) | — | agent-internal | MCP has no life-event/draft tools — conversational draft flow is REST-only |
| Deadline tracking (50 states + DC) | `POST /api/cases`, `GET /api/cases/{id}`, `GET /api/state-laws[/{abbr}]` | `create_case`, `get_case_status`, `get_state_law` | AGENT-COMPLETABLE | none |
| Demand letter PDF (overdue only) | `POST /api/cases/{id}/demand-letter` | `generate_demand_letter` | STRUCTURED-HANDOFF (weak) | **P1**: nothing *sends* the letter. Nudge copy says "Want me to send the demand letter?" and the demo story says "Sent." — but there is no send endpoint/provider. Handoff = PDF + vague "Send it" (`app.py:241-244`), no mailing instructions, no `letter_sent` status to poll |
| Billing setup (card save) | `POST /api/cases/{id}/billing/setup` | — | STRUCTURED-HANDOFF | **P1**: MCP has no billing tools at all — the two money actions are REST-only |
| Charge 25% on confirmed recovery | `POST /api/cases/{id}/recovery-confirmed` | — | AGENT-COMPLETABLE | real Stripe charge, `payment_intent_id` stored; **P1** race (see below) |
| "Cancel any time before confirming" (README) | — | — | — | **P2**: promised, no cancel/delete endpoint; Stripe customer can't be detached |

**Pollability:** `GET /api/cases/{id}` exposes full state incl. `billing_status`, `fee_charged_cents` ✅. Drafts pollable ✅. Fire-and-forget: `POST /api/life-events` (no fan-out ack); no `letter_sent` status; `card_pending` with no SetupIntent-completion check.
**Idempotency:** Double-charge guard present (400 if `fee_charged`, `app.py:279-280`; setup 400 if customer exists). BUT check-then-act with **no Stripe idempotency key** and **no DB transaction** around `charge_fee` → `db.update_case` (`app.py:286-292`): concurrent double-POST can double-charge. `POST /api/cases` mints a fresh uuid per call — retries create duplicate cases.

### 2. EU261 Flight Compensation (`~/workspace/connectors/eu261-flight-comp`, 8472 / 8572)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| Delay detection / draft | `POST /api/life-events` | `handle_life_event` | agent-internal | none |
| Eligibility verdict + tier (€250/400/600) | `POST /api/claims`, `GET /api/claims/{id}` | `check_eligibility`, `get_claim` | AGENT-COMPLETABLE | **P0**: `GET /api/claims/{id}` returns *verdict only*; `billing_status`, `payout_amount_eur`, `fee_amount_eur`, `payment_intent_id` are stored in SQLite (`src/db.py`) but **never exposed** — the money action is unpollable |
| Claim pack PDF | `POST /api/claims/{cid}/claim-pack` | `generate_claim_pack` | STRUCTURED-HANDOFF (weak) | **P1**: "their claims email is usually on their website" (`app.py:247-249`) — no per-airline claims deep link, no `filed` status to poll |
| Billing setup | `POST /api/claims/{cid}/billing/setup` | `setup_billing` | STRUCTURED-HANDOFF | no card-completion verification |
| Charge 30% on confirmed payout | `POST /api/claims/{cid}/payout-confirmed` | `confirm_payout` | AGENT-COMPLETABLE | real Stripe charge, guard present (400 if `fee_charged`); **P1** race; **P2**: MCP flattens 4xx/5xx into `{"error":...}` JSON — agent can't distinguish already-charged from charge-failed |
| "Cancel anytime" (README) | — | — | — | **P2**: promised, no cancel endpoint |

**Pollability:** ⚠️ the one P0 — post-charge money state is invisible.
**Idempotency:** Guards confirmed; same no-idempotency-key race (`app.py:292-300`); intake mints uuid per call — duplicate claims on retry, no dedupe on (flight, date, passenger).

### 3. Subscription Slayer (`~/workspace/connectors/subscription-slayer`, 8473 / 8573)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| Scan Gmail receipts | `POST /api/scan` | `scan_subscriptions` | AGENT-COMPLETABLE (read-only) | none; dedupes on `merchant_key` |
| List subscriptions | `GET /api/subscriptions[?status=]` | `list_subscriptions` | AGENT-COMPLETABLE | none (no GET-single; list covers polling) |
| Update status/savings/notes | `PATCH /api/subscriptions/{id}` | `update_subscription` | AGENT-COMPLETABLE | none |
| Cancel pack (48 curated merchants) | `GET /api/subscriptions/{id}/cancel-pack` | `get_cancel_pack` | STRUCTURED-HANDOFF | **P2**: unknown merchants get generic fallback with empty `cancel_url` (`app.py:78-97`) — structured but weakest handoff |
| Billing setup | `POST /api/billing/setup` | `setup_billing` | STRUCTURED-HANDOFF | **P1**: no pollable card-save status; **P2**: no 400-on-duplicate guard (orphans prior Stripe customers, unlike Bill Negotiator) |
| Charge 30% of first-year savings | `POST /api/savings/confirmed` | `confirm_savings` | AGENT-COMPLETABLE | guards: `status=cancelled` + positive savings (422), re-confirm → 409, no card → 409; charges ledger in SQLite. **P1** race (no idempotency key, `billing.py:62-65`); **P1-minor**: `requires_capture` treated as settled (`billing.py:66-68`) |
| Life-event intake | `POST /api/life-events` | — | agent-internal | none (deliberate asymmetry) |

**Pollability:** `GET /api/subscriptions` is the status endpoint ✅; charges ledger ✅. Gap: card-save completion unpollable.
**Idempotency:** `confirm_savings` retry-safe at API layer (409 after success); Stripe-call race remains.

### 4. Bill Negotiator (`~/workspace/connectors/bill-negotiator`, 8474 / 8574)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| Open negotiation case (+ bill-spike) | `POST /api/cases` | `create_negotiation_case` | AGENT-COMPLETABLE | **P2**: no intake dedupe — retry can open duplicate cases |
| List providers + scripts | `GET /api/providers` | `list_supported_providers` | AGENT-COMPLETABLE | none |
| Script pack (call + chat scripts) | `GET /api/cases/{id}/script` | `get_negotiation_script` | STRUCTURED-HANDOFF | none — README honestly states "The user makes the call/chat — the connector never contacts providers" |
| Report outcome | `POST /api/cases/{id}/outcome` | `report_negotiation_outcome` | AGENT-COMPLETABLE | validates `new < old` (400), caps 12 months, zero-savings → terminal `no_fee_no_savings` |
| Billing setup | `POST /api/cases/{id}/billing/setup` | `setup_negotiation_billing` | STRUCTURED-HANDOFF | **P1**: card-save unpollable; idempotency *better* here (400 if already set up) |
| Charge 35% of documented savings | `POST /api/cases/{id}/savings-confirmed` | `charge_negotiation_fee` | AGENT-COMPLETABLE | **P1** race (no key, `src/billing.py:64-72`); **P1-minor**: `requires_capture` treated as settled |
| Life-event intake | `POST /api/life-events` | `receive_life_event` | agent-internal | none — only connector with the bus receiver as an MCP tool |

**Pollability:** Strong ✅ — `GET /api/cases/{id}` exposes `outcome`, `savings`, full `billing_status` state machine (`not_set_up → card_pending → fee_charged/fee_failed/no_fee_no_savings`), `fee_cents`, `payment_intent_id`.
**Idempotency:** Same Stripe-race as Slayer. **P2**: MCP `create_negotiation_case` skips REST's pydantic validation (`mcp_server.py:38-44` vs `CaseIn`) — negative bill could flow through MCP unvalidated.

### 5. Final Paycheck Recovery (`~/workspace/connectors/final-paycheck`, 8475 / 8575)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| State-law lookup (50 + DC) | `GET /api/state-laws[/{abbr}]` | `get_state_law` | AGENT-COMPLETABLE | none |
| Case intake / life-event draft | `POST /api/cases`, `POST /api/life-events` | `create_case` | agent-internal | none |
| One-tap draft activation | `POST /api/cases/{id}/confirm` | `confirm_case` | AGENT-COMPLETABLE | none |
| Status / deadline tracking | `GET /api/cases/{id}` | `get_case_status` | AGENT-COMPLETABLE | none — exposes `billing_set_up`, `fee_status`, `demand_letter_generated` |
| Demand letter PDF (overdue only) | `POST /api/cases/{id}/demand-letter` | `generate_demand_letter` | STRUCTURED-HANDOFF | none — README explicit: "letters are never sent by the service" (designed handoff) |
| Billing setup | `POST /api/cases/{id}/billing/setup` | `setup_billing` | STRUCTURED-HANDOFF | **P1**: card-save unpollable (`billing_set_up` set at customer creation, before card save) |
| Charge 25% on confirmed recovery | `POST /api/cases/{id}/recovery-confirmed` | `confirm_recovery` | AGENT-COMPLETABLE | guard present; **P2** race |
| "Guidance on filing a wage claim" (promised in `_status_message` for `no_state_deadline`, `app.py:~213`) | — | — | — | **P1**: vague promise, no endpoint behind it |

**Pollability:** ✅ per-case status; card-save the only unpollable step.
**Idempotency:** No keys anywhere; duplicate cases on intake retry (money-free, P2). `FINAL_PAYCHECK_STRIPE_MOCK=1` is env-gated and responses carry `"mock": True` — operational risk only if misconfigured in prod (P2).

### 6. Class Action Cash (`~/workspace/connectors/class-action-cash`, 8476 / 8576)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| List settlements + staleness flag | `GET /api/settlements` | `list_settlements` | AGENT-COMPLETABLE | none — `data_freshness` pollable |
| Scan Gmail receipts | `POST /api/scan` | `scan_receipts` | AGENT-COMPLETABLE (read-only) | **P2**: re-scan creates duplicate matches (uuid per run, `core.py:120-144`) |
| List matches | `GET /api/matches` | `get_matches` | AGENT-COMPLETABLE | **P2**: list-only, no per-match GET |
| Claim pack (steps + official URL) | `POST /api/matches/{id}/claim-pack` | `generate_claim_pack` | STRUCTURED-HANDOFF | none — connector explicitly never files (`core.py:~150`); statuses candidate→claim_pack_generated→billed pollable |
| Billing setup | `POST /api/matches/{id}/billing/setup` | `setup_billing` | STRUCTURED-HANDOFF | **P1**: `card_pending` never resolved to a verifiable state — nothing ever writes billing status except the final `billed` |
| Charge 20% on confirmed payout | `POST /api/matches/{id}/payout-confirmed` | `confirm_payout` | AGENT-COMPLETABLE | guard present (`core.py:283-285`); **P2** race; **P2**: HTTP 200 `ok:false` on refusal instead of 409 (cosmetic) |
| Life-event intake | `POST /api/life-events` | `receive_life_event` | agent-internal | none |

**Pollability:** ✅ matches + settlement freshness; card-save unpollable.
**Idempotency:** `CLASS_ACTION_CASH_DRY_RUN=1` responses carry `"dry_run": True`, but dry-run charges mark the match `billed` — permanently blocking a later real charge for that match in a shared DB (P2 operational).

### 7. Unclaimed Property (Found Money) (`~/workspace/connectors/unclaimed-property`, 8477 / 8577)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| List states + portal URLs | `GET /api/states` | `list_states` | AGENT-COMPLETABLE | none |
| Intake search / one-tap completion | `POST /api/searches`, `PATCH /api/searches/{id}` | `start_search`, `complete_search` | AGENT-COMPLETABLE | none (DOB only with consent — 422 without; SSN never collected) |
| Claim pack per state | `GET /api/searches/{id}/claim-pack?state=XX` | `get_claim_pack` | STRUCTURED-HANDOFF | none — portal deep link + steps + checklist; connector never files, never sends PII to states |
| Per-state claim status tracking | `PATCH /api/searches/{id}/states/{abbr}` | `update_claim_status` | AGENT-COMPLETABLE | none — `not_started/in_progress/filed/paid/denied` pollable via `GET /api/searches/{id}` (`status_counts`, `recoveries`) |
| Search detail | `GET /api/searches/{id}` | — | AGENT-COMPLETABLE | **P2**: no `get_search` MCP tool — MCP agent must use REST for full status |
| Billing setup | `POST /api/searches/{id}/billing/setup` | `setup_billing` | STRUCTURED-HANDOFF | **P1**: card-save unpollable (no SetupIntent retrieval path anywhere) |
| Charge 15% on confirmed recovery | `POST /api/searches/{id}/recovery-confirmed` | `confirm_recovery` | AGENT-COMPLETABLE | guard present (`store.py:167-173`, `app.py:530-533`); **P1**: guard is *per-search* but recoveries are *per-state* — a second genuine recovery (TX then CA) hits 409 `already_billed` and can never be charged. Under-billing + agent dead-end |

**Pollability:** ✅ per-state lifecycle; card-save unpollable.
**Idempotency:** Same Stripe race (P2).

### 8. Moving Concierge (`~/workspace/connectors/moving-concierge`, 8478 / 8578)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| Intake + state-specific checklist | `POST /api/moves` | `create_move` | AGENT-COMPLETABLE | none |
| Move details + progress | `GET /api/moves/{id}` | `get_move` | AGENT-COMPLETABLE | none |
| Pack (Markdown) | `GET /api/moves/{id}/pack?format=md` | `get_pack` | STRUCTURED-HANDOFF | **P2**: MCP hardcodes `format="md"` (`mcp_server.py:35`) — PDF unreachable via MCP |
| Pack (PDF) | `GET /api/moves/{id}/pack?format=pdf` | — | STRUCTURED-HANDOFF | same row |
| Checklist updates | `PATCH /api/moves/{id}/checklist` | `update_checklist` | AGENT-COMPLETABLE | none — per-item URLs, completion pollable item-by-item |
| Billing setup | `POST /api/moves/{id}/billing/setup` | `setup_billing` | AGENT-COMPLETABLE | none (refuses duplicates) |
| Charge $49 flat | `POST /api/moves/{id}/pay` | `pay` | AGENT-COMPLETABLE | **P1** race (no key; `app.py:377-378` check → `app.py:394` write) |
| Life-event intake | `POST /api/life-events` | `receive_life_event` | agent-internal | none |

**External actions:** USPS/DMV/voter/bank steps are explicit non-promises (README: "We never file an address change on your behalf") — structured handoff with per-state deep links ✅.
**Pollability:** ✅ — `GET /api/moves/{id}` covers draft/ready/paid, checklist progress, billing.
**Idempotency:** Sequential retries safe; concurrent race remains. TERMS.md placeholders (support email, governing law) unresolved — **P1 launch-blocking**.

### 9. 401(k) Match Maximizer (MatchMax) (`~/workspace/connectors/401k-match`, 8479 / 8579)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| Match math (uncaptured $, recommended %) | `POST /api/plans` | `create_plan` | AGENT-COMPLETABLE | none — math hand-verified |
| Conversational intake | `POST /api/plans/draft[/{id}/answer]` | `start_draft`, `answer_draft` | AGENT-COMPLETABLE | none |
| Plan summary | `GET /api/plans/{id}` | `get_plan_summary` | AGENT-COMPLETABLE | none |
| Fix pack (402 until paid) | `GET /api/plans/{id}/pack` | `get_fix_pack` | STRUCTURED-HANDOFF | **P1**: no "fix applied" status — the agent can poll `paid` but never verify the user actually changed their contribution |
| Billing setup | `POST /api/plans/{id}/billing/setup` | `setup_billing` | AGENT-COMPLETABLE | none |
| Charge $99/year | `POST /api/plans/{id}/pay` | `pay_fee` | AGENT-COMPLETABLE | **P1** race (409 guards present, `app.py:440-441`); TERMS placeholders unresolved |
| Life-event intake | `POST /api/life-events` | — | agent-internal | **P2**: `receive_life_event` is REST-only — MCP agent can't take the proactive-intake path |
| Disclaimer | — | `disclaimer` | — | none — not-financial-advice on every response ✅ |

**Pollability:** ✅ per-plan; no fire-and-forget mutating POSTs.
**Idempotency:** Same concurrent race. **P2**: IRS elective-deferral constant (`23500.0`) needs yearly review (README flags it).

### 10. Medical Bill Fighter (`~/workspace/connectors/medical-bill-fighter`, 8480 / 8580)

| Capability | REST | MCP tool | Classification | Gap? |
|---|---|---|---|---|
| Bill intake + 6-rule detection | `POST /api/cases`, `/draft`, `/{id}/answer` | `create_case`, `start_bill_check`, `answer_question` | AGENT-COMPLETABLE | none |
| Case + findings | `GET /api/cases/{id}` | `get_case` | AGENT-COMPLETABLE | none |
| Letter packs (dispute/itemized/assistance/negotiate) | `GET /api/cases/{id}/pack?type=` | `generate_pack` | STRUCTURED-HANDOFF | **P1**: no "letter sent" status — dispatch invisible; fee trigger is user's self-reported reduction (`POST /api/cases/{id}/outcome`) |
| Report reduction | `POST /api/cases/{id}/outcome` | `report_outcome` | AGENT-COMPLETABLE | **P2**: overwrites `outcome_json` even after fee charged — post-payment correction desyncs `reduction_amount` from charged `fee_cents` |
| Billing setup | `POST /api/cases/{id}/billing/setup` | `setup_billing` | AGENT-COMPLETABLE | none |
| Charge 25% on confirmed reduction | `POST /api/cases/{id}/reduction-confirmed` | `confirm_reduction_charge` | AGENT-COMPLETABLE (REST) / **BROKEN (MCP)** | **P0**: MCP tool (`mcp_server.py:208-226`) has **no `fee_status` guard** — REST does (`app.py:303-307`). Any MCP retry after success double-charges 25% |
| Life-event intake | `POST /api/life-events` | `receive_life_event` | agent-internal | none |

**Pollability:** ✅ — `GET /api/cases/{id}` returns intake, findings, outcome, `fee_cents`, `fee_status`.
**Idempotency:** REST retry-after-failure safe (`fee_status="failed"`); MCP path has no guard at all (P0); no Stripe idempotency key anywhere (P1 race). `LEGAL_NOTICE` (template automation, not legal advice) on every response ✅.

---

## Prioritized fix list

### P0 — fix before any money flows through these paths
1. **medical-bill-fighter: MCP double-charge hole.** `mcp_server.py:208` `confirm_reduction_charge` charges 25% with no `fee_status` check; REST equivalent has it (`app.py:303-307`). Mirror the guard (`fee_status not in (None, "failed")`) — one line, data already in hand via `_case_or_raise`. Any agent retry/timeout-replay on the MCP path silently double-charges today.
2. **eu261-flight-comp: money state unpollable.** `GET /api/claims/{id}` (`app.py:222-229`) returns only the eligibility verdict; `billing_status`, `payout_amount_eur`, `fee_amount_eur`, `payment_intent_id` exist in SQLite (`src/db.py`) but are never exposed. After the agent charges 30% it cannot re-check the charge state — the exact failure the founder direction forbids.

### P1 — fix before Meta submission / launch
3. **No Stripe idempotency key on ANY charge path (all 10).** Every money endpoint uses check-then-act state guards with no idempotency key and no atomic DB write; the shared Stripe skill CLI (`~/workspace/skills/stripe/bin/stripe`) has no `--idempotency-key` flag on `charge`. Sequential agent retries are safe (guards hold), but concurrent double-POSTs or a crash between Stripe success and the DB write can double-charge. Fix once: add a DB-level "charging" lock (or `charging_at` marker) on every money endpoint + plumb a deterministic key (e.g. `<connector>-fee-<case_id>`) through the CLI when it gains support.
4. **Card-save completion unpollable (all 10).** `billing/setup` returns `client_secret` and leaves `card_pending`; nothing ever reads back SetupIntent status. The agent's first signal of a missing card is the failed charge. Add `GET /api/.../billing/status` (SetupIntent retrieval) per connector.
5. **deposit-recovery: "send the demand letter" copy drift.** `triggers.yaml` ("sends a formal demand letter"), the nudge ("Want me to send the demand letter?"), and the submission demo ("Sent.") all imply the agent sends it — no send capability exists. Either build a send path (email/postal provider) or correct the copy everywhere, and add a `letter_sent` status the agent can poll.
6. **deposit-recovery: MCP missing the money tools.** `billing/setup` and `recovery-confirmed` are REST-only; MCP has `create_case`, `get_case_status`, `generate_demand_letter`, `get_state_law`. An MCP-only agent cannot complete the paid flow.
7. **unclaimed-property: per-search charge guard blocks legitimate multi-state fees.** Guard is per-search (`store.py:167-173`, `app.py:530-533`) but recoveries are per-state — a TX recovery followed by a CA recovery hits 409 `already_billed` and can never be charged. Make the guard per-recovery (state + amount).
8. **eu261-flight-comp: filing handoff too weak.** "Their claims email is usually on their website" (`app.py:247-249`) with no per-airline claims-channel deep link and no `filed` status. Add an airline→claims-URL table + a `filed` flag the agent can set/poll.
9. **final-paycheck: "guidance on filing a wage claim?" promised with no endpoint** (`app.py:~213`, `no_state_deadline` branch) — vague manual-step text, no structured handoff.
10. **TERMS.md placeholders unresolved (launch-blocking).** Support-email and governing-law `[PLACEHOLDER]`s in moving-concierge, 401k-match, medical-bill-fighter (and root/nested copies) — must be filled before launch.
11. **`requires_capture` treated as settled** (subscription-slayer `billing.py:66-68`, bill-negotiator `src/billing.py:66-68`) — an uncaptured authorization is reported as a settled fee.
12. **401k-match: fix-execution unverifiable.** Pack gives exact in-provider steps but there is no "fix applied" status; the agent can poll `paid` but never confirm the user captured the match. (Design decision needed: is the product the calculation or the captured match?)
13. **medical-bill-fighter: letter-send unobservable.** No "letter sent" status; the fee rests on the user's self-reported reduction. Acceptable if self-attestation is the intended trust model — but name it as such.

### P2 — hardening / hygiene
14. **No intake dedupe anywhere (all 10).** `POST /api/cases|claims|moves|plans|searches` and `POST /api/life-events` mint a fresh uuid per call — agent retries and bus redelivery create duplicate cases/claims/matches. Add idempotency/intake keys or natural dedupe (e.g. EU261 on flight+date+passenger; class-action scan on settlement+keyword).
15. **Duplicate Stripe customers** on repeated `POST /api/billing/setup` (subscription-slayer — bill-negotiator already 400s; normalize).
16. **"Cancel anytime" promised, no cancel endpoint** (deposit-recovery, eu261) — README copy vs API reality.
17. **MCP gaps:** moving-concierge `get_pack` hardcodes `format="md"` (PDF unreachable via MCP); unclaimed-property has no `get_search` MCP tool; 401k-match `receive_life_event` is REST-only; eu261 MCP flattens 4xx/5xx into `{"error":...}` JSON.
18. **medical-bill-fighter:** `POST /api/cases/{id}/outcome` overwrites outcome after fee charged — lock outcome once `fee_status` is set.
19. **bill-negotiator:** MCP `create_negotiation_case` skips REST pydantic validation — align or share the model.
20. **class-action-cash:** dry-run charges mark matches `billed`, permanently blocking a later real charge in a shared DB — test-mode hygiene.
21. **401k-match:** IRS elective-deferral constant (`23500.0`) needs a yearly refresh process.

---

## Verdict

**Is the API truly the product today? Almost — but not yet.**

What holds up: **zero PRETEND-COMPLETE endpoints across all 10 connectors.** Every "success" response is backed by a real DB write, a real generated artifact, or a real Stripe charge whose status is verified. Every money endpoint has an explicit user-confirmation gate and an API-layer double-charge guard. External-world actions the connector can't perform (filing claims, cancelling subscriptions, calling providers, mailing letters) are disclosed as human handoffs with structured artifacts — the design is honestly agent-first, and `user_message` coverage lets an agent run every capability conversationally.

What breaks the claim:
1. **Two P0s** — one connector whose money state is invisible after charging (EU261), and one money path that double-charges on MCP retry (medical-bill-fighter). Both are small, known-line fixes.
2. **One structural P1** — no Stripe idempotency key on any charge path, with a CLI that doesn't accept one. The API-layer guards make sequential retries safe, but the concurrent/crash-window double-charge risk is real on all 10 until a DB-level "charging" lock or key support lands.
3. **One systemic P1** — the card-save step (necessarily human, PCI) has no completion check anywhere; the agent flies blind between SetupIntent creation and the first charge attempt.
4. **Copy drift** in deposit-recovery ("send the demand letter") and a handful of weak handoffs (EU261 airline filing, final-paycheck wage-claim guidance) that promise more structure than the API delivers.

Bottom line: the architecture is right and the dishonesty count is zero. Fix the 2 P0s and the idempotency design, close the card-save pollability gap, and the "API is the product" claim becomes defensible for Meta review.
