# Deploying the 10 Qull connectors — production runbook

Everything in `~/workspace/connectors/deploy/`:

| File | What it is |
|---|---|
| `vps-setup.sh` | One-time VPS provisioning (Ubuntu 24.04): Python 3.12, nginx, certbot, `connectors` service user, `/srv/connectors` layout |
| `deploy.sh` | Full deploy/redeploy: rsyncs code, builds venvs, writes systemd units + env files, writes the `api.qull.io` nginx vhost, runs certbot |
| `ENV.md` | Every env var each connector reads; dev-only flags that must NOT be set in prod; the Stripe production-billing blocker |
| `DEPLOY.md` | This file |

Plus, inside each connector directory: `Dockerfile` + `requirements.txt`
(frozen from its own verified `.venv`).

## What Wasiq needs to do (2 steps, ~15 minutes)

**Step 1 — Create the VPS and point DNS at it.**
- Hetzner Cloud → new server: **CX22** (2 vCPU / 4 GB RAM — plenty for 10 small
  Python services), **Ubuntu 24.04**, pick the region closest to you
  (Toronto → `ash` Ashburn or `hil` Hillsboro).
- Cost: roughly **€4–5/month** (~CAD 6–7).
- In your DNS (wherever `qull.io` is managed): add an **A record**
  `api.qull.io → <the VPS IP>`.
- Wait a few minutes for DNS to propagate.

**Step 2 — Give the agent SSH access.**
- In Hetzner, add the agent's SSH public key to the server (or paste a
  root password over a secure channel — key is better).
- Tell the agent the VPS IP / hostname.

**Then the agent runs** (no action needed from you):
```bash
./vps-setup.sh          # on the VPS, once — installs everything
VPS_HOST=root@<ip> ./deploy.sh   # from the agent machine — deploys all 10
```
After that, the public surface is live:
- REST: `https://api.qull.io/<slug>/api/...` (health: `https://api.qull.io/<slug>/health`)
- MCP: `https://api.qull.io/<slug>/mcp` ← this is the URL Muse / Meta connects to

## What is still NOT done after deploy (blockers before real money)

1. **Production billing is not wired.** Billing code calls the agent's Stripe
   skill CLI, which doesn't exist on the VPS. Real charges must stay OFF until
   a `STRIPE_SECRET_KEY`-based path is built (see `ENV.md`). The deploy
   scripts deliberately leave billing inert.
2. **Legal review of the fee models** (25–35% of recovery/savings) — especially
   deposit, final-paycheck, medical-bill, class-action, unclaimed-property.
3. **Meta submission** of deposit-recovery (first), then the rest.
4. **Witnessed live Stripe charge + refund test.**
5. **Confirm the exposed `rk_live_…` Stripe key was rotated.**

Deploy ≠ launch. Deploy makes the product reachable; the five items above
make it legal and payable.

## Railway / Fly alternative (instead of a VPS)

If you'd rather not manage a server: each connector dir has a working
`Dockerfile` (Python 3.12-slim, both ports, `/health` check on 9/10).
Per connector: **one service** from its Dockerfile, set a persistent
**volume mounted at `/app/data`** (sqlite lives there — without a volume,
cases vanish on every redeploy), expose the MCP port publicly. Roughly
$5–10/month per connector on either platform, so ~10× the VPS cost —
the VPS is the economical choice.

## Architecture notes (for the agent / future deploys)

- Systemd runs `run.py` **bound to 127.0.0.1** exactly as verified locally;
  nginx terminates TLS and reverse-proxies. `run.py` needed no changes.
- Only `deposit-recovery` (`DEPOSIT_HOST/DEPOSIT_REST_PORT/DEPOSIT_MCP_PORT`)
  and `final-paycheck` (`REST_PORT`/`MCP_PORT`) honor env overrides in
  `run.py` — the other eight hardcode `127.0.0.1` and their ports. The
  Dockerfiles work around this with a generated `/app/start.py` entrypoint
  (uvicorn on `0.0.0.0` + `mcp_server.server.run(host='0.0.0.0', …)`),
  so **no connector source was modified** for containerization.
- MCP streamable-HTTP goes through nginx with `proxy_buffering off` and
  1-hour timeouts (streaming responses).
- sqlite files are created on first boot (`CREATE TABLE IF NOT EXISTS`);
  `deploy.sh` excludes `data/` and `*.db` from rsync, so redeploys never
  touch production data.
- Env files at `/etc/connectors/<slug>.env` are created once with
  placeholders and never overwritten by redeploys.

## Validation checklist

What was verified while building this package (2026-09-19):

- [x] `requirements.txt` frozen from each connector's own `.venv` (all 10
      venvs present; 30–36 pinned packages each)
- [x] No `file://`, editable, or VCS refs in any `requirements.txt`
- [x] Every PDF import used in source (`fpdf`, `reportlab`) resolves to an
      installed package in that connector's venv
- [x] All 10 Dockerfiles: `FROM python:3.12-slim`, per-connector `REST_PORT` /
      `MCP_PORT` ENV, matching `EXPOSE`, `COPY --exclude` skips `.venv` /
      `__pycache__` / `data` / `*.db`
- [x] Embedded `start.py` in every Dockerfile compiles (`py_compile`)
- [x] HEALTHCHECK present on the 9 connectors with `/health`; omitted for
      deposit-recovery (it has no `/health` route)
- [x] `vps-setup.sh` + `deploy.sh` pass `bash -n`; nginx location-block
      generation tested (variable expansion verified)
- [x] Env inventory grepped from source (not guessed); dev-only flags
      documented as must-be-absent in prod
- [ ] **Dockerfiles not build-tested** — `docker` is not installed in this
      environment. Build at least 2 images on the VPS or locally before
      relying on the Railway/Fly path.
- [ ] **Scripts not run end-to-end** — no VPS exists yet. First `deploy.sh`
      run should be watched; expect minor environment-specific fixes.
- [ ] **certbot email** defaults to `admin@qull.io` — confirm it's a real
      monitored inbox (override with `CERT_EMAIL=`).
- [ ] **nginx streamable-HTTP behavior** (long-lived MCP sessions through
      the proxy) verified only by config review, not a live session.
