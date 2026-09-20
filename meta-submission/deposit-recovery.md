# Meta Muse directory submission — Deposit Recovery (connector #1)

Fill values for the submission form at https://muse.ai/platform.

## Step 1 — Overview

**Example prompts** (one per line):
- My landlord never returned my $1,800 security deposit. Can you get it back?
- I moved out of my Texas apartment 45 days ago and still don't have my deposit. What are my options?
- Track my security deposit deadline and tell me the day my landlord is overdue.

**Connector icon:** 512×512 PNG (in this repo: `assets/deposit-recovery-icon-512.png`)

**Payments:** select "My connector accepts payments"

**Your name:** Muhammad Wasiq Zia

**Work email:** TODO

**Support email or URL:** TODO

**Privacy policy URL:** TODO — pending qull.io hosting (prompt in `prompts/codex-privacy-terms-prompt.txt`)

**Terms of service URL:** TODO — pending qull.io hosting

**Anything else:**
Deposit Recovery watches your move, tracks your state's legal return deadline, and sends a formal demand letter the day your landlord is overdue. If the deposit comes back, a 25% fee is charged automatically. If it doesn't, you pay nothing. Built for all 50 states plus DC.

## Step 2 — Technical specs

**Connection type:** Raw API

**API URL:**
```
https://5.78.152.6.nip.io/deposit-recovery/api
```

**OpenAPI specification:** `<qull.io-hosted spec URL>` — pending (prompt in `prompts/codex-api-docs-prompt.txt`)

**API documentation:** `<qull.io-hosted docs URL>` — pending

**Access requirements:**
```
No account or subscription needed. US only — all 50 states + DC. A payment method is saved when a case starts but is charged only after the user confirms their deposit was recovered. Rate limits: 120 requests/min per client (20/min on payment endpoints); 1 MB max request size.
```

**Authentication methods:** API keys

## Notes for reviewers
- Interactive API docs are hosted on qull.io (see documentation URL above); the API itself requires an API key.
- Open question we will answer if asked: how Muse authenticates on behalf of each end user (per-user identity).
