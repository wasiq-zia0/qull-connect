# Qull Connect — actual service catalog

Operated by Qull, Inc. Published contact: **wasiq@qull.io**.
These are assisted workflows. The user takes the action with the landlord,
employer, airline, merchant, provider, plan administrator or state program.
The backend does not independently send, negotiate, cancel, file or transfer funds.

| Connector | Backend slug | Fee / release status | Current deliverable |
|---|---|---|---|
| Deposit Recovery | `deposit-recovery` | 25% of the deposit recovery you confirm | Organize your tenancy details, review an estimated state return deadline, and prepare a demand-letter PDF you can send to your landlord. |
| FlightPay | `eu261-flight-comp` | 30% of the compensation payout you confirm | Check a flight disruption against the supported EU261 rules and prepare a claim pack to send to the airline yourself. |
| Subscription Slayer | `subscription-slayer` | $10 per completed cancellation you confirm | Identify recurring charges in receipts you provide, get merchant-specific cancellation instructions, and track the cancellations you complete. |
| BillCut | `bill-negotiator` | 35% of confirmed savings, over at most 12 months | Turn your current internet, cable, or phone bill into a practical call-and-chat script, then track a lower rate you negotiate yourself. |
| Final Paycheck Recovery | `final-paycheck` | 25% of recovered wages you confirm | Organize your separation and wage details, review a deadline estimate, and prepare a final-pay demand letter you can send yourself. |
| Class Action Cash | `class-action-cash` | 20% of the settlement payout you confirm | Match receipts you provide against the available settlement catalog and prepare a claim-information pack for you to review and file on the official site. |
| Found Money | `unclaimed-property` | Free preview program directory and claim preparation | Build a state-by-state claim checklist and use the NAUPA directory to reach official unclaimed-property programs where you can search and file your own claims. |
| Moving Concierge | `moving-concierge` | $49 one-time, per move pack | Turn your move details into an address-change pack with a checklist, relevant official links, and a PDF you can work through at your own pace. |
| MatchMax | `401k-match` | $99 one-time, per analysis and action pack | Model a simple employer-match formula, compare contribution amounts, and get an explanation of the calculation and steps to discuss with your plan administrator. |
| Medical Bill Fighter | `medical-bill-fighter` | 25% of the bill reduction you confirm | Review bill and EOB details you enter for a limited set of potential billing issues, then prepare a dispute-letter PDF to send to the billing office yourself. |

## Shared access

- API origin: `https://5.78.152.6.nip.io/<slug>`; business paths include `/api`.
- Authentication: per-user opaque key in `Authorization: Bearer ...`.
- Provisioning: operator-controlled; no public signup/OAuth or confirmed Meta
  identity exchange is implemented.
- Default rate limits: per socket IP,120 requests/minute;20/minute for payment
  and PDF operations; maximum request body1,000,000 bytes.
- Payments: hosted Stripe setup, provider-verified saved method, exact fee
  confirmation and provider-confirmed settlement. FlightPay uses EUR; other
  paid services use USD. No automatic renewal or tax-calculation system.
- Data: owner-scoped operational records; generated documents and outcomes are
  not published. Financial records and deletion have separate handling.

See each service README for inputs, outputs and endpoints, and the generated
OpenAPI for exact request schemas. Policies are drafts and Meta review remains
separate from a local test or a working health endpoint.

## Commercial boundaries

Found Money's fee endpoints are disabled pending a lawful agreement flow.
All other stated fees remain subject to service availability, final terms,
applicable domain/fee review, deployed verification and explicit user authorization.
Do not market template preparation as lawyer representation, a provider negotiation,
a completed cancellation, a filed claim, or a recovered payment.
