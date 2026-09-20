# Medical Bill Fighter — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **Medical Bill Fighter**
- Description: Review bill and EOB details you enter for a limited set of potential billing issues, then prepare a dispute-letter PDF to send to the billing office yourself.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/medical-bill-fighter/
- Privacy URL: https://qull.io/connect/medical-bill-fighter/privacy/
- Terms URL: https://qull.io/connect/medical-bill-fighter/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Check these itemized bill and EOB figures for the issues your rules support.
- Explain why this bill line was flagged so I can ask the provider about it.
- Prepare a billing-dispute letter for me to review and send.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/medical-bill-fighter/api` |
| OpenAPI specification | `https://qull.io/connect/medical-bill-fighter/api-docs/medical-bill-fighter.json` |
| API/MCP documentation | `https://qull.io/connect/medical-bill-fighter/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/medical-bill-fighter` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. Structured U.S. medical-billing information and the checks supported by the current rule set.

25% of the actual bill reduction you report and confirm. A flagged item is not a verified error or a guaranteed saving.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- Potential issue flags from supported duplicate-line and bill/EOB consistency checks.
- An explanation of the facts that triggered each flag.
- A dispute-letter PDF and a record of the result you report.

### Boundaries that must stay in the listing

- Qull does not contact providers, negotiate balances, access insurer accounts, submit insurance appeals, or pay bills for you.
- The rules are limited and can miss issues or flag legitimate charges. Unverified code-pair rules are disabled; this is not a complete coding, clinical, or insurance audit.
- Do not provide Social Security numbers, insurer passwords, full medical records, or unnecessary diagnosis information.

You can ask the provider or insurer to explain or correct a bill directly without using Qull.

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
