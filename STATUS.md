# Review and release status — September 20, 2026

This status distinguishes reviewed source from deployed behavior, an integrated
platform workflow, and an approved commercial service. Those are separate gates.

## Review work in this change

- Replaced trust in caller-supplied identity headers with owner-bound opaque keys,
  stored as hashes with expiry/revocation support.
- Reworked payment transport and persistence around hosted Stripe setup,
  server-verified payment methods, explicit fee confirmation, and retry-safe
  payment tracking. A completed redirect is not treated as proof of payment.
- Reviewed service workflows, ownership checks, input validation, generated
  documents, receipt ingestion, data deletion, and scope of compiled datasets.
- Removed unsupported statements that Qull sends letters, cancels subscriptions,
  negotiates, files claims, or searches live state databases.
- Rewrote the ten product pages, ten API guides, twenty policy pages and their
  legacy aliases, service READMEs, metadata and submission worksheets.
- Corrected MatchMax's product description to a one-time $99 analysis pack;
  there is no automatic annual renewal.
- Restricted Found Money to free portal/claim guides while the state-specific
  fee agreement and eligibility requirements remain unresolved.

See the checked-in test outputs and review notes for the exact checks performed.
Source changes do not establish production deployment or provider-side success.

## Verification evidence collected

All 70 local tests pass, including the ten connector workflow suites, shared
payment tests and security/deployment tests. All ten OpenAPI contracts and 111
MCP tool schemas were exported from the applications; the intake examples were
validated against those contracts. Container build/startup checks run in CI.

`verification/stripe-sandbox.json` records 22 real Stripe **sandbox** checks:
USD/EUR customer creation, hosted setup URL creation, rejection of incomplete
setup, verified SetupIntent/test payment-method attachment, missing-consent
rejection, simulated successful fees, currency, reuse of the same PaymentIntent
on retry, and rejection of an altered amount. These used Stripe test-mode funds;
no live charge was made.

`verification/stripe-provider-extra.json` records provider decline and
authentication-required checks. The hosted setup page rendered, but automatic
approval review blocked its final browser submission as a financial action
requiring user handoff. Browser completion remains unverified.

This does not establish browser completion of hosted Checkout, a production
deployment, a real customer's recovery, or a complete workflow inside Muse.
The final application/security test results should be read with the exact build
and environment recorded in the verification files.

## Required external evidence

| Gate | Current evidence / completion criterion |
|---|---|
| Backend deployment | Reviewed commit must be deployed; authenticated behavior, persistence and restart recovery must be verified on that exact build. |
| Stripe provider flow | Sandbox provider checks are recorded below; browser Checkout completion, decline/bank-authentication walkthroughs and deployed verification remain. No live charge is authorized by this review. |
| Muse integration | Confirm how Meta provisions/passes each user's credential and demonstrate the workflow in the actual platform. No Meta-specific OAuth or identity exchange has been established. |
| Legal and domain scope | Resolve the fee/data/representation issues in [DOMAIN_REVIEW.md](docs/DOMAIN_REVIEW.md), publish final policies, and limit unsupported jurisdictions or cases. |
| Support operations | Confirm the published contact receives requests; establish refunds, deletion requests, monitoring, backup/restore and incident handling. |
| Meta review | Complete Meta's own functional, security, legal and end-to-end review. Neither the repo nor this document grants approval. |
| Commercial launch | Verify user authorization, an actual deliverable, a correctly settled fee, support coverage and the customer acquisition path. Approval itself does not produce revenue. |

## What is not claimed

No claim that all ten connectors are approved, all jurisdictions are supported,
all curated data is current, the updated backend is deployed, or any customer's
money has been recovered. No paid live test was performed as part of drafting
these documents. Use the [review runbook](docs/REVIEW_RUNBOOK.md) to record real
results and the exact environment without overstating them.
