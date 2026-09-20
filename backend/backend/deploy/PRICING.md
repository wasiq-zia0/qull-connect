# Qull Connect — Pricing Audit (2026-09-19)

Founder directive: pricing must "make sense" — competitive, defensible, legally viable.
Researched against real competitors/market comparables. Nothing here is legal advice;
percentage-of-recovery models need counsel sign-off before Meta submission or launch.

**Tax handling (all connectors):** All prices are exclusive of applicable tax. The final
fee and any tax are shown to the user before authorization, and no charge occurs without
explicit user confirmation after verified recovery/savings.

| # | Connector | Current pricing | Real comparables | Recommendation | Legal flag |
|---|-----------|-----------------|------------------|----------------|------------|
| 1 | Deposit Recovery | 25% of recovered deposit | Employment/tenant attorneys: ~33–40% contingency (1/3 standard); small-claims filing $50–150; no direct %-fee competitor (novel category) | **KEEP 25%** — undercuts attorney contingency; novel category with no price anchor to beat | ⚠️ YES — demand-letter drafting + % fee is UPL-adjacent; needs counsel review before submission |
| 2 | FlightPay (EU261) | 30% of payout | AirHelp 35% (+15% more if legal action); Compensair 25–30% (+10% legal); Flightright ~30% | **KEEP 30%** — dead center of the market, below AirHelp headline rate | Low — standard claims-assignment paperwork |
| 3 | Subscription Slayer | 30% of first-year documented savings | BillShark: **$9 flat per cancelled subscription**; Rocket Money: cancellation bundled in Premium ($7–14/mo), no % fee | **CHANGE → flat $9–12 per cancellation** — 30% of first-year savings is 3–6x BillShark (cancel a $15/mo sub = $180/yr savings → $54 fee vs $9). % model overcharges cheap subs and is hard to explain | Low |
| 4 | BillCut | 35% of documented savings, capped at 12 months | Rocket Money 35–60% of first-year savings; BillShark 40% (capped at 24 months); BillCutterz 50% | **KEEP 35%** — at/below every major competitor; 12-month cap is more consumer-friendly than BillShark's 24-month cap | Low |
| 5 | Final Paycheck Recovery | 25% of recovered wages | Employment attorneys: ~33–40% contingency typical | **KEEP 25%** — undercuts attorneys; wage recovery is high-trust, contingency is the expected model | ⚠️ YES — wage-claim % fee; UPL/claims-management risk; counsel review required |
| 6 | Class Action Cash | 20% of settlement payout | Filing directly with administrators is **FREE** (openclassactions.com explicitly warns against paying third parties a cut); TheClassActionLawsuit.com free directory; MCAG charges contingent % but B2B only | **KEEP 20% ONLY with guardrails** — defensible solely as a proactive-matching fee ("money you'd never have claimed"), never as a filing fee. Marketing must never imply filing costs money | ⚠️ YES — consumer-protection/deceptive-practice risk; counsel must clear copy |
| 7 | Found Money (Unclaimed Property) | 15% of recovered funds | **CA Code Civ. Proc. §1582: 10% max**; IN IC 32-34-1-46: 10% max; NE §69-1317: 10% max; most states cap finder fees ~10%. Agreements also void if signed inside 12–24 month windows; must be written; must disclose free self-claim | **CHANGE → 10% max, with per-state rule engine enforcing lower caps** — 15% is unlawful in California and other states | 🔴 RED — hard statutory caps; current 15% blocks Meta submission |
| 8 | Moving Concierge | $49 flat per move | White-glove moving concierges $10k–200k (different market); Move Concierge (utility setup) is free to consumers, provider-paid | **KEEP $49** — impulse price, no direct competitor at this tier; upsell room later | Low |
| 9 | MatchMax (401k) | $99/year flat | Robo-advisors 0.25–0.50% AUM/yr; human advisors ~1% AUM; flat-fee planners $2,000–7,500/yr; one-time plan $1,000–3,000 | **KEEP $99/yr** — an order of magnitude below human advisors; priced as a no-brainer | ⚠️ MEDIUM — must remain calculations/education; avoid triggering fiduciary investment-advice obligations |
| 10 | Medical Bill Fighter | 25% of documented reduction | Medical Cost Advocate 35%; Resolve Medical Bills 25% ($5–15k bills), 10% on bills $15k+; independent patient advocates typically 25–30% | **KEEP 25%** — exactly market rate; consider a large-bill tier later (10–15% above $15k, like Resolve) so big bills don't look extractive | ⚠️ MEDIUM — health-data sensitivity (HIPAA-adjacent handling); contingency on medical debt needs counsel review |

## The 3 pricing changes that matter most (ranked)

1. **Found Money: 15% → 10% (legally required).** California, Indiana, Nebraska and
   most other states cap finder fees at 10%; 15% is unlawful there. This is a launch
   blocker, not a preference. Ship with a per-state cap engine and written-agreement
   flow that discloses free self-claim.
2. **Subscription Slayer: 30% of first-year savings → flat $9–12 per cancellation.**
   BillShark's $9 flat is the price anchor; a % model charges $54 to cancel a $15/mo
   subscription and reads as a bad deal. Flat fee is simpler, competitive, and easier
   to disclose pre-authorization.
3. **Medical Bill Fighter: add a large-bill tier later (10–15% above $15k).** 25% is
   market-correct for typical bills, but 25% of a $50k hospital bill ($12,500 fee)
   invites sticker shock and press risk; Resolve already tiers down to 10% on large
   bills. Not a launch blocker — phase 2.

## Pre-submission legal review queue (priority order)

1. Found Money — statutory fee caps + agreement timing/disclosure rules (blocking).
2. Deposit Recovery + Final Paycheck — contingency-fee/UPL exposure (blocking).
3. Class Action Cash — consumer-protection/marketing copy clearance.
4. Medical Bill Fighter — health-data handling + contingency-on-medical-debt.
5. MatchMax — confirm scope stays clear of fiduciary investment advice.
