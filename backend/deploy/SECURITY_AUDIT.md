# Security Audit — Qull Connect connectors (2026-09-19)

Read-only audit of all 10 connectors (FastAPI REST + MCP streamable-HTTP + SQLite).
Scope: `~/workspace/deposit-recovery` + `~/workspace/connectors/<slug>/` × 9.
No source modified. Companion docs: `STATUS.md` (build/verification), `API_AUDIT.md`
(API completeness), `deploy/PRICING.md`, `deploy/ENV.md`.

**Bottom line:** the code is clean of the classic vulnerability classes — no SQL
injection, no command injection, no SSRF, no hardcoded secrets, no path
traversal reachable today, patched framework versions. The single dominant
finding is **there is no authentication or tenant isolation anywhere in code**:
every REST endpoint and every MCP tool is callable by anyone who can reach the
URL, and all users share one database per connector. The manifests *declare*
`oauth2_user` auth (8 of 10), but no code reads, verifies, or acts on any
identity signal. Meta's security review will fail this on sight unless the
platform provides per-user isolation *and* the code is changed to enforce it.

---

## 1. Authentication / authorization — the top question

**Verdict: no authentication exists in code. All endpoints and tools are
unauthenticated.**

Evidence (all 10 connectors):
- Grep for `apikey|api_key|bearer|authorization|Depends(|add_middleware|
  BaseHTTPMiddleware|secret_key|SECRET_KEY|oauth|jwt` across every `app.py` and
  `mcp_server.py`: **zero hits** (the only `secret_key` hits are in `billing.py`,
  referring to the `STRIPE_SECRET_KEY` env var, not request auth).
- Grep for `request.headers|Header(|X-User|Authorization` across all app and MCP
  server files: **zero hits** — no code reads any identity header at all.
- `connector/manifest.json` declares auth intent per connector:

| Connector | manifest `auth.type` |
|---|---|
| deposit-recovery | `oauth2_user` |
| eu261-flight-comp | `none` |
| subscription-slayer | `oauth2_user` |
| bill-negotiator | `oauth2_user` |
| final-paycheck | `oauth2_user` |
| class-action-cash | `oauth2_user` |
| unclaimed-property | `oauth2_user` |
| moving-concierge | `stripe_customer` |
| 401k-match | `oauth2_user` |
| medical-bill-fighter | `oauth2_user` |

So the *declared* model is "the platform authenticates the user" — but nothing
in code consumes platform identity. Consequences:

1. **No tenant isolation.** No `user_id`/`tenant_id`/`owner` field exists in any
   schema or table (grep: zero hits). One shared SQLite per connector.
2. **Cross-user data exposure via enumeration endpoints** (no auth + no scoping):
   - `GET /api/matches` (class-action-cash) returns **all** matches for all users.
   - `GET /api/subscriptions` (subscription-slayer) returns **all** subscriptions.
   - Per-ID endpoints (`GET /api/cases/{id}` etc.) use unguessable UUIDs, which
     is obfuscation, not a boundary — and the enumeration endpoints above hand
     out real IDs.
3. **Honor-system money authorization.** Charge endpoints take only an amount:
   - `RecoveryConfirmed { amount: float }` (final-paycheck, unclaimed-property)
   - `PayoutConfirmRequest` (class-action-cash), `SavingsConfirmRequest`
     (subscription-slayer), equivalents elsewhere.
   
   Anyone who reaches `POST /api/cases/{id}/recovery-confirmed` with a valid
   case UUID triggers an **off-session Stripe charge on the victim's saved
   card**. UUIDs are unguessable, but (2) shows IDs are enumerable in at least
   two connectors, and "user confirmed" is enforced only by trusting the caller
   (today: Muse; after public hosting: anyone on the internet).

**What Meta's reviewers will ask:** "How do you authenticate callers and isolate
users?" The honest current answer is "we don't; we depend on the platform."
That is only acceptable if (a) Meta's hosted-connector model guarantees
per-user network/identity isolation, **and** (b) the submission documents it,
**and** (c) the code is updated to verify the platform's identity signal and
scope all queries by user. Today (c) is entirely missing — even a perfect
platform can't help code that never checks who is calling.

