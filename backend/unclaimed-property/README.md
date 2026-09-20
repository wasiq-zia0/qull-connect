# Found Money

Build a state-by-state claim checklist and use the NAUPA directory to reach official unclaimed-property programs where you can search and file your own claims.

**Current scope:** U.S. state/DC claim checklists with a NAUPA program-directory entry point. Historical state links require review. Billing setup and recovery-fee collection are disabled pending a compliant agreement flow.

## Deliverables

- The verified NAUPA directory entry point for locating official state programs, alongside state-specific checklists.
- A claim-preparation checklist and cover sheet using the details you provide.
- A tracker for claim steps and outcomes you report.

## What the user supplies

- Full legal name and optional former names
- States where you lived
- Contact information for your claim pack
- Optional date of birth only with explicit consent; never a Social Security number

## Customer workflow

1. **List your states.** Add the states where you have lived and the names under which property may be held.
2. **Find the official program.** Use the NAUPA directory to reach the current state program and run your search yourself. A checklist is not evidence that money was found.
3. **Prepare your claim.** Use the document checklist and follow the state's official filing instructions.
4. **Track the result.** Record claim progress and actual recoveries. No recovery fee is collected in this release.

## Price and collection

The current release provides portal guides and claim preparation without collecting a fee. Paid recovery assistance is unavailable while state-specific agreements and eligibility requirements are unresolved. A proposed future fee of up to 10% is not active or authorized.

Searching and filing directly through an official state program is free. This release does not collect a Qull recovery fee.

You can search and claim your own property through official state programs for free.

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
| `GET` | `/api/states` | List states |
| `POST` | `/api/life-events` | Receive a fanned-out life event from the shared bus. |
| `POST` | `/api/searches` | Create search |
| `PATCH` | `/api/searches/{search_id}` | Conversational step: complete a draft search (the 'one tap' after the nudge) |
| `GET` | `/api/searches/{search_id}` | Get search |
| `GET` | `/api/searches/{search_id}/claim-pack` | Claim pack |
| `PATCH` | `/api/searches/{search_id}/states/{abbr}` | Update state status |
| `POST` | `/api/searches/{search_id}/billing/setup` | Billing setup |
| `GET` | `/api/searches/{search_id}/billing/status` | Pollable billing state: card state + whether the recovery fee is done. |
| `POST` | `/api/searches/{search_id}/recovery-confirmed` | Recovery confirmed |
| `DELETE` | `/api/data` | Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger. |

The OpenAPI spec in `../../openapi/unclaimed-property.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/unclaimed-property`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- Qull does not search state databases automatically, verify a property match, file claims, or receive recovered money for you.
- Historical per-state reference links are unreviewed; use the NAUPA directory to locate the current official program. Claim steps are general guidance, not verified state-specific requirements.
- A single percentage cap does not establish compliance. Agreement timing, registration, disclosures, and other state requirements may apply.
- Do not send Qull Social Security numbers, identity-document scans, or bank-account credentials.

Claim-preparation information; no legal advice or assurance that unclaimed funds exist.

## Data handled

Legal/former names, selected states, contact details, optional consented birth date, claim statuses, reported recovery, and Stripe references if billing is available.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/found-money/)
- [Integration and schema reference](https://qull.io/connect/found-money/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/found-money/privacy/)
- [Terms — draft](https://qull.io/connect/found-money/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
