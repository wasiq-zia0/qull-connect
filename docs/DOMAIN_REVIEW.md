# Domain and source review

Reviewed 20 September 2026. This is an engineering review of the supplied data, calculations, and service claims. It records the defects found in the original backend and the evidence needed to resolve them. Implementation and release status belong in the test results and release checklist; the presence of this report does not mean that a production deployment or commercial legal review has passed.

## What the ten services actually deliver

| Service | Work present in the supplied backend | Claims that require additional implementation or evidence |
| --- | --- | --- |
| Deposit Recovery | Case records, state-rule lookup, date calculations, letter generation, payment hooks | Automatically sending a letter; monitoring a mailbox or bank account; legally reliable deadlines for every state |
| FlightPay | Route lookup, preliminary EU261 calculation, claim-pack generation, payout reporting | Filing with an airline; checking flight operational data; proving every eligibility condition |
| Subscription Slayer | Recurrence detection from receipts, merchant cancellation guides, user-reported completion | Cancelling a merchant account; verifying merchant completion; production per-user Gmail access |
| BillCut | Negotiation scripts and fee calculation from reported old/new charges and duration | Calling or negotiating with a provider; obtaining a particular retention offer; independently verifying savings |
| Final Paycheck Recovery | Case records, prototype deadline rules, letter preparation, payout reporting | Sending a demand; filing a labor complaint; complete nationwide legal deadline coverage |
| Class Action Cash | Local settlement catalog, receipt matching, claim instructions, payout reporting | Filing a claim; proving class membership from a keyword; automatic catalog refresh; production per-user Gmail access |
| Found Money | State portal directory, personalized filing checklist, user-reported claim status | Searching state databases; discovering a property balance; filing claims; lawful fee collection in every state |
| Moving Concierge | Personalized checklist, official-service links, PDF, checklist tracking | Booking movers; completing address changes; updating government records; verified deadlines for every move type |
| MatchMax | Educational projection for a simple employer-match formula | Changing payroll elections; monitoring a plan; modeling all plan terms, catch-ups, and year-to-date contributions |
| Medical Bill Fighter | Checks on entered bill/EOB data, questions for the provider, letter/script preparation | Adjudicating coding errors; negotiating with a provider; determining all insurance or legal protections |

These limits must be consistent in the directory form, landing page, generated letters, API descriptions, MCP descriptions, trigger messages, terms, and payment consent. An internal function named `cancel`, `scan`, or `recover` is not evidence that an external action occurred.

## Deposit Recovery: deadline engine

Files reviewed: `backend/deposit-recovery/data/state_laws.json`, `src/laws.py`, `src/engine.py`, and `src/letters.py`.

The original data explicitly calls itself a prototype for demonstration and cites secondary summaries. A single number of days cannot express all the exceptions already written in its own notes. Examples include Alaska notice/damage distinctions, Florida no-claim versus claim notices, Colorado lease terms, and Arizona demand and holiday requirements. The engine nevertheless presented a legal deadline, overdue status, potential recovery, and fee estimate.

Verified defects and bounded corrections:

