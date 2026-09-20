# Qull Connect — Meta Muse directory kit

Submission material and API contracts for 10 proposed Muse connectors. Backend source and deployment configuration are not in this repository. See [STATUS.md](STATUS.md) for verified evidence and outstanding work.

## Contents
- `connectors.md` — names, intended pricing and documented workflows.
- `openapi/` — API contracts; authentication still needs to be specified.
- `prompts/` — original page-building prompts, with corrected API server roots.
- `meta-submission/` — submission draft and published URL references.
- `assets/` — submission icon.
- `tools/check_contracts.py` — repeatable contract checks.
- `STATUS.md` — readiness findings.

## Live services
Application endpoint prefix: `https://5.78.152.6.nip.io/<slug>/api`.
Public health: `https://5.78.152.6.nip.io/<slug>/health`.

OpenAPI server roots end at `/<slug>` because operation paths already include `/api`. This branch corrects the former duplicate prefix. Authenticated routing still needs backend verification.

The connected Stripe account is Qull, Inc. in live mode. Connector payment flows have not been verified end to end in this audit. Use Stripe test mode for payment testing; this repository does not authorize live charges or refunds.

Run `python tools/check_contracts.py`. A nonzero result means contract issues remain; this is not a Meta certification tool.
