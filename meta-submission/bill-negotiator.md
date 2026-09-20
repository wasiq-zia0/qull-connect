# BillCut — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **BillCut**
- Description: Turn your current internet, cable, or phone bill into a practical call-and-chat script, then track a lower rate you negotiate yourself.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/billcut/
- Privacy URL: https://qull.io/connect/billcut/privacy/
- Terms URL: https://qull.io/connect/billcut/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Help me prepare a call about my higher internet bill.
- Give me a retention-chat script for my provider.
- Calculate savings from the new rate my provider offered.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/bill-negotiator/api` |
| OpenAPI specification | `https://qull.io/connect/billcut/api-docs/bill-negotiator.json` |
| API/MCP documentation | `https://qull.io/connect/billcut/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/bill-negotiator` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. Internet, cable, and phone bills supported by the script library or generic fallback.

35% of the documented savings you confirm: (old monthly bill − new monthly bill) × agreed months, capped at 12 months. No positive confirmed savings means no fee.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- Provider-specific call and chat scripts where available.
- Talking points for asking about promotions, retention offers, and avoidable fees.
- A savings calculation based on the outcome and rate duration you report.

### Boundaries that must stay in the listing

- Qull supplies scripts; it does not call providers, access your account, or negotiate on your behalf.
- Savings are based on the figures you report, not an independent connection to provider billing.
- A lower headline rate may come with taxes, equipment charges, termination fees, or a new contract. Check the full offer.

You can ask your provider for a lower rate directly without paying Qull.

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
