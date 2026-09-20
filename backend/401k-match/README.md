# MatchMax

Model a simple employer-match formula, compare contribution amounts, and get an explanation of the calculation and steps to discuss with your plan administrator.

**Current scope:** Educational calculations for the supported U.S. employer-match formula. Confirm all rules with your plan administrator.

## Deliverables

- A transparent calculation of current contributions and modeled employer matching.
- A comparison of the current rate with the rate needed for the supported formula.
- A paid action pack with per-paycheck figures and questions for your plan administrator.

## What the user supplies

- Annual salary and pay frequency
- Current employee contribution percentage
- Employer matching percentage and eligible pay cap
- Whether your plan provides a true-up, as confirmed by its documents

## Customer workflow

1. **Read your plan formula.** Get the actual matching terms from your employer or plan documents. Do not assume the example formula applies.
2. **Enter your figures.** Supply salary, pay frequency, current contribution, and the confirmed match terms.
3. **Review the comparison.** Check the assumptions and modeled annual amounts. The summary is educational.
4. **Choose the detailed pack.** If useful, authorize the one-time $99 fee. Make any payroll changes yourself after checking your plan's rules.

## Price and collection

USD $99 once for a plan's detailed analysis and action pack. No automatic annual renewal or recurring billing is included.

For a $120,000 salary with a 50% match up to 6% of pay, contributing 4% models a $2,400 match; contributing 6% models $3,600 before plan-specific limits and timing.

Your employer or retirement-plan administrator may provide match information and calculators at no charge.

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
| `POST` | `/api/life-events` | Receive a fanned-out life event from the shared bus. |
| `POST` | `/api/plans` | Advanced path: full intake in one call. Plan stored; pack locked. |
| `POST` | `/api/plans/draft` | Golden path: start the conversational intake. Returns the first question. |
| `POST` | `/api/plans/draft/{draft_id}/answer` | Answer the current draft question; advances the conversation or finishes the plan. |
| `GET` | `/api/plans/{plan_id}` | Get plan summary |
| `POST` | `/api/plans/{plan_id}/billing/setup` | Create the Stripe customer + SetupIntent so the user can save a card. |
| `GET` | `/api/plans/{plan_id}/billing/status` | Pollable billing state: card state + whether the $99 one-time fee is settled. |
| `POST` | `/api/plans/{plan_id}/pay` | Charge the flat $99 one-time fee off-session against the saved card. |
| `GET` | `/api/plans/{plan_id}/pack` | Full fix plan. Locked (402) until the $99 one-time fee is paid. |
| `DELETE` | `/api/data` | Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger. |

The OpenAPI spec in `../../openapi/401k-match.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/401k-match`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- The model covers a simple percentage match up to a percentage of pay; it does not model every tiered or discretionary plan.
- Annualized figures are not a midyear payroll plan. Prior contributions, changing pay, catch-up eligibility, vesting, and payroll timing may change the result.
- Qull does not access retirement accounts, change payroll elections, choose investments, or guarantee employer contributions.

Financial education only; no investment, tax, fiduciary, or individualized financial advice.

## Data handled

Salary, pay frequency, contribution/match percentages, true-up assumption, optional name/employer details, calculated plan, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/matchmax/)
- [Integration and schema reference](https://qull.io/connect/matchmax/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/matchmax/privacy/)
- [Terms — draft](https://qull.io/connect/matchmax/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
