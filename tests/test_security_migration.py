import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'backend/deploy' / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


acceptance = module('deployment_acceptance')
staging = module('stage-config')


class MigrationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        cert = self.root / 'etc/letsencrypt/live/api.example.test'
        cert.mkdir(parents=True)
        for name in ('fullchain.pem', 'privkey.pem'):
            (cert / name).write_text('test fixture; no real TLS material')
        self.env = self.root / 'etc/connectors'
        self.env.mkdir(parents=True)
        for slug in acceptance.SLUGS:
            (self.env / (slug + '.env')).write_text('STRIPE_MODE=test\nSTRIPE_SECRET_KEY=sk_test_NEVER_PRINT\nSTRIPE_PUBLISHABLE_KEY=pk_test_NEVER_PRINT\n')
            staging.stage(self.root / 'srv/connectors' / ('.staged-' + slug), slug, 'api.example.test')

    def preflight(self):
        return acceptance.inventory(self.root, 'api.example.test', 'activate')

    def test_prepare_preserves_running_managed_config(self):
        live = self.env / 'deposit-recovery.managed.env'
        original = b'ENV=production\nPUBLIC_BASE_URL=https://old.example.test/deposit-recovery\n'
        live.write_bytes(original)
        staged = staging.stage(self.root / 'srv/connectors/.staged-deposit-recovery', 'deposit-recovery', 'new.example.test')
        self.assertEqual(live.read_bytes(), original)
        self.assertIn('https://new.example.test/deposit-recovery', staged.read_text())
        self.assertEqual(staged.stat().st_mode & 0o777, 0o640)

    def test_preflight_modes_and_no_secrets_in_report(self):
        result = self.preflight()
        self.assertTrue(result['passed'])
        self.assertNotIn('NEVER_PRINT', json.dumps(result))
        (self.env / 'deposit-recovery.env').write_text('STRIPE_SECRET_KEY=rk_live_NEVER_PRINT\nSTRIPE_PUBLISHABLE_KEY=pk_test_NEVER_PRINT\n')
        result = self.preflight()
        self.assertFalse(result['passed'])
        self.assertNotIn('NEVER_PRINT', json.dumps(result))
        self.assertEqual(result['checks']['connectors']['deposit-recovery']['secret_key_mode'], 'live')

    def test_nginx_conflict_and_custom_database_are_blockers(self):
        enabled = self.root / 'etc/nginx/sites-enabled'
        enabled.mkdir(parents=True)
        (enabled / 'legacy-qull').write_text('server_name api.example.test;\nlimit_req_zone $binary_remote_addr zone=qull_std:10m rate=120r/m;')
        env = self.env / 'final-paycheck.env'
        env.write_text(env.read_text() + 'FINAL_PAYCHECK_DB=/different/location/customer.db\n')
        result = self.preflight()
        self.assertFalse(result['passed'])
        self.assertIn('legacy-qull', result['checks']['conflicting_enabled_nginx_files'])
        self.assertTrue(any('custom FINAL_PAYCHECK_DB' in error for error in result['errors']))

    def test_ambiguous_database_copies_block_activation(self):
        old = self.root / 'srv/connectors/deposit-recovery/data/app.db'
        new = self.root / 'var/lib/qull/deposit-recovery/app.db'
        for path in (old, new):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'database fixture')
        self.assertTrue(any('both legacy and durable' in error for error in self.preflight()['errors']))
        (new.parent / '.legacy-migration.json').write_text(json.dumps({'legacy_sha256': acceptance.digest(old)}))
        self.assertTrue(self.preflight()['passed'])
        old.write_bytes(b'changed by legacy process')
        self.assertTrue(any('changed after recorded migration' in error for error in self.preflight()['errors']))

    def test_deploy_gates_and_snapshots_precede_first_stop(self):
        # Cross-check integration ordering, not only the isolated helper functions.
        script = (ROOT / 'backend/deploy/server-deploy.sh').read_text()
        stop = script.index('systemctl stop "qull-$slug.service"')
        for required in ('deployment_acceptance.py" preflight', 'nginx -t -c', 'etc-connectors.tar.gz', 'etc-nginx.tar.gz'):
            self.assertLess(script.index(required), stop, required)
        self.assertGreater(script.index('install -o root -g "qull-$slug" -m 0640'), stop)
        prepare = script[:script.index('if [[ "$MODE" == prepare ]]')]
        self.assertNotIn('> "/etc/connectors/$slug.managed.env"', prepare)
        self.assertNotIn('chown root:"$user" "$envf"', prepare)
        self.assertIn('Restart and verify this connector before stopping the next one', script)


if __name__ == '__main__':
    unittest.main()
