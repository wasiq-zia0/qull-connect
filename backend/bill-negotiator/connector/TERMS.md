# Bill Negotiator — Terms of Service

**Provider:** Qull / Wasiq (placeholder business details)
**Support:** support@qull.io
**Governing law:** Ontario, Canada

These are plain-language draft terms, not legal advice. Counsel must confirm the support contact and governing law before launch.

## 1. What the service does

Bill Negotiator helps you lower your internet, cable, or phone bill by giving you
per-provider negotiation scripts: a call script, a chat script, and talking points
(ask for the retention/loyalty department, mention competitor pricing, ask for
promotions and fee waivers).

**What it does NOT do:**
- It never contacts your provider on your behalf. You make the call or chat yourself.
- It cannot guarantee any savings. Providers decide what offers you get.
- The scripts are negotiation guidance only — not legal advice and not financial advice.

## 2. Fees

You pay **nothing** unless you save money.

- Fee: **35% of your documented bill savings**, charged **only after you confirm**
  the new lower bill through the connector.
- Documented savings = (your old monthly bill − your new monthly bill) × the number
  of months the new rate is locked (capped at 12).
- Example: bill drops from $120/mo to $85/mo locked for 12 months → $420 documented
  savings → fee $147.00.
- If your negotiation produces no savings, you owe nothing. Reporting "no success"
  is free.

The exact fee is shown to you in plain language before you save a payment card:
*"You will be charged 35% of your documented bill savings, only if you confirm the
new lower bill. No charge otherwise."*

## 3. Payment

- A payment card is saved through a Stripe SetupIntent before you negotiate, so the
  fee can be charged later.
- The fee is charged off-session to the saved card **only** when you confirm
  documented savings.
- Charges appear from the merchant name configured in Stripe (placeholder — set
  before launch).

## 4. Cancellation and refunds

- You can cancel at any time before a charge: just don't confirm savings, or close
  your case. No card charge happens without your confirmed outcome.
- If a charge was made in error (e.g., you reported the wrong new bill and the
  savings never materialized), contact support within 30 days and we will review
  and refund the fee if the savings were not real.
- Disputing the underlying provider bill is between you and your provider.

## 5. Your data

We store your case details (provider, bill amounts, tenure) and outcome, plus the
Stripe customer ID needed for billing. We never store full card numbers. We do not
sell your data.

## 6. Changes

We may update these terms; material changes to fees will be disclosed before they
take effect for new cases.

Last updated: 2026-09-19.
