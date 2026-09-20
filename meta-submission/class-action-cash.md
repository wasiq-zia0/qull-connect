# Class Action Cash — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **Class Action Cash**
- Description: Match receipts you provide against the available settlement catalog and prepare a claim-information pack for you to review and file on the official site.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/class-action-cash/
- Privacy URL: https://qull.io/connect/class-action-cash/privacy/
- Terms URL: https://qull.io/connect/class-action-cash/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Check these receipts against the currently supported settlement catalog.
- Explain this settlement's eligibility checklist before I file.
- Prepare a claim-information sheet for me to use on the official site.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/class-action-cash/api` |
| OpenAPI specification | `https://qull.io/connect/class-action-cash/api-docs/class-action-cash.json` |
| API/MCP documentation | `https://qull.io/connect/class-action-cash/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/class-action-cash` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. Only current, supported catalog entries. Closed, stale, or unverified entries must not be treated as active opportunities.

20% of the actual settlement payout you confirm for this service. Filing directly with a settlement administrator is free.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- Candidate matches to current, supported entries in the settlement catalog.
- An eligibility checklist, official filing link, and claim-information sheet.
- A record of the match and any payout you later confirm.

### Boundaries that must stay in the listing

- The catalog is curated, not a live or exhaustive feed. Only entries with a currently verified official source and an open deadline are considered; availability changes as entries expire.
- A receipt match does not establish eligibility, certify your claim, or guarantee payment.
- Qull does not submit claims, sign declarations, send email, or communicate with administrators for you.

Official settlement claim filing is free. You can find and file eligible claims directly without paying Qull.

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
