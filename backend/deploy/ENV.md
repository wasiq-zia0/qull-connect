# Runtime configuration

This reference describes the reviewed source. A live `/health` response does not prove these changes have been deployed. Keep secrets in server environment files or a secret manager, never in the repository, submission form, logs, or screenshots.

| Variable | Required configuration | Purpose |
|---|---|---|
| `ENV` | `production` on every live service | Disables development identity and development documentation/static routes. |
| `QULL_API_KEYS_FILE` | Absolute path to a readable JSON registry | Resolves bearer-key hashes to server-assigned user IDs. An absent, malformed, permissive, or empty registry authenticates nobody. |
| `DATA_DIR` | Absolute durable directory writable by the service | User databases and generated documents. Kept separate from packaged reference data in the application's `data/` directory. |
| `BILLING_LEDGER_PATH` | Absolute durable SQLite path, normally `$DATA_DIR/billing.sqlite3` | Durable payment state and retries. Back this up with the connector's application database. |
| `STRIPE_SECRET_KEY` | Restricted `rk_test_…`/`rk_live_…` or secret `sk_test_…`/`sk_live_…`; official CLI sandbox `rkcs_test_…` is accepted only in test mode | Stripe server credential. Grant the operations actually used by the shared payment client. Never expose it to browser code. |
| `STRIPE_MODE` | `test` or explicitly `live`; default `test` | Must match the Stripe key mode. Mock success and CLI fallback are removed. |
| `STRIPE_PUBLISHABLE_KEY` | `pk_test_…` or `pk_live_…` matching the secret key | Browser authentication when a payment requires an additional cardholder step. |
| `PUBLIC_BASE_URL` | Full HTTPS connector base, e.g. `https://api.qull.io/deposit-recovery` | Stripe hosted Checkout return URL and payment-authentication link. No query, fragment, user info, or secret. |
| `HOST` | `127.0.0.1` behind nginx; Docker sets `0.0.0.0` | Bind address for both REST and MCP. |
| `REST_PORT`, `MCP_PORT` | Defaults in the table below | All ten supervisors support these variables. |

`SERVICE_API_KEY`, `PLATFORM_USER_HEADER`, and `PLATFORM_TRUSTED_CIDRS` no longer grant access. All life-event calls need the affected user's scoped bearer key. The application ignores `X-Platform-User-Id`; nginx strips it as defense in depth. There is no assumed Meta identity header or implemented OAuth flow.

`ALLOW_DEV_IDENTITY=1` works only when `ENV` is exactly `test` or `development`. Production configuration fails closed if this flag, `FINAL_PAYCHECK_STRIPE_MOCK`, `CLASS_ACTION_CASH_DRY_RUN`, or `FINAL_PAYCHECK_TODAY` is enabled. Never enable these on a public service.

| Connector | REST | MCP |
|---|---:|---:|
| deposit-recovery | 8471 | 8571 |
| eu261-flight-comp | 8472 | 8572 |
| subscription-slayer | 8473 | 8573 |
| bill-negotiator | 8474 | 8574 |
| final-paycheck | 8475 | 8575 |
| class-action-cash | 8476 | 8576 |
| unclaimed-property | 8477 | 8577 |
| moving-concierge | 8478 | 8578 |
| 401k-match | 8479 | 8579 |
| medical-bill-fighter | 8480 | 8580 |

The Deposit supervisor retains `DEPOSIT_HOST`, `DEPOSIT_REST_PORT`, and `DEPOSIT_MCP_PORT` as fallbacks for older installations. `FINAL_PAYCHECK_DB` remains a legacy explicit database-path override; remove it when migrating to `DATA_DIR`, or separately back up the database it names.

## User-key registry and provisioning

The deployment setup creates `/etc/connectors/user-keys.json` as `root:connectors`, mode `0640`. Each entry contains the SHA-256 hash of a cryptographically random API key and the immutable owner ID selected by the operator. No client may choose an owner with a request header.

```json
{"version":1,"keys":[]}
```

Create a separate key per user. Choose a stable internal user ID, not a name supplied by an unauthenticated request. Write the raw credential to a private file rather than terminal output:

```bash
sudo python3 backend/deploy/provision-key.py \
  --registry /etc/connectors/user-keys.json create \
  --owner user_123 --output /root/qull-user_123.key
```

The CLI prints a nonsecret key ID and creates the delivery file with mode `0600`. Deliver that file through a secure credential channel and remove the delivery copy afterward. The CLI refuses to overwrite an existing file. Add `--expires-at 2026-12-31T00:00:00Z` for a time-limited review credential. A disabled or expired key authenticates nobody.

Revoke by the printed key ID:

```bash
sudo python3 backend/deploy/provision-key.py \
  --registry /etc/connectors/user-keys.json revoke --key-id KEY_ID
```

The registry is reloaded for each request; revocation affects new requests immediately. An operation already authorized before revocation can finish. Rotation means creating a replacement for the same owner, delivering it securely, then revoking the old key.

## Probes and limits

`GET /health` is public process liveness. `GET /ready` returns `200` only when the registry, Stripe key modes, publishable key, HTTPS base URL, and writable absolute data directory are configured; otherwise it returns `503`. Readiness performs no Stripe call and cannot verify a key's permissions, card setup, payment collection, document accuracy, or Meta compatibility. It is a configuration gate, not approval evidence.

Other public routes are restricted to exact methods: `GET /billing/return`, `GET /billing/authenticate`, `GET /billing/authenticate.js`, and `POST /billing/authenticate/session`. The payment-session exchange independently validates its short-lived capability token. All personal-data API routes and MCP requests require bearer authentication.

The application rejects request bodies larger than 1,000,000 bytes, including chunked requests without `Content-Length`. It limits each socket client to 120 standard requests/minute and 20 payment/document requests/minute, returns `429` with `Retry-After`, and maintains separate in-memory budgets per REST/MCP process. The nginx edge adds a shared IP limit. Run one ASGI worker per connector. A multi-host deployment requires a shared rate-limit store; these counters are not a distributed quota.