- **Connecticut:** calculate the later of termination plus 21 days and receipt of the written forwarding address plus 15 days. The original `max(move_out, forwarding_date) + 21` is wrong. Input semantics must distinguish giving an address from the landlord receiving written notice. [Connecticut §47a-21(d)](https://www.cga.ct.gov/current/pub/chap_831.htm#sec_47a-21)
- **Arizona:** 14 days excludes Saturdays, Sundays, and legal holidays and follows termination, possession delivery, and the tenant's demand. `move_out + 14` calendar days can create a premature violation notice. Until those inputs and a valid holiday calendar are modeled, return `needs_review` rather than an enforceable due date. [Arizona §33-1321(D)](https://www.azleg.gov/ars/33/01321.htm)
- **California:** the ordinary residential return/accounting period is 21 calendar days after vacating. The output must distinguish an accounting deadline from an unconditional right to the full deposit. Good-faith estimates for unfinished repairs and other provisions remain relevant; a date calculation does not prove bad faith or an entitlement to multiplied damages. [California Civil Code §1950.5](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CIV&sectionNum=1950.5.)
- **Other states:** a populated row is not a reviewed implementation. Preserve the source reference and factual letter preparation, but do not manufacture a legal conclusion from an unverified simplified row. Expand enabled calculations only with exact primary citations, necessary intake fields, date-counting tests, and defined exclusions.

Regression cases should include forwarding received before and after move-out, a weekend/holiday crossing, missing forwarding/demand information, and a case with lawful deductions. Fee estimates must use an actual confirmed recovery rather than a theoretical maximum damages multiplier.

## Final Paycheck Recovery: timing and incomplete facts

Files reviewed: `backend/final-paycheck/data/final_pay_laws.json`, `deadlines.py`, and `letters.py`.

The original data identifies itself as prototype-grade. `working_days` excludes weekends but not legal holidays. An `earlier_of` or `later_of` calculation can silently ignore an unavailable payday branch and report a date from the remaining branch. Missing inputs must remain missing.

| Finding in original code/data | Source-backed correction |
| --- | --- |
| Nevada discharge encoded as three calendar days | Earned unpaid wages become due immediately. The three-day period belongs to a penalty provision; it is not the payment deadline. [NRS 608.020–608.040](https://www.leg.state.nv.us/NRS/NRS-608.html) |
| Delaware uses three calendar days | The rule is the later of the regular pay-cycle date and three **business** days after the last day. An unavailable payday or holiday treatment cannot be silently omitted. [19 Del. C. §1103](https://delcode.delaware.gov/title19/c011/index.html) |
| Minnesota discharge substitutes the last day worked for a demand | The statutory demand matters; the original proxy can falsely declare default. Public-employer conditions also differ. [Minnesota §181.13](https://www.revisor.mn.gov/statutes/cite/181.13) |
| Minnesota quit uses only the first payday, capped at 20 days | If that payday is fewer than five calendar days after separation, payment may be deferred to the second payday, subject to the 20-day maximum. The intake does not contain this second date. [Minnesota §181.14](https://www.revisor.mn.gov/statutes/cite/181.14) |
| South Carolina forces the earlier of 48 hours and payday | The statute permits payment within 48 hours **or** the next regular payday, no later than 30 days. Do not translate that into a mandatory earlier-of demand. [South Carolina §41-10-50](https://www.scstatehouse.gov/code/t41c010.php) |

For an unverified jurisdiction or incomplete fact pattern, a useful deliverable is a factual request for payment, an evidence checklist, and a primary-source link. It must not assert a statutory violation, automatic penalty, or fully verified legal deadline. Coverage should be expanded by reviewed scenario rather than by counting state rows.

## FlightPay: preliminary assessment only

Files reviewed: `backend/eu261-flight-comp/src/eligibility.py`, `src/pack.py`, and `data/airports.csv`.

The original engine applied the €600 distance tier to intra-Community flights over 3,500 km. Article 7 puts intra-Community flights over 1,500 km in the €400 tier. Its rerouting-reduction comparisons were also strict `<` comparisons although Article 7 uses inclusive boundaries. Cancellation notice exceptions have different comparisons and must not be changed indiscriminately. [Regulation 261/2004, Articles 5 and 7](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32004R0261)

An airline's assertion of extraordinary circumstances is not proof of the defense. The rules require evidence and reasonable measures. Missing cancellation notice, disputed circumstances, unknown airports, or an unmodeled itinerary should produce an incomplete/manual-review assessment. Denied boarding has exclusions for valid health, safety, security, and document grounds. Other scope conditions, including check-in, reservations, carrier identity, third-country compensation, and itinerary structure, are not proved by selecting airport codes.

The service can prepare a customer-reviewed claim pack. It has no airline submission integration in the supplied source. A projected compensation amount is not an award, a guaranteed recovery, or an amount that should immediately be billed.

## Class Action Cash: catalog provenance and fixture isolation

Files reviewed: `backend/class-action-cash/data/open_settlements.json`, `core.py`, `matcher.py`, and `data/fixtures/gmail_receipts.json`.

Two original catalog entries have current primary confirmation:

| Settlement | Confirmed on 20 September 2026 | Correction needed |
| --- | --- | --- |
| Kirkbride v. Kroger, 2:21-cv-00022 | Official administrator site confirms a 21 December 2026 claim deadline. [Administrator](https://www.krogersavingsclubsettlement.com/) | The catalog's claim that proof is required **only** at $8,000 or more is false. Unknown claimants below that threshold can also be asked for proof. Payout is variable and approval-dependent; remove an unsupported fixed payment timetable. [Court-authorized notice](https://angeion-public.s3.amazonaws.com/www.krogersavingsclubsettlement.com/docs/Kirkbride%20v%20Kroger%20-%20Long-Form%20Notice_Final.pdf) |
| Starr v. VSL Pharmaceuticals, 8:19-cv-02173-LKG | Official site confirms 20 October 2026 and the purchase period 1 June 2016–19 June 2019. Final approval remains pending. [Administrator](https://www.vsl3lawsuit.com/) | Link the primary notice and require the user to review eligibility; a matching email is not a class-membership determination. [Court-authorized notice](https://angeion-public.s3.amazonaws.com/www.vsl3lawsuit.com/docs/Notice.pdf) |

The remaining eight original entries pointed to aggregator articles/list pages and had `official_claim_url_verified: false`. They must not be presented as official filing destinations or enabled for claim-pack generation merely because they have recent dates. This review does not establish that those eight settlements are false; their submitted records are insufficiently verified.

The original freshness calculation used the newest verification date across the list. One fresh row could make old entries appear fresh. Check each record independently, exclude expired filing deadlines, and use a documented maximum age. Store the official source, reviewed date, deadline, class period, eligibility caveats, and review status separately. Never infer a personal payout ceiling by extracting arbitrary dollar amounts from a narrative that may describe the entire fund.

Fixture receipts and host-local Gmail CLI results must never become billable customer evidence. Production requires receipt input tied to the authenticated owner or a properly isolated per-user mailbox integration. Receipt matching must return a possible match, followed by human eligibility confirmation and direct filing at the official administrator.

## Found Money: portal assistance and fee eligibility

Files reviewed: `backend/unclaimed-property/app.py`, `billing.py`, `data/build_states.py`, and `data/state_claims.json`.

The code does not query state databases. It builds links, checklists, and cover sheets. The original generator duplicates a generic document list across all states, includes stale/homepage/reporting links, and gives every row the same verification date. It cannot support claims of exact state-specific filing requirements or a completed property search. Each state needs a primary portal source and a real link/process review; generic document examples should be labeled examples and users should send sensitive identity documents directly to the official state portal.

The original fee code defines a 10% default and three state caps. That establishes a number, not the right to charge:

- New York's rule applies to paid assistance locating/retrieving property and requires a written signed, notarized agreement and specific disclosures, including the ability to claim directly without a fee. [New York Abandoned Property Law §1416](https://www.nysenate.gov/legislation/laws/ABP/1416)
- California requires an appropriate written signed agreement with property-specific disclosures, restricts its timing, limits the percentage, and prohibits collecting before claim approval and payment to the owner. [California Code of Civil Procedure §1582](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CCP&sectionNum=1582.)
- Florida has registration and professional-status requirements for specified claimant-representative activities. Whether Qull's precise service fits those activities requires a scoped business determination; the statute is not evidence that every free self-filing guide needs a license. [Florida §717.1400](https://www.leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0700-0799/0717/Sections/0717.1400.html)

Paid activation needs reviewed jurisdictions, the applicable agreement process, property and timing evidence, and retained customer consent. A missing/unknown recovery state cannot fall back to a payable default. Until these conditions exist, portal guidance can remain available while the paid route returns a clear unavailable status.

## MatchMax: current constants and model boundaries

Files reviewed: `backend/401k-match/src/calc.py` and `src/converse.py`.

The original code labels $23,500 as the 2026 elective-deferral limit. The correct base limit is **$24,500**. General age-50 catch-up is $8,000; ages 60–63 have a distinct $11,250 amount. [IRS 2026 announcement](https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500)

The 2026 compensation limit is $360,000 and the ordinary defined-contribution annual-additions limit is $72,000. The original engine uses uncapped salary, permits current contributions over the base deferral limit in its match calculation, and can imply larger possible employer matches than its supported plan model establishes. [IRS Notice 2025-67](https://www.irs.gov/pub/irs-drop/n-25-67.pdf)

Use a tax-year-specific model with explicit scope: constant annual eligible compensation, a single simple match formula, uniform contributions, and a documented base-deferral assumption. Reject or clearly route unsupported plan/catch-up scenarios. Never silently invent the employer's match when the user says “yes.” A minimum match-capture percentage is not a recommendation to reduce a user's existing savings rate.

The `true_up` flag originally changes no calculation. Year-to-date deferrals, prior employers, bonuses, pay already received, changing contribution rates, vesting, and plan-specific true-up terms are absent. Outputs must explain these boundaries and avoid claiming that this projection maximizes an actual year's contributions or changes payroll. Boundary tests should cover high compensation, contributions above limits, the smallest supported percentage increment, zero existing contribution, and already captured match.

## Medical Bill Fighter: questions, not billing adjudication

Files reviewed: `backend/medical-bill-fighter/src/detector.py`, `src/models.py`, `src/draft.py`, and `src/code_pairs.json`.

- A repeated code and amount may reflect separate encounters, modifiers, or units. The model does not establish these facts, so repeated rows warrant a question rather than a confirmed billing-error classification.
- A bill/EOB difference may reflect timing or different services. Replace a blanket instruction not to pay with a request to reconcile the same services and preserve payment/appeal deadlines.
- The example code-pair file expressly says it is illustrative. It is not a licensed/versioned authoritative coding database or a full NCCI implementation. Keep findings tentative and ask for provider coding review.
- The original claim that many providers offer a 10–20% prompt-payment discount lacks a supplied primary source. Ask whether assistance or a discount is available without inventing a typical saving.
- No Surprises Act flags must stay conditional. Coverage, facility type, consent, and exceptions matter; federal protections generally do not cover ground ambulances. [CMS patient guidance](https://www.cms.gov/medical-bill-rights/know-your-rights/using-insurance)

No provider contact or negotiation integration exists in the supplied code. The delivered work is a structured bill review and customer-used request/negotiation pack. Fees based on reported savings require an actual before/after balance and consent, not the detector's hypothetical savings.

## Moving Concierge: task pack accuracy

Files reviewed: `backend/moving-concierge/src/pack.py`, `src/states.py`, and `data/voter_links.json`.

USPS now lists a **$1.25** online identity-verification fee; the original pack says $1.10 and calls it the only official fee. Forwarding exclusions, paid extensions, and other services make that broader claim misleading. Link current USPS instructions and distinguish Qull's service price from any third-party charge. [USPS forwarding instructions](https://www.usps.com/manage/forward.htm)

The state table has no per-entry primary deadline citations and does not distinguish a new resident transferring a license from an existing resident changing an address. These are not interchangeable tasks. Retain official agency links and factual checklists; do not display an unverified legal countdown.

Kansas's entry pointed to a third-party registration site while the pack promised official links. Use the Secretary of State's official information page, which links the state registration service and describes when paper registration is necessary. Remove universal “two minutes online” promises. [Kansas official voter information](https://www.sos.ks.gov/elections/voter-information.html)

The service produces and tracks a checklist. It does not update records, submit forms, or book movers.

## Subscription Slayer and BillCut: delivery and savings claims

Subscription Slayer's `cancel_guides.json` contains merchant links and instructions, not cancellation APIs. Instructions vary by billing platform, geography, and plan. The user completes the cancellation and supplies confirmation. Fixtures must be isolated from production billing. A scan through one host-local Gmail CLI is not a per-customer integration.

BillCut's `data/provider_scripts.json` is a set of scripts, not a live provider-offer feed. Statements that supervisors routinely unlock better offers, that a specific price is common, or that loyalty customers get the best rate have no supplied evidence. Replace them with optional, truthful questions. A user should mention a competitor price only if they have actually found an applicable offer.

For both products, savings are an estimate until supported by a cancelled renewal or documented bill reduction. Annualized savings must disclose their assumptions, remaining term, cancellation costs, and whether the price includes fees/taxes. The charge must match the promised unit of service and the customer's recorded confirmation. No commercial projection should treat a fabricated receipt, a prepared script, or a suggested discount as earned revenue.

## Continuing source maintenance

For each enabled rule or external data item, keep an exact primary URL, review date, effective date when available, supported fact pattern, required inputs, and expiry/re-review policy. State-row counts and health checks are not data-quality metrics. A useful release gate is whether the API refuses unsupported conclusions and whether the documentation accurately describes the specific service that a customer receives.

Commercial legal review still needs Qull's actual service agreements, business jurisdictions, fee collection model, and any required registrations. Those facts cannot be established from source code or a Stripe account. They should remain explicit release dependencies instead of being replaced by a claim of nationwide compliance.

## Integration review of the corrected implementation

A second review inspected the changing implementation, rather than assuming that revised documentation proved a fix. The owners corrected these additional issues found during integration:

- The MatchMax model now optimizes the simple match formula against the combined employee/employer limit. A $300,000 salary, 200% match up to 10%, and 8% employee contribution produces $48,000 of employer match at the $72,000 combined limit. The previous revision incorrectly called $47,500 the maximum. A direct calculation check confirms the corrected boundary and ordinary 50%-up-to-6% examples.
- Subscription receipt imports and manual entries now share the known merchant/currency identity. This preserves merchant-specific cancellation instructions and avoids creating two independently chargeable records for one known subscription. A failed fee-quote comparison no longer reserves a cancellation batch.
- Class Action Cash now groups receipt evidence by owner and settlement and derives its payment identity from that settlement. A second receipt is evidence for the same possible claim, not another separately billable payout.
- Medical Bill Fighter reserves its stored outcome before attempting payment and conditionally rejects competing outcome edits. BillCut and FlightPay use conditional writes to keep the approved fee basis from changing while a charge is attempted. Final Paycheck restricts intake confirmation to unfinished drafts.

These are source/code checks, not proof of production payment completion, external filing, legal eligibility, or directory approval. The release test report and deployment record must identify the exact commit actually exercised.

The property directory still needs careful provenance. The original generated verification dates are not evidence that all 51 state processes were reviewed. A reliable fallback is the state-program directory linked by [USAGov's unclaimed-money guidance](https://www.usa.gov/unclaimed-money) at [NAUPA](https://unclaimed.org/). Label generic document lists as preparation examples, send identity documents directly to the state program, and reserve a process-verification date for an actual review of that state's instructions.
