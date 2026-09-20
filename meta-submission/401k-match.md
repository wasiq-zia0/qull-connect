# MatchMax — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **MatchMax**
- Description: Model a simple employer-match formula, compare contribution amounts, and get an explanation of the calculation and steps to discuss with your plan administrator.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/matchmax/
- Privacy URL: https://qull.io/connect/matchmax/privacy/
- Terms URL: https://qull.io/connect/matchmax/terms/
- Payments: Choose “My connector accepts payments” only once the reviewed payment flow is deployed, test-mode verified, and the fee arrangement is cleared.

Example prompts:

- Explain how my employer's stated 401(k) match formula works.
- Compare my current contribution with the rate in my plan's match formula.
- Show the per-paycheck math and the assumptions I should check with HR.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/401k-match/api` |
| OpenAPI specification | `https://qull.io/connect/matchmax/api-docs/401k-match.json` |
| API/MCP documentation | `https://qull.io/connect/matchmax/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/401k-match` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. Educational calculations for the supported U.S. employer-match formula. Confirm all rules with your plan administrator.

USD $99 once for a plan's detailed analysis and action pack. No automatic annual renewal or recurring billing is included.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- A transparent calculation of current contributions and modeled employer matching.
- A comparison of the current rate with the rate needed for the supported formula.
- A paid action pack with per-paycheck figures and questions for your plan administrator.

### Boundaries that must stay in the listing

- The model covers a simple percentage match up to a percentage of pay; it does not model every tiered or discretionary plan.
- Annualized figures are not a midyear payroll plan. Prior contributions, changing pay, catch-up eligibility, vesting, and payroll timing may change the result.
- Qull does not access retirement accounts, change payroll elections, choose investments, or guarantee employer contributions.

Your employer or retirement-plan administrator may provide match information and calculators at no charge.

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
