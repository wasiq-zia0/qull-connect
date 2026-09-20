# BillCut

Turn your current internet, cable, or phone bill into a practical call-and-chat script, then track a lower rate you negotiate yourself.

**Current scope:** Internet, cable, and phone bills supported by the script library or generic fallback.

## Deliverables

- Provider-specific call and chat scripts where available.
- Talking points for asking about promotions, retention offers, and avoidable fees.
- A savings calculation based on the outcome and rate duration you report.

## What the user supplies

- Provider, service type, and current monthly charge
- Optional promotion expiry and account tenure
- New monthly charge after your conversation
- Number of months the new rate is confirmed to last

## Customer workflow

1. **Describe the bill.** Record the provider and current recurring amount, excluding amounts that will not repeat.
2. **Get the script.** Review the call/chat guide and adapt it to your actual service and alternatives.
3. **Contact the provider.** Make the call or use the provider's chat yourself. Confirm any new fees or contract term.
4. **Record the agreed rate.** Enter the new charge and confirmed duration. Review the savings and fee before authorizing payment.

## Price and collection

35% of the documented savings you confirm: (old monthly bill − new monthly bill) × agreed months, capped at 12 months. No positive confirmed savings means no fee.

A bill reduced from $120 to $85 for 12 months saves $420. The fee is $147 and the remaining savings are $273.

You can ask your provider for a lower rate directly without paying Qull.

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
| `GET` | `/api/providers` | Api providers |
| `POST` | `/api/cases` | Api create case |
| `GET` | `/api/cases/{cid}` | Api get case |
| `GET` | `/api/cases/{cid}/script` | Api script |
| `POST` | `/api/cases/{cid}/outcome` | User reports the negotiation result. Computes documented savings: |
| `POST` | `/api/cases/{cid}/billing/setup` | Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge. |
| `GET` | `/api/cases/{cid}/billing/status` | Verify saved-card setup with Stripe; a pending checkout is never a saved card. |
| `POST` | `/api/cases/{cid}/savings-confirmed` | User confirms documented savings: charge the 35% fee off-session. |
| `POST` | `/api/life-events` | Receive fan-out from the shared life-events bus. For bill_spike: create a |
| `GET` | `/api/me/data` | Export only the authenticated caller's local records. |
| `DELETE` | `/api/me/data` | Delete local records and documents. Stripe/payment audit records remain separately retained. |
| `GET` | `/api/cases/{cid}/billing/quote` | Quote the exact fee for the recorded outcome; does not charge or confirm it. |

The OpenAPI spec in `../../openapi/bill-negotiator.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/bill-negotiator`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- Qull supplies scripts; it does not call providers, access your account, or negotiate on your behalf.
- Savings are based on the figures you report, not an independent connection to provider billing.
- A lower headline rate may come with taxes, equipment charges, termination fees, or a new contract. Check the full offer.

Negotiation guidance; no guaranteed rate or saving.

## Data handled

Provider, service type, bill amounts, promotion dates, account tenure, negotiation outcome, savings, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/billcut/)
- [Integration and schema reference](https://qull.io/connect/billcut/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/billcut/privacy/)
- [Terms — draft](https://qull.io/connect/billcut/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
