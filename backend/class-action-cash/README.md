# Class Action Cash

Match receipts you provide against the available settlement catalog and prepare a claim-information pack for you to review and file on the official site.

**Current scope:** Only current, supported catalog entries. Closed, stale, or unverified entries must not be treated as active opportunities.

## Deliverables

- Candidate matches to current, supported entries in the settlement catalog.
- An eligibility checklist, official filing link, and claim-information sheet.
- A record of the match and any payout you later confirm.

## What the user supplies

- Relevant receipt merchant, purchase date, amount, and text
- Name and contact/address details needed for a claim-information sheet
- Your answers to the settlement's eligibility criteria
- Actual payout amount if received

## Customer workflow

1. **Provide relevant receipts.** Supply the purchases you want checked. This release does not access Gmail.
2. **Review candidate matches.** Read the dates and eligibility criteria on the administrator's official website.
3. **File directly.** Use the pack to help complete the official claim yourself. Make only truthful declarations.
4. **Confirm any payout.** A submitted claim may be denied or pay a different amount. Authorize a fee only on the amount actually received.

## Price and collection

20% of the actual settlement payout you confirm for this service. Filing directly with a settlement administrator is free.

If you confirm a $100 settlement payout, the fee is $20 and you keep $80. An estimate in a claim pack is not a promised payout.

Official settlement claim filing is free. You can find and file eligible claims directly without paying Qull.

Billing setup requires explicit fee-term acceptance (`accept_fee_terms: true`)
and returns Stripe's hosted setup URL. A return redirect does not establish
that a payment method is ready; poll the authenticated billing-status endpoint.
Retrieve the fee quote before asking for payment confirmation; fixed-price
Moving Concierge and MatchMax expose their amount in billing status. A quote
does not charge. The charge call requires a fresh confirmation
(`confirm_fee: true`), the exact expected `fee_amount_cents`, and the
operation's outcome data. The server
calculates the amount and verifies the saved payment method. Never treat a
local customer ID, a sample response, or a health response as proof of payment.

No test may create a live charge without separate explicit authorization.
Use Stripe test mode for end-to-end payment verification. There is no automatic
renewal, generic subscription, or automated tax calculation in this release.

## Authentication and access

REST calls use `Authorization: Bearer <opaque Qull user API key>`.
Each credential maps to one server-controlled owner in `QULL_API_KEYS_FILE`.
A caller-supplied platform/user header is not production authentication.
Life-event intake follows the same owner-bound authentication.
See [integration guide](../../docs/INTEGRATION.md) and
[deployment documentation](../deploy/DEPLOY.md) for provisioning and hosting.

Public `/health` is process liveness. `/ready` is configuration readiness, not
confirmation that a customer workflow or payment was completed. No OAuth flow
or Meta-specific credential exchange is implemented; confirm that integration
contract before describing the service as connected to Muse.

## Run and develop

From this service directory, install `requirements.txt` in an isolated Python
environment. Run `python run.py` to start the REST/MCP processes according to
the checked-in ports, or `uvicorn app:app --host 127.0.0.1 --port 8000` for REST.
Use the deploy scripts and their current environment documentation for the
production configuration. Keep databases and credentials out of Git.

## REST operations

| Method | Path | Operation |
|---|---|---|
| `GET` | `/health` | Health |
| `GET` | `/api/settlements` | Settlements |
| `POST` | `/api/scan` | Scan |
| `GET` | `/api/matches` | Matches |
| `POST` | `/api/matches/{match_id}/claim-pack` | Claim pack |
| `POST` | `/api/matches/{match_id}/billing/setup` | Create a Stripe customer + SetupIntent so the user can save a card. |
| `GET` | `/api/matches/{match_id}/billing/status` | Pollable card-save status: billing status, derived card_state, and the |
| `POST` | `/api/matches/{match_id}/payout-confirmed` | User confirms the settlement paid out -> charge the 20% fee off-session. |
| `POST` | `/api/life-events` | Shared-bus receiver. Logs the match, returns the proactive nudge. |
| `GET` | `/api/me/data` | Export only the authenticated caller's local records. |
| `DELETE` | `/api/me/data` | Delete local records and documents. Stripe/payment audit records remain separately retained. |
| `POST` | `/api/matches/{match_id}/billing/quote` | Show the exact rounded fee for review; no card setup, confirmation or payment occurs. |

The OpenAPI spec in `../../openapi/class-action-cash.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/class-action-cash`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- The catalog is curated, not a live or exhaustive feed. Only entries with a currently verified official source and an open deadline are considered; availability changes as entries expire.
- A receipt match does not establish eligibility, certify your claim, or guarantee payment.
- Qull does not submit claims, sign declarations, send email, or communicate with administrators for you.

Claim-organization assistance; no legal advice and no guarantee of settlement eligibility or payment.

## Data handled

Supplied receipt information, candidate matches, contact/address details for claim sheets, reported payout, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/class-action-cash/)
- [Integration and schema reference](https://qull.io/connect/class-action-cash/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/class-action-cash/privacy/)
- [Terms — draft](https://qull.io/connect/class-action-cash/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
