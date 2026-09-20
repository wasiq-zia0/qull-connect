# From implemented connectors to paying customers

Approval is a distribution gate. Revenue requires a buyer who understands the
service, receives a useful result, authorizes the disclosed fee, and can get help
when something goes wrong. This release should be sold as the document/guide/
calculation service it actually provides.

## Per-connector commercial position

| Connector | Actual paid value | Principal gap before a paid launch |
|---|---|---|
| Deposit Recovery | Organizing facts and preparing a deposit letter | Jurisdiction-specific deadline scope and percentage-fee/legal model review; do not price it as legal representation. |
| FlightPay | Supported eligibility screening and claim-pack preparation | Confirm the supported scenarios and claims-assistance agreement; the user still submits to the airline. |
| Subscription Slayer | Receipt organization and cancellation instructions | Explain why a user would pay $10 for guidance they execute themselves; actual delegated cancellation is not implemented. |
| BillCut | A provider script and savings tracker | Clarify that the user negotiates; outcome reporting is not independent bill verification. |
| Final Paycheck | Facts/deadline organization and letter preparation | Employment/jurisdiction coverage and wage-recovery percentage-fee review. |
| Class Action Cash | Current receipt matching and claim organization | Maintain a real verified catalog and make free official filing prominent; receipt matching is not eligibility. |
| Found Money | Free official-portal checklist in this release | Paid collection remains disabled until state-specific fee/agreement rules are implemented and reviewed. |
| Moving Concierge | A complete $49 address-change pack | Demonstrate the pack is useful enough to purchase and maintain official links; no mover/utility coordination. |
| MatchMax | A $99 simple-formula educational analysis pack | Validate model assumptions and explain limitations; no payroll integration or ongoing annual service. |
| Medical Bill Fighter | Potential-issue explanations and letter preparation | Health-data operating policy, limited rule accuracy and bill-reduction fee model review. |

## Finish the operational product

- **Onboarding:** Confirm who can use each service, supported facts/jurisdictions,
  the credential-provisioning path, and what the user will receive before charging.
- **Payment completion:** Finish real Stripe test-mode acceptance, publish final
  fee/refund terms, confirm any applicable tax handling, and ensure the customer
  can complete bank authentication and obtain a payment reference.
- **Delivery:** Make the promised pack retrievable by the correct user after
  payment, including after a restart or a lost conversation. Explain how to use it.
- **Support:** Verify the published Qull contact receives messages and define who
  handles failed delivery, billing mistakes, deletion and refund requests. Never
  advertise a response SLA that is not staffed.
- **Operations:** Deploy the reviewed commit, monitor availability/payment errors,
  preserve and restore the app DB and billing ledger together, and rehearse key
  revocation and incident response.
- **Policy:** Finalize privacy/retention and terms for the real implementation.
  Do not substitute a disclaimer for a missing statutory agreement or data control.
- **Distribution:** Submit a truthful listing with supported prompts and a real
  walkthrough. After approval, use the actual listing URL and remove preview
  language only for the connector that is available.

## Measure the whole conversion path

Track counts and monetary totals rather than collecting unnecessary customer
content. Use actual events, not an assistant's `user_message`, as the source.

| Metric | Definition |
|---|---|
| Qualified starts | Users with supported facts who knowingly start the workflow |
| Useful deliverables | Packs/calculations successfully generated and retrieved |
| Outcome reported | Actual recovery, reduction or cancellation reported by the user |
| Payment offered | Exact fee displayed to a user with a valid payable event |
| Payment authorized | User explicitly agrees to that amount/currency |
| Fee settled | Provider-confirmed succeeded payment, net of later refund |
| Failed payments | Decline, authentication-required, setup incomplete or provider failure |
| Support/refunds | Delivery/billing disputes and refunds, with a reason category |

For each cohort, calculate settled revenue minus processor fees, refunds,
hosting and support costs. An approval, a created Stripe customer, a saved card,
a generated pack, an estimated recovery and an authorized-but-unsettled payment
are different events; none alone is earned net revenue.

Do not set a sales or revenue forecast from the code review. The current prices
are product choices, not proof of demand. Test demand with truthful descriptions
and independently authorized customers after the required launch gates close.
