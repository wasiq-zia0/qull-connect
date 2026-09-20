# FlightPay

Check a flight disruption against the supported EU261 rules and prepare a claim pack to send to the airline yourself.

**Current scope:** Supported EU261 flight-disruption scenarios; coverage depends on route, operating carrier, timing, and circumstances.

## Deliverables

- An initial eligibility result based on the route, carrier, disruption, and facts you provide.
- A claim-letter PDF and supporting checklist.
- Claim and billing status linked to your case.

## What the user supplies

- Passenger name and contact information
- Flight number, airline, route, and travel date
- Delay or cancellation details and the carrier's stated reason
- Booking reference and actual payout if received

## Customer workflow

1. **Describe the disruption.** Supply the flight details and what happened, using your booking and airline messages.
2. **Check the assessment.** Review coverage assumptions and missing facts. The airline may reach a different conclusion.
3. **Submit the pack.** Review the PDF and submit it directly through the airline's claim channel.
4. **Confirm actual payment.** Only record compensation you received. Review the resulting EUR fee before authorizing it.

## Price and collection

30% of the actual compensation payout you confirm, billed in EUR. The fee is shown before you authorize payment.

If an airline pays €400 and you confirm it, the fee is €120 and you keep €280.

You can submit an EU261 claim directly to the airline without using Qull.

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
| `POST` | `/api/life-events` | Receive a fanned-out life event. Creates a draft claim and returns the nudge. |
| `POST` | `/api/claims` | Intake a flight disruption and return the EU261 eligibility verdict. |
| `GET` | `/api/claims/{cid}` | Eligibility verdict + tier + amount for a stored claim. |
| `POST` | `/api/claims/{cid}/claim-pack` | Generate the claim letter/form pack PDF. Only available when eligible. |
| `POST` | `/api/claims/{cid}/billing/setup` | Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge. |
| `GET` | `/api/claims/{cid}/billing/status` | Verify saved-card setup with Stripe; a pending checkout is never a saved card. |
| `POST` | `/api/claims/{cid}/payout-confirmed` | User confirms the airline paid out: charge the 30% contingency fee off-session. |
| `GET` | `/api/claims/{cid}/claim-pack.pdf` | Download claim pack |
| `POST` | `/api/claims/{cid}/confirm` | Confirm claim |
| `GET` | `/api/me/data` | Export only the authenticated caller's local records. |
| `DELETE` | `/api/me/data` | Delete local records and documents. Stripe/payment audit records remain separately retained. |
| `POST` | `/api/claims/{cid}/billing/quote` | Show the exact rounded fee for review; no card setup, confirmation or payment occurs. |

The OpenAPI spec in `../../openapi/eu261-flight-comp.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/eu261-flight-comp`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- This is a preliminary rules-based assessment, not a legal determination or guaranteed compensation.
- The connector does not monitor live flight data, submit claims to airlines, negotiate, or handle litigation.
- The current rules focus on supported EU261 scenarios. Connecting itineraries, extraordinary circumstances, rerouting, and other regimes can require separate review.

General claim-preparation information; no legal advice or representation.

## Data handled

Passenger/contact details, itinerary, booking reference, disruption facts, generated claim documents, reported payout, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/flightpay/)
- [Integration and schema reference](https://qull.io/connect/flightpay/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/flightpay/privacy/)
- [Terms — draft](https://qull.io/connect/flightpay/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
