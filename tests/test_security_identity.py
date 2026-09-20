import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx2 as httpx

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('review_' + name, ROOT / 'backend/deploy/shared' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = load('identity')
limits = load('limits')


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = Path(self.temp.name) / 'keys.json'
        self.secret = 'qk_' + 'test-only-key-material-' * 3
        self.entry = {'sha256': hashlib.sha256(self.secret.encode()).hexdigest(), 'owner_id': 'user-alice'}
        self.write_registry([self.entry])
        self.env = patch.dict(os.environ, {'ENV': 'production', 'QULL_API_KEYS_FILE': str(self.registry),
                                          'STRIPE_SECRET_KEY': 'sk_test_not_a_real_key', 'STRIPE_MODE': 'test',
                                          'DATA_DIR': self.temp.name, 'STRIPE_PUBLISHABLE_KEY': 'pk_test_not_real',
                                          'PUBLIC_BASE_URL': 'https://example.test/connector'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        app = FastAPI()
        @app.get('/health')
        def health():
            return {'status': 'ok'}
        @app.get('/billing/return')
        def returned():
            return {'message': 'Return to your conversation'}
        @app.get('/private')
        async def private():
            owner = identity.require_owner()
            await asyncio.sleep(.001)
            assert identity.require_owner() == owner
            return {'owner': owner}
        @app.post('/api/life-events')
        async def event():
            return {'owner': identity.require_owner()}
        app.add_middleware(identity.IdentityMiddleware)
        self.app = app
        self.client = TestClient(app)

    def write_registry(self, entries):
        self.registry.write_text(json.dumps({'version': 1, 'keys': entries}))
        self.registry.chmod(0o600)

    def auth(self):
        return {'Authorization': 'Bearer ' + self.secret}

    def test_user_bearer_key_and_revocation(self):
        self.assertEqual(self.client.get('/private', headers=self.auth()).json(), {'owner': 'user-alice'})
        self.write_registry([{**self.entry, 'disabled': True}])
        response = self.client.get('/private', headers=self.auth())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers['www-authenticate'], 'Bearer')
        self.assertIsNone(identity.current_owner())

    def test_untrusted_identity_headers_and_retired_service_key_do_not_authenticate(self):
        with patch.dict(os.environ, {'SERVICE_API_KEY': self.secret}):
            for headers in ({'X-Platform-User-Id': 'user-alice'}, {'X-Dev-User-Id': 'alice'},
                            {'Authorization': 'Bearer ' + 'unknown-key-' * 5}):
                self.assertEqual(self.client.get('/private', headers=headers).status_code, 401)
                self.assertEqual(self.client.post('/api/life-events', headers=headers).status_code, 401)
        self.assertEqual(self.client.post('/api/life-events', headers=self.auth()).json()['owner'], 'user-alice')

    def test_production_dev_flag_fails_closed_and_development_requires_opt_in(self):
        with patch.dict(os.environ, {'ALLOW_DEV_IDENTITY': '1'}):
            self.assertEqual(self.client.get('/private', headers=self.auth()).status_code, 401)
            self.assertEqual(self.client.get('/ready').status_code, 503)
            with patch.dict(os.environ, {'ENV': 'test'}):
                self.assertEqual(self.client.get('/private', headers={'X-Dev-User-Id': 'alice'}).json()['owner'], 'dev:alice')

    def test_expiry_duplicate_registry_and_permissions_fail_closed(self):
        cases = [[{**self.entry, 'expires_at': '2020-01-01T00:00:00+00:00'}],
                 [self.entry, {**self.entry, 'owner_id': 'other-user'}],
                 [{**self.entry, 'expires_at': '2099-01-01T00:00:00'}]]
        for entries in cases:
            self.write_registry(entries)
            self.assertEqual(self.client.get('/private', headers=self.auth()).status_code, 401)
        self.write_registry([self.entry])
        self.registry.chmod(0o666)
        self.assertEqual(self.client.get('/private', headers=self.auth()).status_code, 401)

    def test_readiness_is_separate_from_liveness(self):
        self.assertEqual(self.client.get('/ready').status_code, 200)
        with patch.dict(os.environ, {'STRIPE_SECRET_KEY': '', 'DATA_DIR': '/missing/path'}):
            self.assertEqual(self.client.get('/ready').status_code, 503)
            self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertEqual(self.client.get('/health/private').status_code, 401)
        self.assertEqual(self.client.get('/billing/return').status_code, 200)
        self.assertEqual(self.client.post('/billing/return').status_code, 401)

    def test_public_config_does_not_expose_secrets(self):
        response = self.client.get('/ready')
        self.assertNotIn(self.secret, response.text)
        self.assertNotIn(str(self.registry), response.text)
        self.assertNotIn('sk_test', response.text)

    def test_request_owners_are_isolated(self):
        second = 'qk_' + 'second-test-key-' * 4
        self.write_registry([self.entry, {'sha256': hashlib.sha256(second.encode()).hexdigest(), 'owner_id': 'bob'}])
        for secret, owner in [(self.secret, 'user-alice'), (second, 'bob'), (self.secret, 'user-alice')]:
            self.assertEqual(self.client.get('/private', headers={'Authorization': 'Bearer ' + secret}).json()['owner'], owner)
        self.assertIsNone(identity.current_owner())

    def test_concurrent_requests_keep_distinct_owner_context(self):
        second = 'qk_' + 'concurrent-second-test-key-' * 3
        self.write_registry([self.entry, {'sha256': hashlib.sha256(second.encode()).hexdigest(), 'owner_id': 'bob'}])
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
                async def get(secret, owner):
                    response = await client.get('/private', headers={'Authorization': 'Bearer ' + secret})
                    self.assertEqual(response.json()['owner'], owner)
                await asyncio.gather(*(get(secret, owner) for _ in range(10)
                                     for secret, owner in [(self.secret, 'user-alice'), (second, 'bob')]))
        asyncio.run(exercise())
        self.assertIsNone(identity.current_owner())

    def test_provision_and_revoke_never_print_secret(self):
        output = Path(self.temp.name) / 'delivered-key'
        base = [sys.executable, str(ROOT / 'backend/deploy/provision-key.py'), '--registry', str(self.registry)]
        result = subprocess.run(base + ['create', '--owner', 'bob', '--output', str(output)], text=True, capture_output=True, check=True)
        secret = output.read_text().strip()
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(secret, self.registry.read_text())
        self.assertEqual(self.client.get('/private', headers={'Authorization': 'Bearer ' + secret}).json()['owner'], 'bob')
        key_id = json.loads(self.registry.read_text())['keys'][-1]['key_id']
        subprocess.run(base + ['revoke', '--key-id', key_id], capture_output=True, check=True)
        self.assertEqual(self.client.get('/private', headers={'Authorization': 'Bearer ' + secret}).status_code, 401)

    def test_security_copies_stay_identical(self):
        for name in ('identity.py', 'limits.py'):
            expected = (ROOT / 'backend/deploy/shared' / name).read_bytes()
            for path in (ROOT / 'backend').glob('*/' + name):
                self.assertEqual(path.read_bytes(), expected, str(path))
            for path in (ROOT / 'backend').glob('*/src/' + name):
                self.assertEqual(path.read_bytes(), expected, str(path))


class LimitTests(unittest.TestCase):
    def test_rotating_fake_bearer_keys_does_not_bypass_client_rate_limit(self):
        app = FastAPI()
        @app.get('/private')
        def private():
            return {'ok': True}
        app.add_middleware(limits.RateLimitMiddleware, per_minute=2)
        client = TestClient(app)
        for value in ('one', 'two'):
            self.assertEqual(client.get('/private', headers={'Authorization': 'Bearer ' + value}).status_code, 200)
        response = client.get('/private', headers={'Authorization': 'Bearer three'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('retry-after', response.headers)

    def test_chunked_and_understated_body_rejected_before_app_runs(self):
        async def run(headers):
            invoked = False
            output = []
            messages = iter([{'type': 'http.request', 'body': b'123456', 'more_body': True},
                             {'type': 'http.request', 'body': b'789012', 'more_body': False}])
            async def receive():
                return next(messages)
            async def send(message):
                output.append(message)
            async def app(scope, receive, send):
                nonlocal invoked
                invoked = True
            await limits.BodySizeLimitMiddleware(app, max_bytes=10)({'type': 'http', 'headers': headers}, receive, send)
            self.assertFalse(invoked)
            self.assertEqual(output[0]['status'], 413)
        asyncio.run(run([]))
        asyncio.run(run([(b'content-length', b'2')]))

    def test_body_is_replayed_without_loss(self):
        app = FastAPI()
        @app.post('/echo')
        async def echo(request: Request):
            return {'body': (await request.body()).decode()}
        app.add_middleware(limits.BodySizeLimitMiddleware, max_bytes=10)
        response = TestClient(app).post('/echo', content=b'1234567890')
        self.assertEqual(response.json()['body'], '1234567890')
        self.assertEqual(TestClient(app).post('/echo', content=b'12345678901').status_code, 413)


if __name__ == '__main__':
    unittest.main()
