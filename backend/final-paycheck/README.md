# Final Paycheck Recovery

Organize your separation and wage details, review a deadline estimate, and prepare a final-pay demand letter you can send yourself.

**Current scope:** U.S. final-paycheck document preparation. A limited reviewed Nevada discharge rule supports a deadline estimate; other state/separation scenarios require review and produce factual wage requests.

## Deliverables

- A case record with unpaid amount and job-separation details.
- A deadline estimate only for the reviewed Nevada discharge scenario; other cases show that review is required.
- A demand-letter PDF for you to check and send.

## What the user supplies

- Employee and employer names/addresses
- State, last day, separation type, and relevant notice/payday dates
- Unpaid wage amount and supporting facts
- Actual recovered amount, if received

## Customer workflow

1. **Record what is owed.** Check your pay records and enter the unpaid amount and separation facts.
2. **Review the scope.** Read the supported rule and its assumptions. Other states or separation situations remain factual requests, without an asserted legal deadline.
3. **Send your letter.** Review the PDF and send it yourself. Keep your pay records and proof of delivery.
4. **Confirm recovered wages.** Record only money received and review the proposed fee before authorizing payment.

## Price and collection

25% of the actual recovered wages you confirm. No recovery confirmation means no recovery fee.

If you confirm $2,000 in recovered wages, the fee is $500 and you keep $1,500.

State and federal labor agencies may offer wage-claim information and filing channels without a Qull fee.

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
| `GET` | `/api/state-laws` | State laws |
| `GET` | `/api/state-laws/{abbr}` | State law |
| `POST` | `/api/cases` | Create case |
| `GET` | `/api/cases/{case_id}` | Get case |
| `POST` | `/api/cases/{case_id}/confirm` | One-tap activation of a draft case (created from a job_change life event). |
| `POST` | `/api/cases/{case_id}/demand-letter` | Demand letter |
| `POST` | `/api/cases/{case_id}/billing/setup` | Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge. |
| `GET` | `/api/cases/{case_id}/billing/status` | Verify saved-card setup with Stripe; a pending checkout is never a saved card. |
| `POST` | `/api/cases/{case_id}/recovery-confirmed` | User confirms wages were recovered; charge the 25% contingency fee |
| `POST` | `/api/life-events` | Receive fan-out from the shared life-events bus. Creates a draft case |
| `GET` | `/api/me/data` | Export only the authenticated caller's local records. |
| `DELETE` | `/api/me/data` | Delete local records and documents. Stripe/payment audit records remain separately retained. |
| `POST` | `/api/cases/{case_id}/billing/quote` | Show the exact rounded fee for review; no card setup, confirmation or payment occurs. |

The OpenAPI spec in `../../openapi/final-paycheck.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/final-paycheck`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- Qull does not send letters, contact employers, file wage claims, represent you, or calculate every available penalty.
- The compiled deadline data is not a complete legal analysis. Coverage depends on the employment facts and current law.
- You can use government wage-claim channels or seek qualified advice without using Qull.

General information and document preparation; no legal advice or attorney–client relationship.

## Data handled

Employee/employer names and addresses, employment/separation dates, state, unpaid and recovered amounts, generated letters, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/final-paycheck/)
- [Integration and schema reference](https://qull.io/connect/final-paycheck/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/final-paycheck/privacy/)
- [Terms — draft](https://qull.io/connect/final-paycheck/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
