# Subscription Slayer — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **Subscription Slayer**
- Description: Identify recurring charges in receipts you provide, get merchant-specific cancellation instructions, and track the cancellations you complete.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/subscription-slayer/
- Privacy URL: https://qull.io/connect/subscription-slayer/privacy/
- Terms URL: https://qull.io/connect/subscription-slayer/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Find likely recurring subscriptions in these receipts.
- Show me how to cancel this subscription myself.
- Track the subscriptions I have cancelled and the savings I report.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/subscription-slayer/api` |
| OpenAPI specification | `https://qull.io/connect/subscription-slayer/api-docs/subscription-slayer.json` |
| API/MCP documentation | `https://qull.io/connect/subscription-slayer/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/subscription-slayer` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. Supported receipt data and merchant guides. Merchant procedures, notice periods, and cancellation fees vary.

USD $10 for each completed cancellation you select and explicitly confirm for billing. No recurring Qull subscription fee.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- A list of likely recurring subscriptions detected from your supplied receipts.
- A cancellation guide and merchant link where available, with a generic guide for unsupported merchants.
- A record of subscription status and the monthly savings you report.

### Boundaries that must stay in the listing

- Receipt matching can miss subscriptions or mistake repeated purchases for subscriptions. Review every result.
- Qull does not log into merchant accounts, cancel on your behalf, guarantee refunds, or override merchant terms.
- Live Gmail access is unavailable. Import your own receipts or enter subscriptions directly; fixture scans are development demonstrations and do not establish real subscriptions.

You can cancel directly with the merchant without using or paying Qull.

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