---

## 2. Injection

### SQL — clean
- All value interpolation uses `?` parameters. Four f-string `UPDATE`
  statements interpolate **column names only**, and every one is safe:
  - `bill-negotiator/src/db.py:90` — `update_case(cid, **fields)`; all callers
    in `app.py`/`mcp_server.py` pass hardcoded keyword args (verified). Safe
    today, but the `**fields` pattern is one careless caller away from SQLi.
  - `eu261-flight-comp/src/db.py:60` — filtered through an explicit `allowed`
    column set. Safe.
  - `medical-bill-fighter/src/store.py:101` — hardcoded column names. Safe.
  - `subscription-slayer/app.py:292` — keys from a validated pydantic model,
    hardcoded mapping. Safe.

### Command injection — clean
- Every `subprocess` call uses list-form args, **no `shell=True` anywhere**.
- `billing.py` (all 10): `subprocess.run([str(STRIPE_CLI), *args], …)` — user-
  influenced values (name, email, amount, description) travel as list elements,
  never through a shell. Safe.
- `scanner.py` (gmail CLI), `matcher.py` (gmail CLI): same list-form pattern.
  `matcher.py` builds the Gmail *query string* from settlement keywords — those
  keywords come from the connector's own curated settlement list, not user
  input. Safe.
- **Target design** (sibling agent's rewire, already landed in
  `deposit-recovery/src/billing.py`, rolling out to the other 9): `_stripe_rest`
  does form-encoded `requests.post` to the fixed host `https://api.stripe.com`
  with `Authorization: Bearer <STRIPE_SECRET_KEY from env>`. Key read from
  environment only, never logged. Safe transport. **Gap: no `Idempotency-Key`
  header on the charge POST** — now that the CLI is gone, adding one is trivial
  and it closes the API audit's structural double-charge race (P1).

### Path traversal — not reachable today, harden anyway
- `deposit-recovery/app.py:231`: `LETTER_DIR / f"demand-letter-{cid}.pdf"` where
  `cid` is a URL path param. **Not exploitable**: the endpoint 404s unless the
  case exists in the DB (`api_demand_letter`, line ~220), and case IDs are
  server-minted UUIDs — an attacker cannot smuggle `../` through a UUID that
  must already exist. Same pattern in final-paycheck, medical-bill-fighter,
  moving-concierge (download filenames only, never disk writes).
- `letters_out/` is the only user-influenced disk write; writes are
  create-only PDFs. Recommend sanitizing filename components regardless (P2).

---

## 3. Secrets

- Grep for `sk_live_*|rk_live_*|sk_test_*|xox*|ghp_*|password = "..."` across all
  `.py/.json/.yaml/.md/.env` in all 10 repos: **clean — no live secrets
  committed.**
- The `rk_live_…` key exposed in a founder screenshot earlier is **not** in any
  repo (it was screenshot-only). **Rotation/deletion of that key cannot be
  verified from here — human step, must be confirmed before launch** (P0
  process item).
- New billing transport reads `STRIPE_SECRET_KEY` from env only; deploy scripts
  write placeholder env files, never real values. Correct pattern.

---

## 4. Input validation

- **REST: good.** Pydantic models on all intakes with `max_length` bounds
  (deposit-recovery is thorough: names ≤200, addresses ≤300; class-action-cash,
  401k-match similar). Amounts bounded (`gt=0`, caps). Invalid states → 422/404.
- **MCP: thin.** Tools are plain typed functions (e.g.
  `confirm_reduction_charge(case_id: str)`); the MCP SDK gives JSON-schema type
  checking but no length/format/semantic constraints. The API audit already
  found bill-negotiator's MCP path skipping REST's pydantic validation
  (negative bill possible via MCP). Recommend shared validators (P2).
- No LLM/prompt sink exists in the connectors (no prompt-injection surface).
- File uploads: none (`UploadFile` absent everywhere).

---

## 5. PII handling

