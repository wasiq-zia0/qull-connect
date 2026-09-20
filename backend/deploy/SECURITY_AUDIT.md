# Security review — reviewed source, 2026-09-20

This report records concrete changes and their validation. It is not a penetration-test certificate or an assertion about which revision is running at the public hostname.

| Finding | Source change | Verification / remaining work |
|---|---|---|
| Arbitrary platform-user header selected a user's identity | All ten identity modules now require a random per-user bearer key resolved through a hash-only registry. Platform/dev headers do not authenticate production requests. | Spoofed platform headers, unknown keys, expired/revoked keys, duplicate registry entries, and unsafe registry modes rejected in automated tests. |
| Shared service key created ownerless life-event records | No service-key bypass remains in identity middleware. Life-event requests use the same user credential as other requests. | Workflow tests must verify every event record is created under that owner and no NULL-owner adoption remains. |
| Production could enable development identity | Development header requires explicit `ENV=test/development` plus opt-in; unsafe production flags fail authentication and readiness. | Automated production/dev flag tests. |
| Request size checked only `Content-Length` | ASGI middleware counts actual chunks before invoking application code. | Chunked and understated oversized bodies rejected before a test downstream mutation; boundary-size body replay verified. |
| Rate limiter cleared all state at capacity | Bounded buckets expire idle clients and refuse new clients under saturation; changing a claimed bearer key cannot evade a client's IP budget. | Rate test verifies fake bearer rotation still reaches `429` with `Retry-After`. Counters remain per-process, not distributed. |
| API responses could be cached | Authenticated and authentication-error responses carry `Cache-Control: no-store`; authenticated responses add `nosniff`. | Middleware tests inspect authentication responses. |
| Multiple supervisors left a partially running service | Every supervisor terminates the sibling when either REST or MCP exits, handles shutdown, and uses the running Python interpreter. | Python compilation and process-supervision test; production systemd still needs acceptance. |
| Containers omitted all reference data and ran as root | Dockerfiles retain JSON/CSV assets, include shared packages, run UID 10001, separate `/var/lib/qull` from source data. | Static build-context tests; actual Docker builds not run here. |
| Deployment assumed old source paths, failed to restart existing units, and overwrote TLS config | Scripts derive paths from this repository, stage releases, gate activation, snapshot/migrate data, restart units, preserve TLS, and validate nginx before reload. | Shell syntax and renderer tests; no production execution. |
| Every service could write shared source files | Separate service users, root-owned source, private durable directories, systemd filesystem protections. | Configuration review; verify permissions on the deployed host. |

Automated security tests live in `tests/test_security_identity.py` and `tests/test_security_deploy.py`. They run against temporary directories and fake test credentials. They never make a Stripe charge or contact production APIs.

## Boundaries still requiring acceptance

- The public service must be deployed from this reviewed revision; a prior health response cannot prove new authentication or payment code is present.
- Muse's accepted credential exchange must be tested. The implementation intentionally makes no undocumented platform-header, OAuth, or per-user identity assumption.
- Stripe permissions, test-mode end-to-end setup, declines/SCA, idempotent retries, and restart recovery need the real Stripe test account acceptance run.
- Every connector's legal/financial data and fee eligibility must be reviewed for supported jurisdictions. A disclaimer does not validate a fee model or a statutory calculation.
- Configure monitored support, incident response, deletion/retention procedures, encrypted off-host backups, and tested restore/reconciliation. Local files and an API-key registry are not a customer onboarding or support operation.
- Keep dependencies patched and repeat tests on the deploy image. No independent penetration test or production load test was performed.

Public generic Stripe return/authentication pages are deliberate narrow exceptions to bearer auth. The session exchange must validate its short-lived single-use capability against the payment ledger; it must never accept user-selected owner, customer, fee, or payment identifiers as authority.
