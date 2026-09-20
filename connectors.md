# Qull Connect — connector facts

Company: Qull, Inc. Founder: Muhammad Wasiq Zia.
Product line: 10 AI connectors for Meta's Muse directory. Each recovers or saves users money; Qull takes a contingency fee or flat fee via Stripe.

Live API base: `https://5.78.152.6.nip.io/<slug>/api`
Auth: API keys. Rate limits: 120 req/min per client (20/min on payment endpoints); 1 MB max request body.
OpenAPI specs: `openapi/<slug>.json` in this repo.

| # | Connector | Slug | Pricing | What it does |
|---|-----------|------|---------|--------------|
| 1 | Deposit Recovery | deposit-recovery | 25% contingency | Recovers rental security deposits; tracks state return deadlines, generates demand letters. US, all 50 states + DC. |
| 2 | FlightPay | eu261-flight-comp | 30% contingency | Claims EU261 compensation for delayed/cancelled flights. |
| 3 | Subscription Slayer | subscription-slayer | $10 per completed cancellation | Cancels unwanted subscriptions on the user's behalf. |
| 4 | BillCut | bill-negotiator | 35% of documented savings, capped at 12 months | Negotiates down recurring bills (internet, cable, phone). |
| 5 | Final Paycheck Recovery | final-paycheck | 25% contingency | Recovers unpaid final wages after job separation. |
| 6 | Class Action Cash | class-action-cash | 20% contingency | Finds class action settlements you're eligible for and files claims. |
| 7 | Found Money | unclaimed-property | Max 10%, subject to state rules | Finds unclaimed property in state databases and helps claim it. |
| 8 | Moving Concierge | moving-concierge | $49 flat per move | Move planning: checklists, timelines, mover coordination. |
| 9 | MatchMax | 401k-match | $99/year | 401(k) employer-match education and calculations. Education only — not financial advice. |
| 10 | Medical Bill Fighter | medical-bill-fighter | 25% of documented savings | Reviews medical bills for errors and negotiates them down. |

Contingency fees are charged only after the user confirms the recovery/saving. Nothing recovered = nothing charged.

Legal framing (applies where relevant): the recovery connectors are template automation, not law firms; no legal advice; no attorney-client relationship. MatchMax is education/calculation only, not financial advice, not a fiduciary. Medical Bill Fighter gives no medical advice.
