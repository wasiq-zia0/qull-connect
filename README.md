# Qull Connect

Ten services that prepare documents, calculations, guides, and checklists for
consumer claims and household administration. This repository contains the
backend applications, deployment tools, generated API contracts, public-page
content, and Muse submission worksheets.

The services are assisted workflows: users supply and review their information,
contact the relevant organization themselves, and confirm outcomes. They do
not independently recover funds, cancel subscriptions, negotiate bills, file
claims, or change account settings.

## Start here

- [Connector catalog](connectors.md): exact deliverables, fees, and limitations.
- [Review and release status](STATUS.md): implemented work versus external gates.
- [Integration guide](docs/INTEGRATION.md): authentication, payments, error handling.
- [Domain review](docs/DOMAIN_REVIEW.md): source-supported scope and unresolved rules.
- [Deployment guide](backend/deploy/DEPLOY.md) and [environment reference](backend/deploy/ENV.md).
- [Submission worksheets](meta-submission/): one for each connector.
- [Review runbook](docs/REVIEW_RUNBOOK.md): reproducible evidence to collect.
- [Commercial launch work](docs/COMMERCIAL_LAUNCH.md): what is needed to accept paying users.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/<slug>/` | FastAPI service, authenticated MCP adapter, domain logic and data |
| `backend/deploy/` | Deployment, identity provisioning, security and configuration documentation |
| `openapi/<slug>.json` | Application-generated REST contract |
| `docs/connector-content.json` | Shared product descriptions, deliverables, pricing and boundaries |
| `docs/build_documentation.py` | Generates all connector landing, API-guide and legal pages plus service READMEs/submission worksheets |
| `meta-submission/` | Form field values and evidence requirements; not a claim of approval |
| `prompts/` | Historical briefs retained for context; superseded by the implementation and review documentation |

## Public addressing

API origin: `https://5.78.152.6.nip.io/<slug>`.
Business operations use paths such as `/api/cases`; the OpenAPI server origin
does **not** end in `/api`. The public process check is `/<slug>/health`.
A process responding is not evidence that a customer workflow or charge worked.

Public documentation: [qull.io/connect](https://qull.io/connect/).
The reviewed source and production must be checked against the same commit
before reviewers are told that a particular behavior is deployed.

## Build the documentation

After generating the OpenAPI contracts from the reviewed applications, copy each
spec to the matching connector's `api-docs/<slug>.json` on the site, then run:

```bash
python docs/build_documentation.py --site /path/to/qull_site
```

The generator writes only the connector pages/assets in that site and the
service documentation/worksheets in this repository. It does not deploy or
modify OpenAPI JSON. Public-page source is maintained with the Qull website
repository; deploy only the scoped connector changes to avoid unrelated site
changes.

## Testing and payments

With Python 3.12, create an isolated environment and run the same application
checks used by CI:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python tools/install_review_environment.py
python -m pytest tests backend/tests -q
python tools/export_openapi.py
git diff --exit-code -- openapi 'backend/*/connector/manifest.json'
```

The exporter validates all ten OpenAPI documents, response examples and runtime
MCP tool schemas. Each service's `response_contracts.json` documents observed
response fields and synthetic examples; examples are not live account records
or usable payment links. CI also builds and starts all ten Docker images.

Use temporary databases, synthetic records, independently scoped user keys,
and Stripe **test mode**. Do not test by charging real cards or by weakening
production identity. Live payments require separately authorized customer
transactions and the operational/legal prerequisites in the launch checklist.
No health endpoint, README, or mock test establishes Meta approval or revenue.

Published Qull contact: **wasiq@qull.io**. The prior `support@qull.io` and
Ontario governing-law entries were explicitly placeholders and are not treated
as established operating facts. Legal pages remain drafts until reviewed.
