# Qull Connect — documented workflows

Company: Qull, Inc. Founder: Muhammad Wasiq Zia.

These descriptions reflect the supplied API contracts, not independently verified service delivery. Pricing is the stated commercial model; fee enforceability and implemented collection remain to be reviewed.

Application API prefix: `https://5.78.152.6.nip.io/<slug>/api`.
Specs: `openapi/<slug>.json`.

The original kit describes API-key authentication, 120 requests/minute per client, 20 payment requests/minute and a 1 MB request limit. The implementation details and enforcement are unverified.

| Connector | Slug | Stated pricing | Workflow described by the API |
|---|---|---|---|
| Deposit Recovery | deposit-recovery | 25% of recovery | State information, case intake and demand-letter preparation. The user reviews and sends the letter, then confirms recovery. |
| FlightPay | eu261-flight-comp | 30% of compensation | Eligibility assessment and claim-pack generation for flight disruptions; user confirms payout. Airline submission and collection are not established. |
| Subscription Slayer | subscription-slayer | $10 per completed cancellation | Subscription intake/listing and cancellation instructions; user-confirmed cancellation triggers billing. Provider-side cancellation is not established. |
| BillCut | bill-negotiator | 35% of documented savings over at most 12 months | Generates a negotiation script; user reports an outcome and confirms savings. Provider negotiations are not established. |
| Final Paycheck Recovery | final-paycheck | 25% of recovered wages | State information, case intake, demand-letter preparation and user-confirmed recovery. Letter delivery and wage collection are not established. |
| Class Action Cash | class-action-cash | 20% of payout | Settlement scanning, matching and claim-pack preparation; user confirms payout. Claim filing is not established. |
| Found Money | unclaimed-property | Up to 10%, subject to state rules | Search intake, state status tracking and claim-pack preparation. Live state-database search and filing require verification. |
| Moving Concierge | moving-concierge | $49 per move | Move intake, checklist and planning pack. Mover booking or coordination is not established. |
| MatchMax | 401k-match | $99/year | Conversational intake and a paid employer-match education/calculation pack. Renewal/subscription behavior requires verification. |
| Medical Bill Fighter | medical-bill-fighter | 25% of reduction | Bill intake, assistance pack and user-reported outcome/reduction. Provider negotiations are not established. |

Contingency models depend on verified recovery/savings and user confirmation. Moving Concierge and MatchMax use separate flat-fee models; the general “nothing recovered = nothing charged” statement does not describe them.

The intended services include template assistance and education. Disclaimers alone do not establish legal compliance; final terms must match the delivered service, data handling and supported jurisdictions.
