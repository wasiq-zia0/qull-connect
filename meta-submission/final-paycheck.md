# Final Paycheck Recovery — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **Final Paycheck Recovery**
- Description: Organize your separation and wage details, review a deadline estimate, and prepare a final-pay demand letter you can send yourself.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/final-paycheck/
- Privacy URL: https://qull.io/connect/final-paycheck/privacy/
- Terms URL: https://qull.io/connect/final-paycheck/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Help me organize an unpaid final paycheck case.
- Prepare a final-pay demand letter for me to review and send.
- Explain the assumptions in this state's final-pay deadline estimate.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/final-paycheck/api` |
| OpenAPI specification | `https://qull.io/connect/final-paycheck/api-docs/final-paycheck.json` |
| API/MCP documentation | `https://qull.io/connect/final-paycheck/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/final-paycheck` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. U.S. final-paycheck document preparation. A limited reviewed Nevada discharge rule supports a deadline estimate; other state/separation scenarios require review and produce factual wage requests.

25% of the actual recovered wages you confirm. No recovery confirmation means no recovery fee.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- A case record with unpaid amount and job-separation details.
- A deadline estimate only for the reviewed Nevada discharge scenario; other cases show that review is required.
- A demand-letter PDF for you to check and send.

### Boundaries that must stay in the listing

- Qull does not send letters, contact employers, file wage claims, represent you, or calculate every available penalty.
- The compiled deadline data is not a complete legal analysis. Coverage depends on the employment facts and current law.
- You can use government wage-claim channels or seek qualified advice without using Qull.

State and federal labor agencies may offer wage-claim information and filing channels without a Qull fee.

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
