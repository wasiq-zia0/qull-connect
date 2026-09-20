#!/usr/bin/env bash
# server-deploy.sh — all-in-one deploy for the 10 Qull connectors.
# Runs ON the VPS as root. No SSH needed.
#
# Usage (paste into the Hetzner web console as root):
#   curl -sSL <bundle-url> -o /tmp/qull.tar.gz \
#     && mkdir -p /tmp/qull && tar -xzf /tmp/qull.tar.gz -C /tmp/qull \
#     && bash /tmp/qull/server-deploy.sh
#
# Env overrides:
#   DOMAIN      public domain for nginx+certbot (default: 5.78.152.6.nip.io)
#   CERT_EMAIL  certbot contact email            (default: admin@qull.io)
#
# Idempotent: safe to re-run. Never deletes /srv/connectors/*/data,
# never overwrites an existing env file.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "ERROR: run as root." >&2; exit 1; fi

DOMAIN="${DOMAIN:-5.78.152.6.nip.io}"
CERT_EMAIL="${CERT_EMAIL:-admin@qull.io}"
BUNDLE="$(cd "$(dirname "$0")" && pwd)"
CODE="$BUNDLE/code"

SLUGS="deposit-recovery eu261-flight-comp subscription-slayer bill-negotiator final-paycheck class-action-cash unclaimed-property moving-concierge 401k-match medical-bill-fighter"

rest_port() { case "$1" in
  deposit-recovery) echo 8471;; eu261-flight-comp) echo 8472;;
  subscription-slayer) echo 8473;; bill-negotiator) echo 8474;;
  final-paycheck) echo 8475;; class-action-cash) echo 8476;;
  unclaimed-property) echo 8477;; moving-concierge) echo 8478;;
  401k-match) echo 8479;; medical-bill-fighter) echo 8480;; esac; }
mcp_port() { case "$1" in
  deposit-recovery) echo 8571;; eu261-flight-comp) echo 8572;;
  subscription-slayer) echo 8573;; bill-negotiator) echo 8574;;
  final-paycheck) echo 8575;; class-action-cash) echo 8576;;
  unclaimed-property) echo 8577;; moving-concierge) echo 8578;;
  401k-match) echo 8579;; medical-bill-fighter) echo 8580;; esac; }

# Per-connector env-file body. SERVICE_API_KEY is generated below.
env_body() {
  case "$1" in
    deposit-recovery) cat <<'EOF'
# Deposit Recovery — production env.
DEPOSIT_HOST=127.0.0.1
#DEPOSIT_REST_PORT=8471
#DEPOSIT_MCP_PORT=8571
EOF
      ;;
    final-paycheck) cat <<'EOF'
# Final Paycheck — production env.
#REST_PORT=8475
#MCP_PORT=8575
# DEV-ONLY — MUST be absent/unset in production:
#FINAL_PAYCHECK_STRIPE_MOCK=1
#FINAL_PAYCHECK_TODAY=2026-01-15
EOF
      ;;
    class-action-cash) cat <<'EOF'
# Class Action Cash — production env.
# DEV-ONLY — MUST be absent/unset in production:
#CLASS_ACTION_CASH_DRY_RUN=1
EOF
      ;;
    medical-bill-fighter) cat <<'EOF'
# Medical Bill Fighter — production env.
#MCP_PORT=8580
EOF
      ;;
    *) echo "# $1 — production env. No connector-specific vars today." ;;
  esac
  cat <<EOF
# ---- Identity / auth ----
# SERVICE_API_KEY: required Bearer token for POST /api/life-events (server-to-server).
SERVICE_API_KEY=$SERVICE_API_KEY
# ENV=production: disables /docs, /redoc, /openapi.json and the dev static site.
ENV=production
# PLATFORM_USER_HEADER: header the Muse platform injects with the user id (default X-Platform-User-Id).
#PLATFORM_USER_HEADER=X-Platform-User-Id
# ALLOW_DEV_IDENTITY: NEVER set to 1 in production (enables X-Dev-User-Id spoofing).
#ALLOW_DEV_IDENTITY=
# ---- Stripe / billing ----
# Production transport: direct Stripe REST via this key (restricted key, payments scope only).
# Billing stays INERT until this is set. Add it only when ready for the live charge test.
#STRIPE_SECRET_KEY=
EOF
}

