# Subscription Slayer

Identify recurring charges in receipts you provide, get merchant-specific cancellation instructions, and track the cancellations you complete.

**Current scope:** Supported receipt data and merchant guides. Merchant procedures, notice periods, and cancellation fees vary.

## Deliverables

- A list of likely recurring subscriptions detected from your supplied receipts.
- A cancellation guide and merchant link where available, with a generic guide for unsupported merchants.
- A record of subscription status and the monthly savings you report.

## What the user supplies

- Receipt subject, sender, snippet/body, and date; or merchant, amount, currency, and billing frequency entered directly
- Subscriptions you want to track
- Cancellation status and monthly savings you report
- Billing contact information if you choose the paid service

## Customer workflow

1. **Provide receipts.** Supply the relevant receipts. This release does not connect to your Gmail account.
2. **Review recurring charges.** Confirm that each detected charge is a subscription and that the amount is correct.
3. **Cancel with the merchant.** Follow the guide yourself and keep the merchant's confirmation.
4. **Confirm completed work.** Select the cancellations you completed and review the $10-per-item total before authorizing payment.

## Price and collection

USD $10 for each completed cancellation you select and explicitly confirm for billing. No recurring Qull subscription fee.

If you confirm two completed cancellations, the total fee is $20. Cancelling with the merchants remains your responsibility.

You can cancel directly with the merchant without using or paying Qull.

Billing setup requires explicit fee-term acceptance (`accept_fee_terms: true`)
and returns Stripe's hosted setup URL. A return redirect does not establish
that a payment method is ready; poll the authenticated billing-status endpoint.
The charge call requires a fresh confirmation (`confirm_fee: true`), the exact
expected `fee_amount_cents`, and the operation's outcome data. The server
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
| `POST` | `/api/scan` | Fixture-only demonstration. Gmail integration requires per-user OAuth and is unavailable. |
| `POST` | `/api/receipts` | Detect recurring charges from user-provided receipt text. Raw receipts are not stored. |
| `POST` | `/api/subscriptions` | Track a recurring charge supplied by the user; does not contact the merchant. |
| `GET` | `/api/subscriptions` | List subscriptions |
| `PATCH` | `/api/subscriptions/{sub_id}` | Update subscription |
| `GET` | `/api/subscriptions/{sub_id}/cancel-pack` | Get cancel pack |
| `POST` | `/api/billing/setup` | Create the Stripe customer + SetupIntent. NO charge is taken here. |
| `GET` | `/api/billing/status` | Pollable card-save + billing state for the caller. |
| `POST` | `/api/savings/fee-quote` | Read-only quote for the exact completed cancellation IDs. Does not reserve or charge. |
| `POST` | `/api/savings/confirmed` | User-confirmed cancellations trigger the $10-per-cancellation off-session fee charge. |
| `POST` | `/api/life-events` | Receive user-authorized charge metadata; never a shared service-key draft. |
| `DELETE` | `/api/data` | Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger. |

The OpenAPI spec in `../../openapi/subscription-slayer.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/subscription-slayer`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- Receipt matching can miss subscriptions or mistake repeated purchases for subscriptions. Review every result.
- Qull does not log into merchant accounts, cancel on your behalf, guarantee refunds, or override merchant terms.
- Live Gmail access is unavailable. Import your own receipts or enter subscriptions directly; fixture scans are development demonstrations and do not establish real subscriptions.

Cancellation guidance and tracking. Merchant charges and terms remain separate from Qull's fee.

## Data handled

Receipt content you provide, merchant names, charge amounts/dates, detected subscriptions, cancellation notes/status, reported savings, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/subscription-slayer/)
- [Integration and schema reference](https://qull.io/connect/subscription-slayer/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/subscription-slayer/privacy/)
- [Terms — draft](https://qull.io/connect/subscription-slayer/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
