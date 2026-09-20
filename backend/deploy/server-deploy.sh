#!/usr/bin/env bash
# Run from a reviewed repository backend/deploy/ on Ubuntu 24.04 as root.
# MODE=prepare installs a release/config templates without starting it.
# MODE=activate validates config, snapshots data, restarts services, and reloads nginx.
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${DOMAIN:-api.qull.io}"
MODE="${MODE:-prepare}"
if [[ ! "$MODE" =~ ^(prepare|activate)$ ]]; then echo 'MODE must be prepare or activate' >&2; exit 1; fi
python3 "$ROOT/deploy/render-nginx.py" --domain "$DOMAIN" >/dev/null
SLUGS=(deposit-recovery eu261-flight-comp subscription-slayer bill-negotiator final-paycheck class-action-cash unclaimed-property moving-concierge 401k-match medical-bill-fighter)
for slug in "${SLUGS[@]}"; do
  test -f "$ROOT/$slug/app.py"
  test -f "$ROOT/$slug/requirements.txt"
done
test -d "$ROOT/api_support"
test -d "$ROOT/payment_support"
# Package installation is explicit and only required on a new server.
if ! getent group connectors >/dev/null || ! command -v nginx >/dev/null; then
  echo 'Run backend/deploy/vps-setup.sh first.' >&2; exit 1
fi
for slug in "${SLUGS[@]}"; do
  user="qull-$slug"
  id "$user" >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "$user"
  usermod -a -G connectors "$user"
  if [[ ! -d "/srv/connectors/$slug" ]]; then install -d -o root -g "$user" -m 0750 "/srv/connectors/$slug"; fi
  install -d -o "$user" -g "$user" -m 0700 "/var/lib/qull/$slug"
  envf="/etc/connectors/$slug.env"
  if [[ ! -f "$envf" ]]; then
    install -o root -g "$user" -m 0640 /dev/null "$envf"
    cat > "$envf" <<EOF
# Configure privately. No raw user API keys belong here.
STRIPE_MODE=test
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
EOF
  fi
  if [[ "$MODE" == activate ]]; then chown root:"$user" "$envf"; chmod 0640 "$envf"; fi
  # Nonsecret managed values are separated from existing operator secrets.
  cat > "/etc/connectors/$slug.managed.env" <<EOF
ENV=production
HOST=127.0.0.1
DATA_DIR=/var/lib/qull/$slug
BILLING_LEDGER_PATH=/var/lib/qull/$slug/billing.sqlite3
QULL_API_KEYS_FILE=/etc/connectors/user-keys.json
PUBLIC_BASE_URL=https://$DOMAIN/$slug
EOF
  chown root:"$user" "/etc/connectors/$slug.managed.env"
  chmod 0640 "/etc/connectors/$slug.managed.env"
  # Stage separately so prepare mode cannot alter a running service's code.
  staged="/srv/connectors/.staged-$slug"
  install -d -o root -g "$user" -m 0750 "$staged"
  rsync -a --delete-delay --exclude '.venv' --exclude '__pycache__' --exclude '*.db*' \
    --exclude '*.sqlite*' --exclude '.env*' --exclude '*.env' --exclude 'letters_out' --exclude 'packs_out' \
    "$ROOT/$slug/" "$staged/"
  rsync -a --delete "$ROOT/payment_support/" "$staged/payment_support/"
  rsync -a --delete "$ROOT/api_support/" "$staged/api_support/"
  test -x "$staged/.venv/bin/python" || python3.12 -m venv "$staged/.venv"
  "$staged/.venv/bin/pip" install --quiet --no-input -r "$staged/requirements.txt"
  chown -R root:"$user" "$staged"
  chmod -R g+rX,o-rwx "$staged"
done
if [[ "$MODE" == prepare ]]; then
  echo 'Prepared all ten releases. Configure Stripe keys and user API-key registry, then use MODE=activate.'
  exit 0
fi
# Check certificate prerequisites before any service is stopped.
test -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" || { echo "Missing TLS certificate for $DOMAIN." >&2; exit 1; }
test -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem"
# Preflight the staged release as its service identity without shell-sourcing secrets.
for slug in "${SLUGS[@]}"; do
  systemd-run --quiet --wait --pipe --collect -p "User=qull-$slug" \
    -p "WorkingDirectory=/srv/connectors/.staged-$slug" \
    -p "EnvironmentFile=/etc/connectors/$slug.env" \
    -p "EnvironmentFile=/etc/connectors/$slug.managed.env" \
    /srv/connectors/.staged-"$slug"/.venv/bin/python -c \
    "import importlib.util,pathlib,sys,os; legacy=os.environ.get('FINAL_PAYCHECK_DB'); expected=str(pathlib.Path(os.environ['DATA_DIR'])/'app.db'); assert not legacy or legacy == expected, 'Migrate custom FINAL_PAYCHECK_DB before activation'; p=pathlib.Path('src/identity.py'); p=p if p.exists() else pathlib.Path('identity.py'); s=importlib.util.spec_from_file_location('identity_check',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); ready,_=m.readiness(); print('configuration ready' if ready else 'configuration incomplete'); sys.exit(0 if ready else 1)"
