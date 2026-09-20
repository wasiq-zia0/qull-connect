# API review notes — September 20, 2026

This supersedes the earlier readiness summary. The maintained contracts are
application-generated OpenAPI specifications in `openapi/`. See the per-service
READMEs and [integration guide](../../docs/INTEGRATION.md) for exact scope.

## Contract corrections

- Server origins stop at `/<slug>`; paths include `/api`. Previous combinations
  could produce an invalid `/api/api/...` URL.
- Protected operations describe owner-bound Bearer credentials rather than an
  unspecified API key or untrusted platform identity header.
- Public health, configured readiness, authenticated business workflows,
  provider settlement and Meta approval are distinct states.
- Hosted Stripe setup replaces an undocumented embedded card-collection step.
  Setup and fee confirmation must reflect explicit user decisions; the server
  verifies provider state and the expected fee.
- API/MCP behavior must use the same owner and validated business rules. Runtime
  MCP tool schemas and the REST contracts must be refreshed after model changes.
- User-owned deletion and actual receipt intake need to appear in the generated
  contract. Disabled Gmail/automatic sending/database-search behavior must not
  appear as an available production capability.

## Evidence required

1. Validate that every published server+path matches the routed deployed app.
2. Compare generated models with representative successful and failure responses.
3. Exercise create/read/update/document/delete as two distinct users.
4. Check PDF output, pagination/encoding, content safety and authenticated access.
5. Complete Stripe test-mode setup and payment branches with genuine provider
   results; mock tests verify logic but do not replace this.
6. Verify restart persistence, backups, payment idempotency and reconciliation.
7. Run the workflow in the actual Muse transport and credential model.

Use [REVIEW_RUNBOOK.md](../../docs/REVIEW_RUNBOOK.md) and record the build and
actual results. This document is a scope/evidence guide, not a passed audit,
security certification, live-billing verification, or approval from Meta.