**Stored per connector (SQLite, plaintext — no encryption at rest):**
- Names, emails, phone, postal addresses: deposit-recovery (tenant, landlord,
  forwarding/rental addresses), unclaimed-property (incl. **DOB**, gated behind
  a `consent_required` 422 — good), class-action-cash (name/email/address on
  claim packs), moving-concierge (move addresses), 401k-match (name, salary
  inputs), subscription-slayer (subscription/merchant data from Gmail).
- **Health-adjacent data:** medical-bill-fighter stores bill line items,
  provider names, and dispute text — PHI-adjacent. No HIPAA controls, no
  encryption, no BAA. This belongs in the legal review queue (already flagged).
- Stripe customer IDs + payment intent IDs stored in DB (low sensitivity
  without the secret key).

**Logging:** no `logging` module use in any `app.py`; grep for `print(...name/
email/address/…)` — clean. Uvicorn access logs record method+path only (no
bodies). No evidence of PII in log files.

**At rest:** SQLite files are plaintext on disk. No column encryption, no
SQLCipher. Acceptable for launch only with the platform's disk-level
protections documented; recommend encrypting PII columns or the DB for the
medical connector at minimum (P2, legal-adjacent).

---

## 6. Dependencies

- `pip-audit` / `pip_audit` is **not installed** in any `.venv`; not run
  (installing it was out of scope for a read-only audit). **Recommend adding
  `pip-audit` as a blocking CI/deploy gate** (P1).
- Manual check on pinned versions (identical in all 10 `requirements.txt`):
  `starlette==1.6.0`, `fastapi==0.141.1`, `uvicorn==0.53.0`,
  `pydantic==2.13.5`, `mcp==2.2.0`, `requests==2.34.2`, `anyio==4.15.1`,
  `reportlab==5.0.1`.
- **CVE-2026-48710 "BadHost"** (Starlette Host-header validation bypass,
  GHSA-86qp-5c8j-p5mr): affects starlette `>=0.8.3, <=1.0.0`, fixed in `1.0.1`.
  Our pin `1.6.0` **includes the fix** — not vulnerable. (Exploitability would
  have been low anyway: no path-based security middleware exists to bypass.)
- `requests==2.34.2` postdates the known `.netrc`/proxy CVEs. No other
  headline CVEs identified in the pinned set via manual search; this does not
  substitute for `pip-audit`.

---

## 7. CORS, rate limiting, request size limits

| Control | Status |
|---|---|
| CORS middleware | **Absent** (no `CORSMiddleware` anywhere). Not a vuln for server-to-server MCP/REST; browsers would simply block cross-origin calls. Leave absent or set restrictively. |
| Rate limiting | **Absent everywhere.** No `slowapi`, no limiter, no nginx rate config yet. Enumeration endpoints, PDF generation (CPU-heavy: reportlab/fpdf), and charge endpoints are unthrottled — trivial DoS/abuse from one client. **P1.** |
| Request size limits | **Absent at the framework layer.** Pydantic `max_length` bounds individual fields, but total JSON body size is unbounded (Starlette default) — large-body memory DoS possible. **P2** (mitigate in nginx `client_max_body_size` + app-level limit). |
| `/docs`, `/openapi.json`, `/redoc` | **Exposed by default** (no `docs_url=None` anywhere) — full API schema, including money endpoints, visible to anyone. **P1**: disable in production. |

---

## 8. SSRF

**Clean.** No endpoint fetches a user-supplied URL (no `requests.get` on user
input, no `urlopen`, no webhook URL fields). The only outbound HTTPS is the new
billing transport to the fixed host `https://api.stripe.com`. Gmail/skill CLIs
are local subprocesses, not URL fetches.

---

## Per-connector security table

