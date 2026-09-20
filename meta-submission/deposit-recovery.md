# Deposit Recovery — submission draft

Resolve the open items in `STATUS.md` before submitting at https://muse.ai/platform. Descriptions follow the supplied API contract; the customer workflow still needs authenticated testing.

## Step 1 — Overview
**Example prompts** (one per line):
- Help me prepare a demand letter for my unreturned rental deposit.
- Show me the deposit-return information for my state.
- I received my deposit back. Help me review the recovery fee.

**Connector icon:** `assets/deposit-recovery-icon-512.png`

**Payments:** Intended to accept payments. Verify the complete test-mode flow before making the production payments claim.

**Your name:** Muhammad Wasiq Zia

**Work email:** Required — owner must supply a working address.

**Support email or URL:** Required — owner must supply a working support channel.

**Privacy policy URL:** https://qull.io/connect/deposit-recovery/privacy

**Terms of service URL:** https://qull.io/connect/deposit-recovery/terms

Both legal pages are drafts with a support-email placeholder. The URLs are live; their content is not final.

**Anything else — proposed copy after workflow verification:**

Deposit Recovery helps US renters prepare a demand letter for an unreturned security deposit using their case details and state-specific information. The renter reviews and sends the letter. The stated fee is 25% of the recovered amount, payable after the renter confirms recovery and authorizes payment. No recovery means no recovery fee.

## Step 2 — Technical specs
**Connection type:** Raw API

**API URL (application endpoint prefix; verify authenticated routing):**
```
https://5.78.152.6.nip.io/deposit-recovery/api
```

**OpenAPI specification URL:**
```
https://qull.io/connect/deposit-recovery/api-docs/deposit-recovery.json
```

**API documentation URL:**
```
https://qull.io/connect/deposit-recovery/api-docs/
```

The hosted spec still has the original duplicate `/api` prefix and omits authentication. Publish the corrected, authenticated contract at this URL before submission. The OpenAPI server root is `https://5.78.152.6.nip.io/deposit-recovery`; operation paths supply `/api` themselves.

**Access requirements — incomplete until implementation is verified:**

US rental security-deposit cases. An authenticated user identity is required for case access. The user reviews and sends the prepared demand letter. The stated recovery fee is 25%, due only after recovery is confirmed. Credential provisioning, supported jurisdictions, payment authorization, account requirements and enforced usage limits must be confirmed before this field is final.

**Authentication methods:** Unresolved. The original kit says API keys, but gives no header name, key-issuance process or end-user identity protocol. A 401 response does not verify whether API keys or OAuth are implemented.

## Reviewer handoff still needed
- Exact authentication and end-user identity instructions, plus privately shared test credentials.
- Tested intake → letter generation → user review/send decision → recovery confirmation → fee authorization/payment.
- Test-mode evidence for duplicate-payment prevention, declined cards and required customer authentication.
- Final contact details, privacy policy and fee terms.

The original 120 requests/minute, 20 payment requests/minute and 1 MB body limit are unverified configuration claims. Confirm enforcement before including them in the form.
