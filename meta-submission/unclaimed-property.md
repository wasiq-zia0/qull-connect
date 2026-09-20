# Found Money — Muse submission worksheet

**Review draft. Do not represent deployment or approval as complete.**

## Overview

- Display name: **Found Money**
- Description: Build a state-by-state claim checklist and use the NAUPA directory to reach official unclaimed-property programs where you can search and file your own claims.
- Operator: Qull, Inc.
- Contact name: Muhammad Wasiq Zia
- Work/contact email: `wasiq@qull.io` (published Qull contact; receiving/inbox access must be verified).
- Support URL: `mailto:wasiq@qull.io`; use the email field if the form does not accept mailto URLs.
- Product URL: https://qull.io/connect/found-money/
- Privacy URL: https://qull.io/connect/found-money/privacy/
- Terms URL: https://qull.io/connect/found-money/terms/
- Payments: Do not select “accepts payments” for this release; collection is disabled.

Example prompts:

- Show me the official unclaimed-property sites for states where I lived.
- Build a checklist for filing an unclaimed-property claim myself.
- Help me track a claim I filed with the state.

## Technical specs

| Form field | Value |
|---|---|
| Connection type | Raw API |
| API URL | `https://5.78.152.6.nip.io/unclaimed-property/api` |
| OpenAPI specification | `https://qull.io/connect/found-money/api-docs/unclaimed-property.json` |
| API/MCP documentation | `https://qull.io/connect/found-money/api-docs/` |
| Authentication method | API keys — `Authorization: Bearer <per-user key>` |

The API URL above denotes the `/api` route prefix for the form. In the OpenAPI
file, `servers.url` ends at `/unclaimed-property` because paths already contain `/api`.
Confirm Muse's API-URL interpretation during integration; do not silently add
another `/api` to the generated client.

### Access requirements

An operator-provisioned Qull credential is required and is bound to one end
user. No public signup or OAuth flow is implemented. U.S. state/DC claim checklists with a NAUPA program-directory entry point. Historical state links require review. Billing setup and recovery-fee collection are disabled pending a compliant agreement flow.

The current release provides portal guides and claim preparation without collecting a fee. Paid recovery assistance is unavailable while state-specific agreements and eligibility requirements are unresolved. A proposed future fee of up to 10% is not active or authorized.

Hosted Stripe payment-method setup and an explicit, amount-specific payment
confirmation are required before a fee is collected. Default request limits:
120 requests/minute per socket IP, 20/minute for payment-sensitive and PDF
routes, and a 1,000,000-byte maximum request body. Actual deployment settings and headers
control. The platform-to-user credential handoff still needs to be agreed and
tested with Meta; a shared key must not mix multiple users' data.

### Reviewer notes: exact deliverable

- The verified NAUPA directory entry point for locating official state programs, alongside state-specific checklists.
- A claim-preparation checklist and cover sheet using the details you provide.
- A tracker for claim steps and outcomes you report.

### Boundaries that must stay in the listing

- Qull does not search state databases automatically, verify a property match, file claims, or receive recovered money for you.
- Historical per-state reference links are unreviewed; use the NAUPA directory to locate the current official program. Claim steps are general guidance, not verified state-specific requirements.
- A single percentage cap does not establish compliance. Agreement timing, registration, disclosures, and other state requirements may apply.
- Do not send Qull Social Security numbers, identity-document scans, or bank-account credentials.

You can search and claim your own property through official state programs for free.

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
