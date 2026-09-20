# Terms of Service — Medical Bill Fighter

_Last updated: 2026-09-19. Support: support@qull.io. Terms pending final review by counsel._

## What this service does

Medical Bill Fighter checks a medical bill you provide against a set of
automated error-detection rules (duplicate charges, bill-vs-EOB mismatches,
possible balance-billing situations, unbundling heuristics, missing itemized
detail) and generates template letter packs you can review, sign, and send
yourself: a billing-error dispute letter, an itemized-bill request, a
financial-assistance request, and a prompt-pay negotiation script.

## What it does NOT do

- It is **not a law firm and does not provide legal advice**. Findings use
  conditional language ("may be protected — verify") and must be reviewed by
  you (and, where appropriate, a licensed attorney) before you act on them.
- It is **not medical advice**. It never collects diagnoses and does not
  interpret clinical information; the unbundling check is a heuristic over
  billing codes only.
- It does **not send letters, make phone calls, contact providers or
  insurers, or negotiate on your behalf**. All letters are templates you
  review before sending.
- It does **not guarantee** that any error will be found or that any bill
  will be reduced.

## Fee terms (exact)

- **25% of the bill reduction you confirm.** If you tell us your bill was
  reduced by $1,000, the fee is $250.
- **Charged only after you confirm a reduction.** Reporting an outcome does
  not charge you; a separate confirmation step charges the saved card
  off-session via Stripe.
- **No charge otherwise.** If no reduction is confirmed, you pay nothing.
- Fees are in USD. The exact fee is computed and shown to you before any
  charge.

## Cancellation and refunds

- You may **cancel at any time before a charge is made** — simply do not
  confirm a reduction, or ask support to delete your case and payment method.
- Because the fee is only charged after you personally confirm a reduction,
  refunds apply only to processing errors (e.g. a duplicate charge). Contact
  support within 30 days and erroneous charges will be reversed.

## Your data (medical-data handling)

- We collect the minimum needed: your name/email, provider name, bill date,
  line items, and EOB figures. **We do not collect diagnoses, procedure
  notes, or insurance member IDs.**
- Data is stored in a local SQLite database (`data/app.db`). No data is sold
  or shared with third parties except Stripe (for payment processing) as
  described in the fee terms.
- Ask support any time to export or delete your data.

## Acceptable use

Provide only bills that are yours (or that you are authorized to handle).
Do not submit someone else's medical information without their consent.

## Governing law

_These terms are governed by the laws of Ontario, Canada, without regard to its
conflict-of-law principles._

## Changes

Material changes to these terms (especially fee terms) will be disclosed in
the fee-disclosure message before any future charge.
