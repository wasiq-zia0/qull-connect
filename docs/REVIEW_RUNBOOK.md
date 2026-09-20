# Reviewer and release runbook

Use this document to collect evidence, not to manufacture a successful demo.
Record the commit, environment, date, test identity, input, expected behavior,
actual result and evidence reference for each check. Redact credentials and use
synthetic customer facts. Do not submit a test result from a different build as
proof about production.

## Environment record

| Item | Record before a run |
|---|---|
| Source | Git commit and generated OpenAPI version/hash |
| Target | Exact service URL and environment; local/test/production |
| Credentials | Non-secret IDs of two distinct owner keys; never the raw keys |
| Persistence | Database/ledger locations and backup/restore procedure |
| Payments | Stripe test mode and the non-secret account reference |
| Dataset | Source/version/date and any feature gates |
| Execution | Request/response evidence with sensitive fields removed |

## Shared service checks

1. Call `/health` and `/ready`; record what each actually proves.
2. Call a business endpoint with no credential, an invalid key, a revoked key
   and an expired key. None may access customer records.
3. Create a record as user A. With user B, attempt read, mutation, document
   download, payment setup, payment confirmation and deletion of A's record.
   The record must remain unavailable and unmodified.
4. Submit caller-controlled identity and forwarded-IP headers. They must not
   grant another user's identity or bypass the intended controls.
5. Check schema validation, actual body-size limits, and rate limits. Include
   malformed dates, non-finite/negative money, excessive arrays and unknown IDs.
6. Complete a documented customer workflow with accurate synthetic facts. Open
   the actual generated PDF or pack and verify names, dates, amounts, line wraps,
   character encoding, source caveats and links.
7. Stop/restart the service. Confirm the intended records, ownership, consent
   and payment ledger survive and the restarted service uses the same data path.
8. Delete user A's operational data using its documented authenticated operation.
   Confirm A's artifacts are unavailable and user B's data remains intact.
9. Review application/proxy logs for accidental credential, card or unnecessary
   customer-data disclosure. Check that stored artifacts are not served publicly.

## Payment checks against Stripe test mode

A mock is useful for deterministic business logic; it is not proof of a real
provider integration. Perform the following against Stripe's test environment.

- Show the exact scope/fee disclosure and obtain setup acceptance. Open the
  genuine returned hosted setup URL and complete it with a test payment method.
- Cancel/abandon setup and confirm that no charge or paid state occurs.
- Confirm the server rejects an incomplete SetupIntent, a different customer's
  payment method and a caller-supplied unverified setup status.
- Verify missing consent and an incorrect expected fee are rejected before a
  charge. For outcome fees, use actual test outcome data rather than an estimate.
- Confirm a successful PaymentIntent settles the exact amount/currency and
  unlocks only the intended record. Inspect Stripe's test record as evidence.
- Exercise decline and additional authentication. An authentication requirement
  must return a usable authorization path and must not mark the record paid.
- Retry the same charge and simulate simultaneous requests. Both must refer to
  the same business event/PaymentIntent rather than create duplicate fees.
- Simulate a network error after provider creation; reconcile status and repeat
  the same operation. Test restart between provider success and local recording.
- Test an operator refund procedure in Stripe test mode and the support record
  needed to explain it. A public refund endpoint is not implemented.
- Do not invoke live payment or real-card flows without explicit, separate
  authorization for that specific transaction. A request to review code is not
  that authorization.

## Connector-specific walkthroughs

| Connector | Minimum genuine demonstration |
|---|---|
| Deposit Recovery | Supported reviewed-state estimate; unsupported state requires review; neutral vs supported letter; user-sent status; actual recovery amount and exact 25% fee. |
| FlightPay | Supported route assessment; incomplete/ambiguous scenario does not become guaranteed eligibility; correct PDF; actual payout fee in EUR. |
| Subscription Slayer | Owner-supplied receipts; recurring-charge detection checked against inputs; cancellation guide; user-completed cancellations; exactly $10 per distinct confirmed item. |
| BillCut | Provider/generic script; outcome calculation; 12-month savings cap; no-success produces no fee; 35% exact amount confirmation. |
| Final Paycheck | Separation inputs; unknown/incomplete deadline case; safe factual letter; actual recovered wages and exact 25% fee. |
| Class Action Cash | Current verified settlement and dated receipt; stale/closed entries excluded; correct official claim link; user files; actual payout and 20% fee. |
| Found Money | Correct selected-state links/checklist; no claim of an actual database match; SSN rejection/no storage; fee endpoints blocked until agreement requirements are resolved. |
| Moving Concierge | Correct old/new addresses; useful checklist and PDF; unpaid pack locked; one $49 payment; restart/retry does not re-charge. |
| MatchMax | Independently computed simple formula and current annual limit; unsupported plan/midyear assumptions explained; one $99 payment; no automatic renewal. |
| Medical Bill Fighter | Explain each issue flag from entered figures; false-positive limitations; correct PDF; user-reported reduction; exactly 25% fee with authorization. |

## Muse submission package

Prepare a restricted reviewer identity, exact synthetic example prompts and
inputs, the form worksheet, current hosted docs/spec/legal URLs, and a genuine
walkthrough. Establish how that identity is supplied by the platform and test it
there. A working REST curl request does not show that Muse can authenticate each
end user or complete a hosted card setup.

Meta states that it reviews functional, security and legal requirements and
performs end-to-end testing on its [platform page](https://muse.ai/platform).
That is the documented high-level process; do not invent detailed unpublished
acceptance rules or promise that passing these local checks guarantees approval.

Keep a final list of known limitations, disabled functions and unresolved items.
Do not enable a legal or security-sensitive feature just to make a demo appear
complete. Demonstrate the actual service scope submitted for review.
