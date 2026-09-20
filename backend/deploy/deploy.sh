#!/usr/bin/env bash
# deploy.sh — deploy (or redeploy) all 10 Qull connectors to the VPS.
#
# Runs on YOUR machine (the agent's), orchestrates over SSH. Requires:
#   VPS_HOST  — ssh target, e.g.  root@203.0.113.10   (export it first)
#   VPS_SSH_KEY (optional) — path to the ssh key; default ~/.ssh/id_ed25519
#
#   VPS_HOST=root@203.0.113.10 ./deploy.sh
#
# What it does per connector:
#   1. rsyncs code to /srv/connectors/<slug> (excludes .venv, caches, sqlite)
#   2. (remote) creates/updates .venv and pip-installs requirements.txt
#   3. (remote) writes /etc/connectors/<slug>.env ONLY if missing (placeholders)
#   4. (remote) writes systemd unit qull-<slug>.service, daemon-reload, enable --now
#   5. (remote, once) writes the api.qull.io nginx vhost, tests + reloads nginx
#   6. (remote, once) runs certbot --nginx for api.qull.io if no cert exists yet
#
# Idempotent and non-destructive: re-running is safe. It NEVER deletes
# /srv/connectors/*/data, NEVER overwrites an existing env file, and NEVER
# touches the local connector source or sqlite files.
set -euo pipefail

VPS_HOST="${VPS_HOST:?ERROR: export VPS_HOST first, e.g. VPS_HOST=root@203.0.113.10}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH="ssh -i $VPS_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new $VPS_HOST"
CERT_EMAIL="${CERT_EMAIL:-admin@qull.io}"   # used once by certbot; override if needed

WORKSPACE="$HOME/workspace"
declare -A LOCAL_DIR=(
  [deposit-recovery]="$WORKSPACE/deposit-recovery"
)
SLUGS="deposit-recovery eu261-flight-comp subscription-slayer bill-negotiator final-paycheck class-action-cash unclaimed-property moving-concierge 401k-match medical-bill-fighter"
declare -A REST_PORT=(
  [deposit-recovery]=8471 [eu261-flight-comp]=8472 [subscription-slayer]=8473
  [bill-negotiator]=8474 [final-paycheck]=8475 [class-action-cash]=8476
  [unclaimed-property]=8477 [moving-concierge]=8478 [401k-match]=8479
  [medical-bill-fighter]=8480
)
declare -A MCP_PORT=(
  [deposit-recovery]=8571 [eu261-flight-comp]=8572 [subscription-slayer]=8573
  [bill-negotiator]=8574 [final-paycheck]=8575 [class-action-cash]=8576
  [unclaimed-property]=8577 [moving-concierge]=8578 [401k-match]=8579
  [medical-bill-fighter]=8580
)
# Per-connector env-file body (placeholders only — never real secrets).
# Dev-only flags are documented as MUST-NOT-SET in production.
env_body() {
  case "$1" in
    deposit-recovery) cat <<'EOF'
# Deposit Recovery — production env. Fill real values before going live.
DEPOSIT_HOST=127.0.0.1
#DEPOSIT_REST_PORT=8471
#DEPOSIT_MCP_PORT=8571
EOF
      ;;
    final-paycheck) cat <<'EOF'
# Final Paycheck — production env. Fill real values before going live.
#REST_PORT=8475
#MCP_PORT=8575
# DEV-ONLY — MUST be absent/unset in production:
#FINAL_PAYCHECK_STRIPE_MOCK=1
#FINAL_PAYCHECK_TODAY=2026-01-15
EOF
      ;;
    class-action-cash) cat <<'EOF'
# Class Action Cash — production env. Fill real values before going live.
# DEV-ONLY — MUST be absent/unset in production:
#CLASS_ACTION_CASH_DRY_RUN=1
EOF
      ;;
    medical-bill-fighter) cat <<'EOF'
# Medical Bill Fighter — production env. Fill real values before going live.
#MCP_PORT=8580
EOF
      ;;
    *) echo "# $1 — production env. No connector-specific vars today." ;;
  esac
  cat <<'EOF'
# ---- Identity / auth ----
# SERVICE_API_KEY: required Bearer token for POST /api/life-events (server-to-server).
# Generate: openssl rand -hex 32. MUST be set in production.
#SERVICE_API_KEY=
# ENV=production: disables /docs, /redoc, /openapi.json and the dev static site.
ENV=production
# PLATFORM_USER_HEADER: header the Muse platform injects with the user id (default X-Platform-User-Id).
#PLATFORM_USER_HEADER=X-Platform-User-Id
# ALLOW_DEV_IDENTITY: NEVER set to 1 in production (enables X-Dev-User-Id spoofing).
#ALLOW_DEV_IDENTITY=
# ---- Stripe / billing ----
# Production transport: direct Stripe REST via this key (restricted key, payments scope only).
# NEVER reuse a key that was exposed; create a fresh restricted key in the Stripe Dashboard.
#STRIPE_SECRET_KEY=
EOF
}

echo "==> preflight: ssh to $VPS_HOST"
$SSH "echo connected; test -d /srv/connectors"

