# Deploying the reviewed connectors

The repository contains application code, shared payment/API packages, reference data, and deployment tooling. These scripts are prepared for Ubuntu 24.04 with nginx and systemd. They have not been run against the production VPS during this review. Docker builds and production migrations still require an operator acceptance run.

## Release layout

Each connector runs as its own `qull-<slug>` system user. Application files live at `/srv/connectors/<slug>` and are owned by root. Durable databases and generated documents live at `/var/lib/qull/<slug>` with owner-only access. The application cannot change its own code or another connector's data. Secrets live in `/etc/connectors/<slug>.env`; nonsecret generated settings live in `<slug>.managed.env`. A read-only group-accessible user-key hash registry is shared by the ten services.

The supervisor uses the active Python interpreter, starts REST and MCP, and terminates both if either process exits. It handles SIGTERM and waits for children before exiting. Systemd restarts a failed pair. Services bind to loopback; only nginx is exposed publicly. Restrict the host firewall to your administrative SSH sources plus HTTPS and HTTP certificate validation. Never expose ports 8471–8480 or 8571–8580 directly.

## Prepare and configure

Review the code, tests, environment reference, and generated nginx before using it. Clone the reviewed commit on the server or use the SSH uploader. The uploader requires an already verified `known_hosts` entry and will not accept an unknown server key automatically.

On a new Ubuntu 24.04 server, run:

```bash
sudo bash backend/deploy/vps-setup.sh
sudo DOMAIN=api.qull.io MODE=prepare bash backend/deploy/server-deploy.sh
```

From a workstation, the equivalent uploader is:

```bash
VPS_HOST=your-verified-ssh-alias DOMAIN=api.qull.io MODE=prepare \
  bash backend/deploy/deploy.sh
```

`prepare` installs isolated staged releases and dependency environments. It does not replace running application code, overwrite an existing managed environment, change existing secret-file ownership, or start the new release. The new managed configuration is staged inside `.staged-<slug>/.managed.env` until activation. It retains packaged JSON/CSV reference data; the old scripts incorrectly excluded the entire `data/` directory.

Configure the Stripe test secret and publishable keys privately in each existing `/etc/connectors/<slug>.env`. No existing secret file is overwritten. Provision separate user/reviewer API keys following [ENV.md](ENV.md). The generated managed settings select production identity, durable paths, and each connector's public HTTPS base URL.

For an existing deployment, inspect `FINAL_PAYCHECK_DB` and any custom storage locations before activation. The migration recognizes the standard legacy `data/app.db`; custom paths must be backed up and deliberately migrated by the operator. Existing ownerless records are not automatically assigned to a new user. Resolve their ownership from trustworthy account records before making them accessible.

Point the hostname's DNS to the server and obtain its TLS certificate before activation. For an existing site, keep its current certificate. For a new hostname, create an HTTP nginx vhost using `render-nginx.py --domain HOSTNAME`, validate it with `nginx -t`, then issue a certificate with the installed certbot nginx plugin using your monitored administrative address. Review legacy vhost symlinks: do not leave two connector vhosts defining the same hostname or `qull_std`/`qull_money` zones enabled.

## Activate

```bash
sudo DOMAIN=api.qull.io MODE=activate bash backend/deploy/server-deploy.sh
```

Activation checks all ten staged configurations as their service users, checks legacy/durable database ambiguity and configured port/key modes, and validates both current and candidate nginx/TLS configuration before stopping a service. It snapshots the prior connector configuration and full nginx tree, then creates private timestamped code and data backups, migrates the standard legacy SQLite database using SQLite's backup API, preserves generated documents, installs reviewed code and systemd units, restarts one service at a time, and waits for its `/ready` before stopping the next service. A failed step exits with an error; the timestamped snapshot is retained for operator rollback. There is no claim of atomic all-ten deployment.

The nginx configuration routes `/<slug>/api/...` to the REST application's `/api/...` path and `/<slug>/mcp` to MCP. It also routes health, readiness, and payment return/authentication pages. It forwards the bearer credential, strips untrusted identity headers, sets an exact 1,000,000-byte body cap, and limits requests by client IP. TLS directives are included on every deployment so an existing certificate is not accidentally removed from the vhost. `nginx -t` must pass before reload; a failed generated vhost restores the previous saved configuration when available.

After activation, perform authenticated end-to-end acceptance tests using separate users and Stripe test mode:

1. Create a case, read it as its owner, and verify another user cannot read or mutate it.
2. Produce the actual letter, checklist, calculation, or other documented deliverable; verify its contents and limitations.
3. Complete hosted Stripe card setup and test the explicit fee-confirmation flow. Exercise declines, additional cardholder authentication, repeated requests, and a process restart without duplicate charging.
4. Verify the live schema, API documentation, privacy/terms pages, support address, directory copy, and pricing agree with that behavior.
5. Test the exact Muse credential handoff and request flow with reviewer credentials. A Qull API-key implementation does not by itself establish Muse compatibility.

Moving from test mode to live requires matching live secret/publishable keys, `STRIPE_MODE=live`, an approved fee/product policy, and completed operator acceptance. Do not use real customer charges as a software test. This repository does not authorize a live charge or refund.

## Backups and rollback

Activation snapshots are under `/var/backups/qull/<UTC timestamp>/`. Each connector has separate `-code.tar.gz` and `-data.tar.gz` archives; treat both as sensitive. Prior connector environment files, nginx configuration and enabled symlinks, and per-service systemd units are also saved. The [operator acceptance supplement](OPERATOR_ACCEPTANCE.md) gives the exact verification and rollback sequence. These are release snapshots, not a scheduled backup system. Establish encrypted off-host backups, retention, and restore drills before relying on the service commercially.

For rollback, stop the affected service, restore its code and data from the same snapshot into their respective parent directories, restore service-unit/configuration changes where needed, set the recorded service ownership, then restart and verify readiness. Preserve the failed release for investigation. Restoring an older payment ledger after a real payment can lose local payment history: reconcile Stripe and the ledger before allowing further charges. Never erase a database to fix startup.

## Containers

Build from the shared `backend/` context so payment and API packages are included:

```bash
docker build -f backend/deposit-recovery/Dockerfile -t qull/deposit-recovery backend
```

Each image runs as UID 10001, uses the same supervisor, includes packaged reference assets, and declares `/var/lib/qull` as its durable volume. Mount the hash registry read-only with owner/group permissions that UID 10001 can read (without world-readable access), and supply configuration through your orchestrator's secret mechanism. A volume mounted over `/app/data` would hide packaged laws/catalogs and is no longer the prescribed layout. Expose only the application port behind your authenticated HTTPS ingress; configure MCP routing separately. The image health check is liveness only; poll `/ready` and run acceptance tests separately.

The repository does not contain production SSH credentials. Actual deployment, TLS issuance, rollback, Docker builds, backup recovery, and a real Muse review have not been demonstrated by local syntax/unit checks.