done
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "/var/backups/qull/$stamp"
for slug in "${SLUGS[@]}"; do
  if [[ -f "/etc/systemd/system/qull-$slug.service" ]]; then
    cp -a "/etc/systemd/system/qull-$slug.service" "/var/backups/qull/$stamp/$slug.service"
  fi
  if systemctl cat "qull-$slug.service" >/dev/null 2>&1; then systemctl stop "qull-$slug.service"; fi
  # Snapshot source and data before migration. Preserve originals for rollback.
  tar -czf "/var/backups/qull/$stamp/$slug-code.tar.gz" -C /srv/connectors "$slug"
  tar -czf "/var/backups/qull/$stamp/$slug-data.tar.gz" -C /var/lib/qull "$slug"
  if [[ -f "/srv/connectors/$slug/data/app.db" && ! -f "/var/lib/qull/$slug/app.db" ]]; then
    python3 - "$slug" <<'PY'
import sqlite3,sys
slug=sys.argv[1]
with sqlite3.connect(f'/srv/connectors/{slug}/data/app.db') as source:
    with sqlite3.connect(f'/var/lib/qull/{slug}/app.db') as destination:
        source.backup(destination)
PY
  fi
  for dir in letters_out packs_out claim_packs; do
    if [[ -d "/srv/connectors/$slug/$dir" ]]; then
      target="$dir"
      if [[ "$dir" == letters_out ]]; then target=letters; fi
      if [[ "$slug" == eu261-flight-comp ]]; then target=claim_packs; fi
      mkdir -p "/var/lib/qull/$slug/$target"
      rsync -a --ignore-existing "/srv/connectors/$slug/$dir/" "/var/lib/qull/$slug/$target/"
    fi
  done
  rsync -a --delete-delay --exclude 'data/*.db*' --exclude '*.sqlite*' --exclude 'letters_out' --exclude 'packs_out' \
    "/srv/connectors/.staged-$slug/" "/srv/connectors/$slug/"
  chown -R root:"qull-$slug" "/srv/connectors/$slug"
  chown -R "qull-$slug:qull-$slug" "/var/lib/qull/$slug"
  cat > "/etc/systemd/system/qull-$slug.service" <<EOF
[Unit]
Description=Qull connector $slug REST and MCP
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=qull-$slug
Group=qull-$slug
SupplementaryGroups=connectors
WorkingDirectory=/srv/connectors/$slug
EnvironmentFile=/etc/connectors/$slug.env
EnvironmentFile=/etc/connectors/$slug.managed.env
ExecStart=/srv/connectors/$slug/.venv/bin/python run.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/qull/$slug
[Install]
WantedBy=multi-user.target
EOF
done
systemctl daemon-reload
for index in "${!SLUGS[@]}"; do
  slug="${SLUGS[$index]}"
  systemctl enable --now "qull-$slug.service"
  ok=0
  for attempt in {1..20}; do
    if curl --fail --silent --max-time 2 "http://127.0.0.1:$((8471+index))/ready" >/dev/null; then ok=1; break; fi
    sleep 1
  done
  if [[ "$ok" != 1 ]]; then echo "$slug did not become ready. Prior snapshot: /var/backups/qull/$stamp" >&2; exit 1; fi
done
# Preserve TLS on every redeploy. Initial certificate issuance is a separate action.
test -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" || { echo 'Issue the domain TLS certificate before activating nginx.' >&2; exit 1; }
vhost=/etc/nginx/sites-available/qull-connectors
if [[ -f "$vhost" ]]; then cp -a "$vhost" "/var/backups/qull/$stamp/nginx.conf"; fi
python3 "$ROOT/deploy/render-nginx.py" --domain "$DOMAIN" --tls > "$vhost"
ln -sfn "$vhost" /etc/nginx/sites-enabled/qull-connectors
if ! nginx -t; then
  if [[ -f "/var/backups/qull/$stamp/nginx.conf" ]]; then cp -a "/var/backups/qull/$stamp/nginx.conf" "$vhost"; fi
  echo 'nginx validation failed; existing running nginx was not reloaded.' >&2; exit 1
fi
systemctl reload nginx
echo "Activated all ten. Complete authenticated end-to-end tests against https://$DOMAIN before submission."
