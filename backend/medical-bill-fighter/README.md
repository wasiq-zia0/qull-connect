# Medical Bill Fighter

Review bill and EOB details you enter for a limited set of potential billing issues, then prepare a dispute-letter PDF to send to the billing office yourself.

**Current scope:** Structured U.S. medical-billing information and the checks supported by the current rule set.

## Deliverables

- Potential issue flags from supported duplicate-line and bill/EOB consistency checks.
- An explanation of the facts that triggered each flag.
- A dispute-letter PDF and a record of the result you report.

## What the user supplies

- Patient and provider contact details needed for a letter
- Bill total, line-item dates, codes, descriptions, and amounts
- Relevant insurer explanation-of-benefits figures
- Original and revised balance if the provider changes it

## Customer workflow

1. **Enter relevant billing details.** Use the itemized bill and EOB. Omit medical histories and identifiers the review does not need.
2. **Review the flags.** Treat each finding as a question to check with the provider, not a proven billing error.
3. **Send your letter.** Review the generated PDF and send it yourself to the provider or billing office.
4. **Confirm a reduction.** Use the revised statement to record actual savings and review the fee before authorizing it.

## Price and collection

25% of the actual bill reduction you report and confirm. A flagged item is not a verified error or a guaranteed saving.

If a provider reduces your bill by $800 and you confirm that reduction, the fee is $200 and the net saving is $600.

You can ask the provider or insurer to explain or correct a bill directly without using Qull.

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
| `POST` | `/api/life-events` | Receive a fanned-out life event. Creates a draft case and returns the nudge. |
| `POST` | `/api/cases/draft` | Start the one-question-at-a-time bill check. |
| `POST` | `/api/cases/{case_id}/answer` | Answer the current draft question; advances the conversation. |
| `POST` | `/api/cases` | Create case |
| `GET` | `/api/cases/{case_id}` | Get case |
| `GET` | `/api/cases/{case_id}/pack` | Get pack |
| `GET` | `/api/packs` | List packs |
| `POST` | `/api/cases/{case_id}/outcome` | Report outcome |
| `POST` | `/api/cases/{case_id}/billing/setup` | Create the Stripe customer + SetupIntent. |
| `GET` | `/api/cases/{case_id}/billing/status` | Pollable billing state: card state + whether the 25% reduction fee is settled. |
| `GET` | `/api/cases/{case_id}/fee-quote` | Quote 25% of this case's stored, user-reported reduction without charging. |
| `POST` | `/api/cases/{case_id}/reduction-confirmed` | Charge 25% of the user-confirmed reduction off-session. |
| `DELETE` | `/api/data` | Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger. |

The OpenAPI spec in `../../openapi/medical-bill-fighter.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/medical-bill-fighter`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- Qull does not contact providers, negotiate balances, access insurer accounts, submit insurance appeals, or pay bills for you.
- The rules are limited and can miss issues or flag legitimate charges. Unverified code-pair rules are disabled; this is not a complete coding, clinical, or insurance audit.
- Do not provide Social Security numbers, insurer passwords, full medical records, or unnecessary diagnosis information.

Billing-organization assistance; no medical, legal, insurance, or clinical coding advice.

## Data handled

Patient/provider contact details, bill and EOB figures, line-item codes/descriptions, potential issue flags, generated letter, reported reduction, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/medical-bill-fighter/)
- [Integration and schema reference](https://qull.io/connect/medical-bill-fighter/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/medical-bill-fighter/privacy/)
- [Terms — draft](https://qull.io/connect/medical-bill-fighter/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
