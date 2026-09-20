# Existing-server acceptance and rollback

Run these commands on the authorized server from the reviewed checkout. Replace the domain with the hostname already serving the connectors; do not introduce a new hostname during a code migration unless DNS and its certificate are ready. The commands below use the currently documented hostname as an example.

## Read-only inventory, before prepare

```bash
python3 backend/deploy/deployment_acceptance.py preflight \
  --domain 5.78.152.6.nip.io --phase inventory
sudo nginx -t
systemctl is-active qull-deposit-recovery.service
```

The JSON report lists only file existence/size/mode, storage paths, key modes, and conflict filenames. It never prints keys, registry entries, environment-file contents, customer records, or nginx configuration. Inventory can succeed while activation is unready; it is a description of the current layout. The tool neither connects to Stripe nor edits databases. It will not shell-source environment files.

Record the running commit/version, all ten systemd states, available disk space, and whether any customer operations are active. Inspect only the specific service logs needed for a failure; logs may contain customer information and must not be copied into public reports. Existing unowned records need a deliberate ownership decision supported by real account records. Deployment will not claim them for a reviewer account.

## Prepare, then gate activation

```bash
sudo DOMAIN=5.78.152.6.nip.io MODE=prepare bash backend/deploy/server-deploy.sh
sudo python3 backend/deploy/deployment_acceptance.py preflight \
  --domain 5.78.152.6.nip.io --phase activate
```

Prepare stages code and generated managed configuration. Existing running units retain their current managed configuration and existing secret-file ownership. Required missing configuration must be entered privately; do not paste raw credentials into a terminal transcript or chat.

Activation preflight fails for missing/inconsistent Stripe test/live modes, incompatible public URLs or ports, a custom final-paycheck database requiring migration, conflicting enabled nginx vhosts, or ambiguous old/new databases without a migration record. It does not decide that a live key should become test or vice versa. Preserve the actual account's intended mode and require explicit live-operation authorization before charging anyone.

If both `data/app.db` and `/var/lib/qull/<slug>/app.db` exist without a migration marker, stop and establish which database contains the authoritative records. Do not delete either copy or create a marker merely to bypass the gate. After a standard migration, the marker records the old database hash; any subsequent write by a legacy process blocks the next deployment until reconciled.

For an enabled legacy nginx vhost conflict, back up the old symlink/configuration and deliberately consolidate it into the managed `qull-connectors` vhost. The preflight reports names only. Never disable unrelated hostnames. The activation script also validates certificate hostname/expiry and parses the candidate nginx configuration before stopping services.

## Activate and verify

```bash
sudo DOMAIN=5.78.152.6.nip.io MODE=activate bash backend/deploy/server-deploy.sh
python3 backend/deploy/deployment_acceptance.py verify \
  --base https://5.78.152.6.nip.io
```

For an authenticated middleware check, point to an already provisioned private reviewer key file:

```bash
sudo python3 backend/deploy/deployment_acceptance.py verify \
  --base https://5.78.152.6.nip.io --key-file /root/qull-reviewer.key
```

Verification sends only GET requests. It checks health, readiness, missing-key rejection, platform-header spoof rejection, and the generic payment-return page. With a key file, it expects a normal `404` at an intentionally nonexistent authenticated path, proving the key passed the authentication boundary without reading customer data. The tool refuses redirects so a bearer credential cannot be forwarded to another origin. It prints response codes only and never response bodies or the key.

This verifies deployment and authentication boundaries, not the complete service. Run the repository's isolated workflow tests and the authorized Stripe test-mode acceptance flow separately; verify MCP with the actual Muse client before submission. Do not introduce real customer charges into a deployment smoke test.

## Rollback decision and commands

Activation records a private directory under `/var/backups/qull/<timestamp>`. It contains `etc-connectors.tar.gz`, `etc-nginx.tar.gz`, the old `<slug>.service`, and separate `<slug>-code.tar.gz`/`<slug>-data.tar.gz` for every connector reached by the cutover. Since cutover is sequential, an aborted deployment may not have a snapshot for untouched connectors. Leave those connectors alone.

Use a selected connector's own snapshot. First stop it and preserve the failed release/data rather than overwriting evidence. These are operator commands, not an automatically executed rollback:

```bash
# Set these to the actual reported snapshot and affected connector.
QULL_SNAPSHOT=/var/backups/qull/ACTUAL_TIMESTAMP
QULL_SLUG=deposit-recovery
sudo systemctl stop "qull-$QULL_SLUG.service"
sudo install -d -m 0700 "$QULL_SNAPSHOT/failed-release"
sudo mv "/srv/connectors/$QULL_SLUG" "$QULL_SNAPSHOT/failed-release/code-$QULL_SLUG"
sudo tar -xzf "$QULL_SNAPSHOT/$QULL_SLUG-code.tar.gz" -C /srv/connectors
sudo cp -a "$QULL_SNAPSHOT/$QULL_SLUG.service" "/etc/systemd/system/qull-$QULL_SLUG.service"
```

Restore that connector's old environment entries from `etc-connectors.tar.gz` into a private temporary directory, then copy only its files back with their archived ownership and modes. Restore the full configuration archive only during a deliberately coordinated all-service rollback; doing so during a single-service rollback could revert other services that already passed acceptance. If the snapshot lacks a prior `.managed.env` but activation created one, remove that one newly created managed file only after the old unit/configuration is restored and retained for investigation.

**Do not restore an old payment ledger over newer successful payments.** Keep the failed durable directory intact and reconcile Stripe payment IDs and confirmed fees first. A code-only rollback may use the old application's preserved source database, so compare it with the new durable database before restarting any route that could charge. If no operations occurred after cutover, an operator may restore the matching `-data.tar.gz` into `/var/lib/qull` after moving the current directory into the private failed-release archive. Do not merge arbitrary SQLite/WAL files from different snapshots.

After the database and configuration decision:

```bash
sudo systemctl daemon-reload
sudo systemctl start "qull-$QULL_SLUG.service"
sudo nginx -t
```

If nginx configuration was changed and must be restored, unpack the saved nginx tree into a private staging directory, compare only the affected vhost and enabled symlink, restore them, then validate and reload nginx. Avoid replacing unrelated hostnames. Repeat the GET verification appropriate to the restored version; old releases may not implement `/ready`, and that limitation must be reported instead of treating a `404` as success.

No scheduled backup system is installed by these scripts. Release snapshots provide a migration rollback point only. Before commercial launch, configure encrypted off-host backups with documented retention and perform a restore rehearsal that also reconciles payment state. Preserve the existing hosting backup feature if present; do not enable billable hosting backups or install an automated restore job implicitly as part of this code deployment.