| # | Connector | Unauth endpoints/tools | Enum. exposure | Charge auth | Notable |
|---|---|---|---|---|---|
| 1 | deposit-recovery | all | UUID-guarded | honor-system | letter write safe via 404-guard |
| 2 | eu261-flight-comp | all | UUID-guarded | honor-system | manifest declares `auth: none` |
| 3 | subscription-slayer | all | **YES** `GET /api/subscriptions` lists all | honor-system | Gmail read via local CLI |
| 4 | bill-negotiator | all | UUID-guarded | honor-system | `**fields` SQL pattern (safe today) |
| 5 | final-paycheck | all | UUID-guarded | honor-system | — |
| 6 | class-action-cash | all | **YES** `GET /api/matches` lists all | honor-system | DRY_RUN marks billed (test flag) |
| 7 | unclaimed-property | all | UUID-guarded | honor-system | stores DOB (consent-gated ✅) |
| 8 | moving-concierge | all | UUID-guarded | honor-system ($49 flat) | — |
| 9 | 401k-match | all | UUID-guarded | honor-system ($99 flat) | — |
| 10 | medical-bill-fighter | all | UUID-guarded | honor-system | health-adjacent PII, no encryption |

---

## Ranked fix list

### P0 — remotely exploitable / fails Meta review on sight
1. **No authentication or tenant isolation in code.** Every REST endpoint and
   MCP tool is reachable by anyone; enumeration endpoints (`GET /api/matches`,
   `GET /api/subscriptions`) expose all users' data; charge endpoints fire on
   the caller's word alone. Fix: adopt Meta's platform identity mechanism
   (verify per their connector docs), scope every query by user, remove or
   user-scope enumeration endpoints. Until this is designed, **do not put real
   user data or real cards on a public host.**
2. **Confirm the exposed `rk_live_…` restricted key was rotated/deleted.**
   Human step; cannot be verified from the repos. Do before any production
   Stripe traffic.

### P1 — Meta will likely require / serious hardening
3. **Rate limiting** on all public endpoints (nginx `limit_req` + app-level),
   especially PDF generation and charge endpoints.
4. **Disable `/docs`, `/openapi.json`, `/redoc`** in production
   (`docs_url=None, openapi_url=None`).
5. **Add `Idempotency-Key` header to the Stripe charge POST** in the new
   `_stripe_rest` transport (all 10) — closes the crash-between-charge-and-
   DB-write double-charge window.
6. **Run `pip-audit` and gate deploys on it** (currently never run).
7. **Document the auth model for Meta's review**: exactly which platform
   mechanism authenticates callers, how user identity reaches the connector,
   and where tenant isolation is enforced. The manifest claims (`oauth2_user`)
   must match a real implementation.

### P2 — defense in depth
8. Sanitize/allowlist filename components for letter/PDF writes (safe today
   via 404-guard + UUID IDs; make it structurally safe).
9. Add an explicit column allowlist to bill-negotiator's `update_case`
   (future-proof the `**fields` pattern).
10. Share pydantic validators with MCP tools (MCP inputs currently get only
    SDK type checking).
11. Request body size limits (nginx `client_max_body_size` + Starlette limit).
12. Encryption at rest for SQLite PII (minimum: medical-bill-fighter), or
    documented platform disk encryption.
13. `POST /api/life-events` receivers accept arbitrary `payload` dicts —
    validate `event_type` against the known set and bound payload size.

---

## What would fail Meta's functional/security/legal review on sight

1. **Zero authentication in code** while manifests promise `oauth2_user` —
   the single biggest review risk. A reviewer curling any endpoint with no
   credentials gets full access; `GET /api/matches` dumps every user's
   settlement matches.
2. **Cross-user data visibility** (no tenant isolation) — a privacy finding
   independent of auth.
3. **Charge endpoints authorized by caller assertion only** — a payments
   reviewer will flag off-session charges triggerable by an unauthenticated
   POST.
4. **`/docs` exposing the full money API** to the open internet.
5. **No rate limiting** in front of CPU-heavy PDF generation and money
   endpoints.
6. Legal queue (unchanged, out of security scope but review-blocking):
   contingency-fee/UPL exposure, Found Money state fee caps, medical-data
   handling, TERMS.md placeholders (support email, governing law).

## Explicitly clean (verified)
No SQL injection · no command/shell injection · no SSRF · no hardcoded secrets
in repos · no reachable path traversal · no PII in logs · Starlette 1.6.0
includes the BadHost fix · Stripe key handled env-only, never logged ·
subprocess always list-form, no `shell=True`.
