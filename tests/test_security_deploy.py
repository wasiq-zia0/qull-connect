import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SLUGS = ('deposit-recovery', 'eu261-flight-comp', 'subscription-slayer', 'bill-negotiator',
         'final-paycheck', 'class-action-cash', 'unclaimed-property', 'moving-concierge',
         '401k-match', 'medical-bill-fighter')


class DeploymentTests(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ('deploy.sh', 'server-deploy.sh', 'vps-setup.sh'):
            subprocess.run(['bash', '-n', str(ROOT / 'backend/deploy' / name)], check=True)

    def test_nginx_routing_tls_and_untrusted_header_stripping(self):
        spec = importlib.util.spec_from_file_location('nginx_renderer', ROOT / 'backend/deploy/render-nginx.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = module.render('api.example.test', tls=True)
        self.assertIn('listen 443 ssl;', output)
        self.assertIn('/etc/letsencrypt/live/api.example.test/fullchain.pem', output)
        self.assertIn('client_max_body_size 1000000;', output)
        self.assertIn('proxy_set_header Authorization $http_authorization;', output)
        self.assertNotIn('$http_x_platform_user_id', output)
        for index, slug in enumerate(SLUGS):
            self.assertIn(f'location /{slug}/ {{', output)
            self.assertIn(f'proxy_pass http://127.0.0.1:{8471+index}/;', output)
            self.assertIn(f'proxy_pass http://127.0.0.1:{8571+index}/mcp;', output)
        for invalid in ('https://host/path', 'host;include /private;', 'host\nserver{}'):
            with self.assertRaises(ValueError):
                module.render(invalid)

    def test_build_context_retains_reference_assets_and_shared_packages(self):
        for slug in SLUGS:
            docker = (ROOT / 'backend' / slug / 'Dockerfile').read_text()
            self.assertIn(f'COPY {slug}/ ./', docker)
            self.assertIn('COPY payment_support/ ./payment_support/', docker)
            self.assertIn('COPY api_support/ ./api_support/', docker)
            self.assertIn('USER qull', docker)
            self.assertIn('VOLUME ["/var/lib/qull"]', docker)
            self.assertNotIn('--exclude=data', docker)
        ignore = (ROOT / 'backend/.dockerignore').read_text().splitlines()
        self.assertNotIn('data', ignore)
        self.assertNotIn('**/data', ignore)

    def run_supervisor(self, fail):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            shutil.copyfile(ROOT / 'backend/deposit-recovery/run.py', folder / 'run.py')
            (folder / 'mcp_server.py').write_text('def create_mcp_app(): return None\n')
            (folder / 'uvicorn.py').write_text('''import os,time
from pathlib import Path

def run(*args, **kwargs):
    Path('mcp.pid').write_text(str(os.getpid()))
    if os.environ.get('FAIL_CHILD') == 'mcp':
        time.sleep(.3)
        raise SystemExit(7)
    time.sleep(60)

if __name__ == '__main__':
    Path('rest.pid').write_text(str(os.getpid()))
    if os.environ.get('FAIL_CHILD') == 'rest':
        time.sleep(.3)
        raise SystemExit(9)
    time.sleep(60)
''')
            process = subprocess.Popen([sys.executable, str(folder / 'run.py')], cwd=folder,
                                       env={**os.environ, 'FAIL_CHILD': fail}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                if fail == 'signal':
                    deadline = time.monotonic() + 5
                    while not ((folder/'rest.pid').exists() and (folder/'mcp.pid').exists()):
                        if time.monotonic() > deadline:
                            self.fail('Children did not start')
                        time.sleep(.02)
                    process.send_signal(signal.SIGTERM)
                code = process.wait(timeout=5)
                self.assertEqual(code, 0 if fail == 'signal' else (7 if fail == 'mcp' else 9))
                for name in ('rest.pid', 'mcp.pid'):
                    pid = int((folder / name).read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def test_rest_failure_stops_mcp(self):
        self.run_supervisor('rest')

    def test_mcp_failure_stops_rest(self):
        self.run_supervisor('mcp')

    def test_shutdown_reaps_both_children(self):
        self.run_supervisor('signal')


if __name__ == '__main__':
    unittest.main()
