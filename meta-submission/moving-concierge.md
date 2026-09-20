# Moving Concierge — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **Moving Concierge**
- Description: Turn your move details into an address-change pack with a checklist, relevant official links, and a PDF you can work through at your own pace.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/moving-concierge/
- Privacy URL: https://qull.io/connect/moving-concierge/privacy/
- Terms URL: https://qull.io/connect/moving-concierge/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Make an address-change checklist for my move.
- Show me which official sites I need to update after moving.
- Track which address changes I have completed.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/moving-concierge/api` |
| OpenAPI specification | `https://qull.io/connect/moving-concierge/api-docs/moving-concierge.json` |
| API/MCP documentation | `https://qull.io/connect/moving-concierge/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/moving-concierge` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. U.S. move planning and supported state address-update resources.

USD $49 once for the complete address-change pack for one move. It is not a subscription, a mover booking, or payment of third-party fees.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- An address-change checklist tailored to the move information you provide.
- Official links and instructions for common address updates.
- A downloadable pack and a tracker for pending, completed, or inapplicable tasks.

### Boundaries that must stay in the listing

- Qull does not file a USPS change, update bank or government records, book movers, switch utilities, or coordinate providers.
- The pack does not cover every organization you may need to notify. Add your own tasks and check current official deadlines.
- The pack is unlocked only after successful payment. Saving a card alone does not unlock it.

You can update addresses directly with each organization without buying a Qull pack.

## Evidence required before submission

- Deployed build and generated OpenAPI match the reviewed commit.
- Two independently provisioned users pass access-isolation and deletion checks.
- The create → deliverable → outcome/purchase flow works in the intended platform.
- Stripe test mode demonstrates setup, success, failure, authentication-required,
  retry, and duplicate-submission behavior without any live charge.
- Support contact can receive requests; final privacy/terms and required fee/data
  handling review are complete.
- Reviewer credential, exact example inputs, and a genuine unedited walkthrough
  are prepared. Do not commit review secrets or real customer records.
- Meta's own review is complete; no wording in this worksheet promises acceptance.