for slug in $SLUGS; do
  src="${LOCAL_DIR[$slug]:-$WORKSPACE/connectors/$slug}"
  echo "==> [$slug] rsync code"
  rsync -az --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude 'data' --exclude '*.db' \
    -e "ssh -i $VPS_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    "$src/" "$VPS_HOST:/srv/connectors/$slug/"

  echo "==> [$slug] venv + requirements"
  $SSH "cd /srv/connectors/$slug && [ -x .venv/bin/python ] || python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && chown -R connectors:connectors /srv/connectors/$slug"

  echo "==> [$slug] env file (only if missing)"
  $SSH "test -f /etc/connectors/$slug.env || { install -o connectors -g connectors -m 640 /dev/null /etc/connectors/$slug.env; }"

  echo "==> [$slug] systemd unit"
  $SSH "cat > /etc/systemd/system/qull-$slug.service" <<EOF
[Unit]
Description=Qull Muse connector: $slug (REST+MCP)
After=network.target

[Service]
Type=simple
User=connectors
WorkingDirectory=/srv/connectors/$slug
EnvironmentFile=/etc/connectors/$slug.env
ExecStart=/srv/connectors/$slug/.venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  # push the env body only when the file is still empty (first deploy)
  if [ "$($SSH "wc -c < /etc/connectors/$slug.env")" -eq 0 ]; then
    env_body "$slug" | $SSH "cat > /etc/connectors/$slug.env && chown connectors:connectors /etc/connectors/$slug.env && chmod 640 /etc/connectors/$slug.env"
  fi

  $SSH "systemctl daemon-reload && systemctl enable --now qull-$slug.service"
done

echo "==> nginx vhost for api.qull.io"
VHOST="$(mktemp)"
{
# Trusted platform CIDRs for the identity header (space-separated).
# Empty (default) = the platform user-id header is blanked for ALL clients
# (spoof-proof); Meta's egress ranges go here once published.
TRUSTED_CIDRS="${PLATFORM_TRUSTED_CIDRS:-}"
cat <<'NGINX'
# Generated by qull deploy.sh — safe to regenerate.
# NOTE: geo/map/limit_req_zone live in the http context (sites-available is
# included inside http on Ubuntu nginx).

# Platform identity gating: X-Platform-User-Id is forwarded upstream ONLY when
# the request comes from a trusted platform CIDR. Every other client gets the
# header blanked, so a direct caller cannot spoof another user's identity.
# Until Meta publishes their egress ranges, the header is blanked for
# everyone: identity then resolves only via whatever mechanism Meta documents
# for reviewers (wire it in each connector's src/identity.py).
geo $platform_trusted {
    default 0;
NGINX
  for cidr in $TRUSTED_CIDRS; do
    echo "    $cidr 1;"
  done
cat <<'NGINX'
}
map $platform_trusted $forwarded_platform_user {
    1 $http_x_platform_user_id;
    0 "";
}
# NOTE: the map above assumes the default PLATFORM_USER_HEADER=X-Platform-User-Id.
# If you customize the header name, adjust $http_x_platform_user_id accordingly.

limit_req_zone $binary_remote_addr zone=qull_std:10m rate=120r/m;
limit_req_zone $binary_remote_addr zone=qull_money:10m rate=20r/m;

server {
    listen 80;
    server_name api.qull.io;

    # Defense in depth: cap request bodies at the edge (app also enforces 1MB).
    client_max_body_size 1m;

    # Security: REST + MCP are internal-only; everything goes through these
    # path-prefixed locations.
NGINX
  for slug in $SLUGS; do
    r="${REST_PORT[$slug]}"; m="${MCP_PORT[$slug]}"
    cat <<NGINX
    # ---- $slug ----
    location = /$slug { return 301 /$slug/; }
    location /$slug/api/ {
        limit_req zone=qull_std burst=40 nodelay;
        proxy_pass http://127.0.0.1:$r/api/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Platform-User-Id \$forwarded_platform_user;
    }
    location = /$slug/health {
        proxy_pass http://127.0.0.1:$r/health;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /$slug/mcp {
        limit_req zone=qull_money burst=20 nodelay;
        proxy_pass http://127.0.0.1:$m/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Platform-User-Id \$forwarded_platform_user;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    location /$slug/mcp/ {
        limit_req zone=qull_money burst=20 nodelay;
        proxy_pass http://127.0.0.1:$m/mcp/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Platform-User-Id \$forwarded_platform_user;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
NGINX
  done
  echo "}"
} > "$VHOST"
scp -i "$VPS_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$VHOST" "$VPS_HOST:/etc/nginx/sites-available/api.qull.io"
rm -f "$VHOST"
$SSH "ln -sf /etc/nginx/sites-available/api.qull.io /etc/nginx/sites-enabled/api.qull.io && nginx -t && systemctl reload nginx"

echo "==> TLS via certbot (skipped if a cert already exists)"
$SSH "[ -d /etc/letsencrypt/live/api.qull.io ] || certbot --nginx -d api.qull.io --non-interactive --agree-tos -m $CERT_EMAIL --redirect"

echo "==> status"
for slug in $SLUGS; do
  status="$($SSH "systemctl is-active qull-$slug.service" || echo INACTIVE)"
  echo "[$slug] $status"
done
echo "DONE. Public surface:"
echo "  REST: https://api.qull.io/<slug>/api/...   (health: https://api.qull.io/<slug>/health)"
echo "  MCP:  https://api.qull.io/<slug>/mcp"
