# Connector environment variables — production reference

Inventory generated 2026-09-19 by grepping every connector's `*.py` (excluding
venvs) for `os.environ` / `os.getenv` reads, plus the billing modules' Stripe
wiring. Updated for the identity/tenant-isolation fix pass. Source of truth
is the code; this file summarizes it.

## Shared variables (all 10 connectors)

| Variable | Required in prod? | Notes |
|---|---|---|
| `ENV=production` | **Yes** | Disables `/docs`, `/redoc`, `/openapi.json` and dev-only static serving. Without it the full money-API schema is public. |
| `SERVICE_API_KEY` | **Yes** | Bearer token required on `POST /api/life-events` (server-to-server). Generate with `openssl rand -hex 32`. Without it, life-event intake 401s. |
| `PLATFORM_USER_HEADER` | No (default `X-Platform-User-Id`) | Header the Muse platform injects with the authenticated user's id. If Meta's review specifies a different propagation mechanism, change this (and see nginx note below). |
| `ALLOW_DEV_IDENTITY=1` | **NEVER in prod** | Enables the `X-Dev-User-Id` fallback (anyone can impersonate any user). Dev/verification only. |
| `STRIPE_SECRET_KEY` | **Yes** | Fresh restricted key (see Stripe section). Never reuse the key exposed on 2026-09-19. |

### nginx edge (deploy-time, not connector code)

| Variable | Where | Notes |
|---|---|---|
| `PLATFORM_TRUSTED_CIDRS` | env on the machine running `deploy.sh` | Space-separated CIDRs whose `X-Platform-User-Id` header nginx forwards upstream. **Default empty = the header is blanked for every client** (spoof-proof). Fill with Meta's published egress ranges once known; until then, reconcile with Meta's actual identity mechanism during review — the code choke point is `src/identity.py:resolve_owner()`. |

## Per-connector variables

| Connector | Variable | Required in prod? | Notes |
|---|---|---|---|
| deposit-recovery | `DEPOSIT_HOST` | No (default `127.0.0.1`) | Only connector honoring a host override. Systemd unit pins it to `127.0.0.1` (nginx terminates TLS). Docker image overrides via `REST_PORT`/`MCP_PORT` instead (see below). |
| deposit-recovery | `DEPOSIT_REST_PORT` | No (default `8471`) | |
| deposit-recovery | `DEPOSIT_MCP_PORT` | No (default `8571`) | |
| final-paycheck | `REST_PORT` | No (default `8475`) | |
| final-paycheck | `MCP_PORT` | No (default `8575`) | |
| final-paycheck | `FINAL_PAYCHECK_DB` | No (default `<dir>/data/app.db`) | Override only if you relocate sqlite storage. |
| medical-bill-fighter | `MCP_PORT` | No (default `8580`) | Read by `mcp_server.py` standalone mode. |
| eu261-flight-comp, subscription-slayer, bill-negotiator, class-action-cash, unclaimed-property, moving-concierge, 401k-match | — | — | No env vars read at all. Ports/hosts are hardcoded in `run.py` / `mcp_server.py`. |

### Docker-only variables (not read by connector code)

The Dockerfiles set `REST_PORT` / `MCP_PORT` as image ENV, consumed by the
generated `/app/start.py` entrypoint (which binds both servers to `0.0.0.0`).
Only `final-paycheck` and `medical-bill-fighter` also read these names in
their own code; for the other eight they are entrypoint-only. Overridable
at `docker run -e REST_PORT=… -e MCP_PORT=…`.

## DEV-ONLY flags — MUST be absent/unset in production

These change money behavior. `deploy.sh` writes env files with them commented
out; verify they are **not** present in `/etc/connectors/*.env` before going live:

| Variable | Connector | What it does in dev | Prod risk if set |
|---|---|---|---|
| `CLASS_ACTION_CASH_DRY_RUN=1` | class-action-cash | Simulates billing without charging | Fake charges recorded as real; revenue silently lost |
| `FINAL_PAYCHECK_STRIPE_MOCK=1` | final-paycheck | Stubs all Stripe CLI calls | No real money moves; fees never collected |
| `FINAL_PAYCHECK_TODAY=YYYY-MM-DD` | final-paycheck | Overrides "today" for deadline math | Wrong legal deadlines computed |

Check on the VPS any time with:
```
sudo grep -rE "DRY_RUN|MOCK|_TODAY" /etc/connectors/ || echo "clean"
```
Expected output: `clean`.

## Stripe / billing — production transport (rewired 2026-09-19)

Every connector's `billing.py` now routes Stripe calls through an internal
transport (`_stripe()`):

- **`STRIPE_SECRET_KEY` is set (production):** direct Stripe REST calls to
  `https://api.stripe.com/v1` — `POST /v1/customers`, `POST /v1/setup_intents`,
  `POST /v1/payment_intents` (off-session, confirmed, USD). Request shapes are
  identical to the old CLI's. The key is read from the environment only and
  is never logged, printed, or persisted.
- **`STRIPE_SECRET_KEY` is unset (dev on the agent machine):** falls back to
  the local skill CLI at `~/workspace/skills/stripe/bin/stripe`, which carries
  the connected `custom.stripe-billing` credential. This keeps local
  development and the existing verification flows working unchanged.

Mock/dry-run flags (`FINAL_PAYCHECK_STRIPE_MOCK=1`,
`CLASS_ACTION_CASH_DRY_RUN=1`) still short-circuit **before** the transport
choice, so test runs never touch the network even if a key is set. All fee
math, fee-disclosure ordering, user-confirmed charge guards, and
double-charge/`already_billed` guards are unchanged.

### `STRIPE_SECRET_KEY` — REQUIRED in production

| | |
|---|---|
| **Variable** | `STRIPE_SECRET_KEY` |
| **Required in prod?** | **Yes — all 10 connectors.** Without it, billing silently falls back to the CLI path, which does not exist on the VPS, so every charge/setup call fails. |
| **Value** | A **fresh** Stripe **restricted** secret key (`rk_live_…`), scoped to the minimum needed: Customers (write), SetupIntents (write), PaymentIntents (write). Read-only on nothing else; no other permissions. |
| **Where** | `/etc/connectors/<slug>.env` on the VPS (mode `640`, loaded via `EnvironmentFile=`). The generated env files already contain a `#STRIPE_SECRET_KEY=` placeholder — uncomment and fill it, then `systemctl restart qull-<slug>`. |
| **Rotation** | A restricted key was exposed in a screenshot on 2026-09-19. **Do NOT reuse that key** — create a brand-new restricted key in the Stripe Dashboard (Developers → API keys → Restricted keys) and revoke the exposed one. |

No raw keys are stored anywhere in this repo — keep it that way.

**Do not enable real charges** until: the fresh restricted key is in place,
the dev-only flags above are confirmed absent, legal has reviewed the
contingency-fee model, and one witnessed live charge+refund has succeeded.

## Env file locations (VPS)

- `/etc/connectors/<slug>.env` — owned by `connectors:connectors`, mode `640`.
- Created with placeholders on first `deploy.sh` run; **never overwritten** after.
- Systemd units load them via `EnvironmentFile=`; `systemctl restart qull-<slug>`
  after any edit.
