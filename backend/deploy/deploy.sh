#!/usr/bin/env bash
# Upload this repository's backend/ to an existing server. Default: prepare only.
set -euo pipefail
: "${VPS_HOST:?Set VPS_HOST to a trusted SSH alias or user@hostname}"
if [[ ! "$VPS_HOST" =~ ^[A-Za-z0-9_.@:-]+$ ]]; then echo 'Invalid VPS_HOST' >&2; exit 1; fi
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
MODE="${MODE:-prepare}"
DOMAIN="${DOMAIN:-api.qull.io}"
if [[ ! "$DOMAIN" =~ ^[a-z0-9.-]+$ || ! "$MODE" =~ ^(prepare|activate)$ ]]; then echo 'Invalid DOMAIN or MODE' >&2; exit 1; fi
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -i "$VPS_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes "$VPS_HOST")
# Verify the server key out of band and populate known_hosts before this command.
"${SSH[@]}" 'install -d -m 0700 /srv/qull-release'
rsync -az --delete-delay --exclude '.venv' --exclude '__pycache__' --exclude '*.db*' \
  --exclude '*.sqlite*' --exclude '.env*' --exclude '*.env' --exclude 'letters_out' --exclude 'packs_out' \
  -e "ssh -i $VPS_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=yes" \
  "$SOURCE/" "$VPS_HOST:/srv/qull-release/backend/"
"${SSH[@]}" "DOMAIN='$DOMAIN' MODE='$MODE' bash /srv/qull-release/backend/deploy/server-deploy.sh"
