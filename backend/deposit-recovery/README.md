# Deposit Recovery

Organize your tenancy details, review an estimated state return deadline, and prepare a demand-letter PDF you can send to your landlord.

**Current scope:** U.S. residential rental deposits. Limited deadline estimates are currently supported for reviewed California, Connecticut, and Texas rules; other states require review and use factual request letters.

## Deliverables

- A case summary with the information used to estimate the return deadline.
- A letter PDF using your tenancy details; unreviewed state rules produce a neutral request without unsupported legal assertions.
- A record of your letter status, reported recovery, and fee status.

## What the user supplies

- Tenant name and forwarding address
- Rental property and landlord names/addresses
- State, move-out date, and landlord receipt date for a written forwarding address where relevant; Connecticut also requires the date the tenancy legally ended
- Deposit amount and any amount later recovered

## Customer workflow

1. **Record the tenancy.** Enter the dates, addresses, and deposit amount. Check the details against your lease and move-out records.
2. **Review the deadline.** Read the estimate and its assumptions. Check the current official rule before relying on it.
3. **Send your letter.** Review the generated PDF, make any needed corrections, and send it yourself. Keep evidence of delivery.
4. **Confirm the outcome.** If money is returned, record the actual amount and review the fee before authorizing payment.

## Price and collection

25% of the recovered deposit amount you confirm. No recovery confirmation means no recovery fee.

If you confirm that $1,800 was returned, the fee is $450 and you keep $1,350.

You can contact your landlord yourself and use your state or local tenant resources without paying Qull.

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
| `GET` | `/health` | Public liveness probe (orchestrator). |
| `GET` | `/api/state-laws` | Api list states |
| `GET` | `/api/state-laws/{abbr}` | Api get state |
| `POST` | `/api/cases` | Api create case |
| `GET` | `/api/cases/{cid}` | Api get case |
| `POST` | `/api/cases/{cid}/demand-letter` | Api demand letter |
| `POST` | `/api/cases/{cid}/letter-status` | Record the user's review/send decision on the prepared demand letter. |
| `POST` | `/api/cases/{cid}/billing/setup` | Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge. |
| `GET` | `/api/cases/{cid}/billing/status` | Verify saved-card setup with Stripe; a pending checkout is never a saved card. |
| `POST` | `/api/cases/{cid}/recovery-confirmed` | Tenant confirms the deposit came back: charge the 25% contingency fee off-session. |
| `POST` | `/api/life-events` | Receive a fan-out life event from the shared bus. |
| `GET` | `/api/drafts/{draft_id}` | Api get draft |
| `POST` | `/api/drafts/{draft_id}/promote` | Merge conversationally collected fields into a draft and open the case. |
| `GET` | `/api/me/data` | Export only the authenticated caller's local records. |
| `DELETE` | `/api/me/data` | Delete local records and documents. Stripe/payment audit records remain separately retained. |
| `POST` | `/api/cases/{cid}/billing/quote` | Show the exact rounded fee for review; no card setup, confirmation or payment occurs. |

The OpenAPI spec in `../../openapi/deposit-recovery.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/deposit-recovery`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- The service prepares documents; it does not contact your landlord, mail letters, negotiate, file in court, or provide representation.
- Only specifically reviewed rule paths provide deadline estimates. Other states or incomplete facts require manual review; lease terms, notices, local rules, and exceptions can change the result.
- Deadline status is calculated when requested. A live email watcher, scheduled mailing service, and automated reminders are not included.

Document preparation and general information; no legal advice or attorney–client relationship.

## Data handled

Names, rental and forwarding addresses, landlord details, tenancy dates, deposit/recovery amounts, generated letters, case status, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/deposit-recovery/)
- [Integration and schema reference](https://qull.io/connect/deposit-recovery/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/deposit-recovery/privacy/)
- [Terms — draft](https://qull.io/connect/deposit-recovery/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