echo "==> [1/7] base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  python3.12 python3.12-venv python3.12-dev python3-pip \
  build-essential nginx certbot python3-certbot-nginx \
  curl ca-certificates openssl

echo "==> [2/7] service user + layout"
id connectors >/dev/null 2>&1 || useradd -r -m -s /bin/bash connectors
mkdir -p /etc/connectors
for slug in $SLUGS; do mkdir -p "/srv/connectors/$slug"; done

echo "==> [3/7] copy code"
for slug in $SLUGS; do
  if [ ! -d "$CODE/$slug" ]; then echo "ERROR: bundle missing code/$slug" >&2; exit 1; fi
  cp -a "$CODE/$slug/." "/srv/connectors/$slug/"
done
chown -R connectors:connectors /srv/connectors

echo "==> [4/7] venvs + requirements (this is the slow part, ~10 min)"
for slug in $SLUGS; do
  echo "  - $slug"
  cd "/srv/connectors/$slug"
  [ -x .venv/bin/python ] || python3.12 -m venv .venv
  .venv/bin/pip install -q --no-input -r requirements.txt
done
chown -R connectors:connectors /srv/connectors

echo "==> [5/7] env files (created once, never overwritten)"
SERVICE_API_KEY="$(openssl rand -hex 32)"
for slug in $SLUGS; do
  envf="/etc/connectors/$slug.env"
  if [ ! -s "$envf" ]; then
    install -o connectors -g connectors -m 640 /dev/null "$envf"
    env_body "$slug" > "$envf"
    chown connectors:connectors "$envf"; chmod 640 "$envf"
  fi
done

echo "==> [6/7] systemd units"
for slug in $SLUGS; do
  cat > "/etc/systemd/system/qull-$slug.service" <<EOF
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
done
systemctl daemon-reload
for slug in $SLUGS; do systemctl enable --now "qull-$slug.service"; done

echo "==> [7/7] nginx vhost + TLS for $DOMAIN"
VHOST="$(mktemp)"
{
cat <<'NGINX'
# Generated by qull server-deploy.sh — safe to regenerate.
# geo/map/limit_req_zone live in the http context (sites-available is
# included inside http on Ubuntu nginx).

# Platform identity gating: X-Platform-User-Id is forwarded upstream ONLY when
# the request comes from a trusted platform CIDR. Every other client gets the
# header blanked, so a direct caller cannot spoof another user's identity.
geo $platform_trusted {
    default 0;
}
map $platform_trusted $forwarded_platform_user {
    1 $http_x_platform_user_id;
    0 "";
}

limit_req_zone $binary_remote_addr zone=qull_std:10m rate=120r/m;
limit_req_zone $binary_remote_addr zone=qull_money:10m rate=20r/m;

server {
    listen 80;
NGINX
  echo "    server_name $DOMAIN;"
  cat <<'NGINX'

    # Defense in depth: cap request bodies at the edge (app also enforces 1MB).
    client_max_body_size 1m;
NGINX
  for slug in $SLUGS; do
    r="$(rest_port "$slug")"; m="$(mcp_port "$slug")"
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
cp "$VHOST" /etc/nginx/sites-available/qull-connectors
rm -f "$VHOST"
ln -sf /etc/nginx/sites-available/qull-connectors /etc/nginx/sites-enabled/qull-connectors
nginx -t && systemctl reload nginx

if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERT_EMAIL" --redirect
else
  echo "cert exists for $DOMAIN, skipping certbot"
fi

echo ""
echo "================ SERVICE STATUS ================"
for slug in $SLUGS; do
  r="$(rest_port "$slug")"
  active="$(systemctl is-active "qull-$slug.service" || true)"
  if [ "$slug" = "deposit-recovery" ]; then
    health="n/a (no /health route)"
  else
    if curl -sf -m 5 "http://127.0.0.1:$r/health" >/dev/null 2>&1; then health="healthy"; else health="NOT RESPONDING"; fi
  fi
  printf "%-22s %-8s %s\n" "$slug" "$active" "$health"
done
echo "================================================"
echo "Public surface:"
echo "  REST: https://$DOMAIN/<slug>/api/..."
echo "  MCP:  https://$DOMAIN/<slug>/mcp"
echo "Billing is INERT until STRIPE_SECRET_KEY is set in /etc/connectors/<slug>.env"
echo "DONE."
