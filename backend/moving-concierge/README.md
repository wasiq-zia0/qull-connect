# Moving Concierge

Turn your move details into an address-change pack with a checklist, relevant official links, and a PDF you can work through at your own pace.

**Current scope:** U.S. move planning and supported state address-update resources.

## Deliverables

- An address-change checklist tailored to the move information you provide.
- Official links and instructions for common address updates.
- A downloadable pack and a tracker for pending, completed, or inapplicable tasks.

## What the user supplies

- Name and contact email
- Old and new addresses
- Move date and destination state
- Checklist items you complete or mark inapplicable

## Customer workflow

1. **Add move details.** Enter the addresses, date, and destination state.
2. **Check the pack scope.** Review what the pack includes and decide whether it suits your move before purchasing.
3. **Purchase the pack.** Review the one-time $49 fee, save a payment method, and authorize payment.
4. **Complete each update.** Use the official links yourself and track your progress. Qull does not submit address changes.

## Price and collection

USD $49 once for the complete address-change pack for one move. It is not a subscription, a mover booking, or payment of third-party fees.

One move pack costs $49. USPS, movers, utilities, and other organizations may have separate fees paid directly to them.

You can update addresses directly with each organization without buying a Qull pack.

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
| `POST` | `/api/life-events` | Receive a fanned-out life event. A 'move' creates a draft move + the proactive nudge. |
| `POST` | `/api/moves` | Intake: build the move + checklist, emit the move event to the shared bus. |
| `GET` | `/api/moves/{move_id}` | Get move |
| `GET` | `/api/moves/{move_id}/pack` | Get pack |
| `PATCH` | `/api/moves/{move_id}/checklist` | Update checklist |
| `POST` | `/api/moves/{move_id}/billing/setup` | Create the Stripe customer + card SetupIntent. Honest fee disclosure BEFORE any charge. |
| `GET` | `/api/moves/{move_id}/billing/status` | Pollable billing state: card state + whether the $49 pack fee is settled. |
| `POST` | `/api/moves/{move_id}/pay` | Charge the flat $49.00 pack fee off-session against the saved card, then deliver the pack. |
| `DELETE` | `/api/data` | Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger. |

The OpenAPI spec in `../../openapi/moving-concierge.json` supplies exact request
models. The public server origin is `https://5.78.152.6.nip.io/moving-concierge`; operation paths
already contain `/api`. Do not compose `/api/api`.

## Important limits

- Qull does not file a USPS change, update bank or government records, book movers, switch utilities, or coordinate providers.
- The pack does not cover every organization you may need to notify. Add your own tasks and check current official deadlines.
- The pack is unlocked only after successful payment. Saving a card alone does not unlock it.

Administrative guidance; you complete and verify every address change yourself.

## Data handled

Name, email, old/new addresses, move date, destination state, checklist progress, generated pack, and Stripe references.

User records are scoped to the authenticated owner. Use documented deletion
operations where available. Local record deletion does not reverse payments
or erase Stripe's independent records. A final retention/backup policy and
support process remain operational launch requirements.

## Links

- [Product overview](https://qull.io/connect/moving-concierge/)
- [Integration and schema reference](https://qull.io/connect/moving-concierge/api-docs/)
- [Privacy policy — draft](https://qull.io/connect/moving-concierge/privacy/)
- [Terms — draft](https://qull.io/connect/moving-concierge/terms/)
- Contact: wasiq@qull.io (existing Qull contact; mailbox delivery not verified here).

A functioning local test is not Meta approval. See [review status](../../STATUS.md)
for the distinction between source changes, tests, deployment, and review.
