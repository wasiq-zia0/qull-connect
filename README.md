# Qull Connect — Meta Muse directory kit

Everything needed to list Qull's 10 money-recovery connectors in Meta's Muse directory.

## Contents
- `connectors.md` — the 10 connectors: names, slugs, pricing, what each does.
- `openapi/` — OpenAPI 3.x specs for all 10 APIs (`<slug>.json`), with the live server URL baked in.
- `prompts/codex-privacy-terms-prompt.txt` — prompt for building the 20 privacy/terms pages on qull.io.
- `prompts/codex-api-docs-prompt.txt` — prompt for hosting the 10 API docs pages + specs on qull.io.
- `meta-submission/deposit-recovery.md` — paste-ready answers for the Meta submission form (connector #1).
- `assets/deposit-recovery-icon-512.png` — connector icon for the submission form.
- `STATUS.md` — where things stand.

## Live services
Base: `https://5.78.152.6.nip.io/<slug>/api` · Health: `https://5.78.152.6.nip.io/<slug>/health`
Stripe billing is live (Qull, Inc.). No live charges in testing — a witnessed charge-and-refund is the only exception.
