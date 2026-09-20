#!/usr/bin/env bash
# vps-setup.sh — one-time provisioning for the Qull connectors VPS.
#
# Run ONCE on a fresh Ubuntu 24.04 VPS, as root (or via sudo):
#   curl -fsSL <url>/vps-setup.sh | sudo bash
# or: scp vps-setup.sh root@<vps>: && ssh root@<vps> bash vps-setup.sh
#
# What it does:
#   - installs Python 3.12, venv support, nginx, certbot (+ nginx plugin), rsync
#   - creates the unprivileged service user `connectors`
#   - creates /srv/connectors/<slug> layout and /etc/connectors for env files
#
# Idempotent: safe to re-run. It never touches /srv/connectors/*/data
# and never overwrites existing env files.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root (or with sudo)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  python3.12 python3.12-venv python3-pip \
  nginx certbot python3-certbot-nginx \
  rsync curl ca-certificates

if ! id connectors >/dev/null 2>&1; then
  useradd -r -m -s /bin/bash connectors
  echo "created user: connectors"
fi

SLUGS="deposit-recovery eu261-flight-comp subscription-slayer bill-negotiator final-paycheck class-action-cash unclaimed-property moving-concierge 401k-match medical-bill-fighter"

mkdir -p /etc/connectors
chmod 755 /etc/connectors
for slug in $SLUGS; do
  mkdir -p "/srv/connectors/$slug"
done
chown -R connectors:connectors /srv/connectors

# HTTP/HTTPS for nginx + certbot (only touches ufw if it is installed & active)
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  echo "ufw: opened 80,443"
fi

systemctl enable --now nginx

echo "OK: VPS base ready. Next: point api.qull.io DNS at this server, then run deploy.sh"
