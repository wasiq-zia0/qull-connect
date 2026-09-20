#!/usr/bin/env bash
# Prepare Ubuntu 24.04 packages and directories. No application activation.
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends python3.12 python3.12-venv python3-pip \
  build-essential nginx certbot python3-certbot-nginx rsync curl ca-certificates
getent group connectors >/dev/null || groupadd --system connectors
install -d -o root -g connectors -m 0750 /etc/connectors
install -d -o root -g root -m 0755 /srv/connectors /var/lib/qull /var/backups/qull
if [[ ! -f /etc/connectors/user-keys.json ]]; then
  install -o root -g connectors -m 0640 /dev/null /etc/connectors/user-keys.json
  echo '{"version":1,"keys":[]}' > /etc/connectors/user-keys.json
fi
systemctl enable --now nginx
