# FlightPay — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **FlightPay**
- Description: Check a flight disruption against the supported EU261 rules and prepare a claim pack to send to the airline yourself.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/flightpay/
- Privacy URL: https://qull.io/connect/flightpay/privacy/
- Terms URL: https://qull.io/connect/flightpay/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Check whether my delayed flight fits the EU261 rules this service supports.
- Prepare a flight-compensation claim pack for me to send to the airline.
- Explain which facts I need before assessing my cancelled flight.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/eu261-flight-comp/api` |
| OpenAPI specification | `https://qull.io/connect/flightpay/api-docs/eu261-flight-comp.json` |
| API/MCP documentation | `https://qull.io/connect/flightpay/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/eu261-flight-comp` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. Supported EU261 flight-disruption scenarios; coverage depends on route, operating carrier, timing, and circumstances.

30% of the actual compensation payout you confirm, billed in EUR. The fee is shown before you authorize payment.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- An initial eligibility result based on the route, carrier, disruption, and facts you provide.
- A claim-letter PDF and supporting checklist.
- Claim and billing status linked to your case.

### Boundaries that must stay in the listing

- This is a preliminary rules-based assessment, not a legal determination or guaranteed compensation.
- The connector does not monitor live flight data, submit claims to airlines, negotiate, or handle litigation.
- The current rules focus on supported EU261 scenarios. Connecting itineraries, extraordinary circumstances, rerouting, and other regimes can require separate review.

You can submit an EU261 claim directly to the airline without using Qull.

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
