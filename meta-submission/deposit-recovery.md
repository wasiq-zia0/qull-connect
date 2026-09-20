# Deposit Recovery — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **Deposit Recovery**
- Description: Organize your tenancy details, review an estimated state return deadline, and prepare a demand-letter PDF you can send to your landlord.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/deposit-recovery/
- Privacy URL: https://qull.io/connect/deposit-recovery/privacy/
- Terms URL: https://qull.io/connect/deposit-recovery/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Help me organize the facts about my overdue rental deposit.
- Prepare a deposit demand letter for me to review and send.
- Show me the estimated return deadline and the assumptions behind it.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/deposit-recovery/api` |
| OpenAPI specification | `https://qull.io/connect/deposit-recovery/api-docs/deposit-recovery.json` |
| API/MCP documentation | `https://qull.io/connect/deposit-recovery/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/deposit-recovery` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. U.S. residential rental deposits. Limited deadline estimates are currently supported for reviewed California, Connecticut, and Texas rules; other states require review and use factual request letters.

25% of the recovered deposit amount you confirm. No recovery confirmation means no recovery fee.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- A case summary with the information used to estimate the return deadline.
- A letter PDF using your tenancy details; unreviewed state rules produce a neutral request without unsupported legal assertions.
- A record of your letter status, reported recovery, and fee status.

### Boundaries that must stay in the listing

- The service prepares documents; it does not contact your landlord, mail letters, negotiate, file in court, or provide representation.
- Only specifically reviewed rule paths provide deadline estimates. Other states or incomplete facts require manual review; lease terms, notices, local rules, and exceptions can change the result.
- Deadline status is calculated when requested. A live email watcher, scheduled mailing service, and automated reminders are not included.

You can contact your landlord yourself and use your state or local tenant resources without paying Qull.

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
